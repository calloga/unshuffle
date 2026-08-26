from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from unshuffle.logic.coherence.models import CoherenceResult, RefinementCandidate
from unshuffle.persistence import UnshuffleDB
from unshuffle.persistence.db import unshuffle_db as db_module
from unshuffle.persistence.stores.cache_store import PeeweeCacheStore, SqliteCacheStore
from unshuffle.persistence.stores.coherence_store import PeeweeCoherenceStore, SqliteCoherenceStore
from unshuffle.persistence.stores.maintenance_store import PeeweeMaintenanceStore, SqliteMaintenanceStore


def _store_config(driver: str) -> dict[str, dict[str, str]]:
    return {
        "STORE_MIGRATION": {
            "cache": driver,
            "coherence": driver,
            "maintenance": driver,
        }
    }


def _cache_row(file_hash: str, path: Path) -> tuple[str, Path, int, float]:
    return file_hash, path, 10, 1.0


def test_peewee_clear_cache_executes_delete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db_module, "get_config", lambda: _store_config("peewee"))
    db = UnshuffleDB(tmp_path / "clear-cache.db")
    try:
        db.update_cache_bulk([_cache_row("hash-one", tmp_path / "one.wav")])
        assert set(db.get_all_hashes()) == {"hash-one"}

        db.clear_cache()

        assert db.get_all_hashes() == {}
    finally:
        db.close()


def test_peewee_handles_route_queries_to_their_own_database(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(db_module, "get_config", lambda: _store_config("peewee"))
    first = UnshuffleDB(tmp_path / "first.db")
    second = UnshuffleDB(tmp_path / "second.db")
    try:
        first.update_cache_bulk([_cache_row("hash-first", tmp_path / "first.wav")])
        second.update_cache_bulk([_cache_row("hash-second", tmp_path / "second.wav")])
        first.upsert_coherence_results("session-first", [
            CoherenceResult("record-first", "Bass", "Sub", "stable", 1.0)
        ])
        second.upsert_coherence_results("session-second", [
            CoherenceResult("record-second", "Kicks", "Sub", "stable", 1.0)
        ])
        first.register_session("session-first", tmp_path / "source-one", tmp_path / "target-one", "copy")
        second.register_session("session-second", tmp_path / "source-two", tmp_path / "target-two", "copy")

        assert set(first.get_all_hashes()) == {"hash-first"}
        assert set(second.get_all_hashes()) == {"hash-second"}
        assert first.list_coherence_results("session-first")[0]["record_id"] == "record-first"
        assert second.list_coherence_results("session-second")[0]["record_id"] == "record-second"
        assert first._maintenance_store._all_session_ids() == {"session-first"}
        assert second._maintenance_store._all_session_ids() == {"session-second"}
    finally:
        first.close()
        second.close()


def test_closing_newer_peewee_handle_does_not_break_older_handle(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(db_module, "get_config", lambda: _store_config("peewee"))
    database_path = tmp_path / "shared.db"
    first = UnshuffleDB(database_path)
    second = None
    try:
        first.update_cache_bulk([_cache_row("hash-first", tmp_path / "first.wav")])
        second = UnshuffleDB(database_path)
        second.close()

        assert set(first.get_all_hashes()) == {"hash-first"}
    finally:
        first.close()
        if second is not None:
            second.close()


def test_concurrent_peewee_handles_remain_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db_module, "get_config", lambda: _store_config("peewee"))
    first = UnshuffleDB(tmp_path / "concurrent-first.db")
    second = UnshuffleDB(tmp_path / "concurrent-second.db")
    try:
        first.update_cache_bulk([_cache_row("hash-first", tmp_path / "first.wav")])
        second.update_cache_bulk([_cache_row("hash-second", tmp_path / "second.wav")])

        def repeatedly_read(db: UnshuffleDB, expected: str) -> None:
            for _ in range(10):
                assert set(db.get_all_hashes()) == {expected}

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(repeatedly_read, first, "hash-first"),
                executor.submit(repeatedly_read, second, "hash-second"),
            ]
            for future in futures:
                future.result()
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("configured", [{}, {"STORE_MIGRATION": "invalid"}, {
    "STORE_MIGRATION": {"cache": "unknown", "coherence": None}
}])
def test_missing_or_invalid_migration_config_safely_uses_sqlite(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        configured,
):
    monkeypatch.setattr(db_module, "get_config", lambda: configured)
    db = UnshuffleDB(tmp_path / "fallback.db")
    try:
        assert isinstance(db._cache_store, SqliteCacheStore)
        assert isinstance(db._coherence_store, SqliteCoherenceStore)
        assert isinstance(db._maintenance_store, SqliteMaintenanceStore)
    finally:
        db.close()


@pytest.mark.parametrize(
    ("driver", "cache_type", "coherence_type", "maintenance_type"),
    [
        ("sqlite", SqliteCacheStore, SqliteCoherenceStore, SqliteMaintenanceStore),
        ("peewee", PeeweeCacheStore, PeeweeCoherenceStore, PeeweeMaintenanceStore),
    ],
)
def test_store_backends_share_basic_cache_and_coherence_contract(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        driver,
        cache_type,
        coherence_type,
        maintenance_type,
):
    monkeypatch.setattr(db_module, "get_config", lambda: _store_config(driver))
    db = UnshuffleDB(tmp_path / f"contract-{driver}.db")
    try:
        assert isinstance(db._cache_store, cache_type)
        assert isinstance(db._coherence_store, coherence_type)
        assert isinstance(db._maintenance_store, maintenance_type)

        db.update_cache_bulk([_cache_row("hash-one", tmp_path / "one.wav")])
        assert set(db.get_all_hashes()) == {"hash-one"}

        result = CoherenceResult(
            record_id="record-one",
            category="Bass",
            subcategory="Sub",
            coherence_status="low_coherence",
            coherence_score=0.2,
            cluster_id="cluster-one",
            is_outlier=True,
        )
        db.upsert_coherence_results("session-one", [result])

        assert db.list_coherence_results("session-one")[0]["record_id"] == "record-one"
        assert db.list_coherence_result_clusters("session-one") == [
            {"record_id": "record-one", "cluster_id": "cluster-one"}
        ]

        db.clear_generated_coherence_audit("session-one")
        assert db.list_coherence_results("session-one") == []

        candidate = RefinementCandidate(
            candidate_id="candidate-one",
            record_id="record-one",
            current_category="Bass",
            current_subcategory="Sub",
            suggested_category="Kicks",
            suggested_subcategory="Kick",
            evidence="neighbors fit kicks",
            confidence_score=0.8,
        )
        db.append_coherence_group("session-two", [result], [candidate], [])
        assert len(db.list_coherence_results("session-two")) == 1
        assert len(db.list_refinement_candidates("session-two")) == 1
    finally:
        db.close()


@pytest.mark.parametrize("driver", ["sqlite", "peewee"])
def test_coherence_audit_rolls_back_for_each_backend(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        driver: str,
):
    monkeypatch.setattr(db_module, "get_config", lambda: _store_config(driver))
    db = UnshuffleDB(tmp_path / f"rollback-{driver}.db")
    result = CoherenceResult(
        record_id="record-one",
        category="Bass",
        subcategory="Sub",
        coherence_status="low_coherence",
        coherence_score=0.2,
    )
    try:
        def fail(*_args, **_kwargs):
            raise RuntimeError("forced failure")

        monkeypatch.setattr(db._coherence_store, "upsert_refinement_candidates", fail)
        with pytest.raises(RuntimeError, match="forced failure"):
            db.upsert_coherence_audit("session-one", [result], [], [])

        assert db.list_coherence_results("session-one") == []
    finally:
        db.close()
