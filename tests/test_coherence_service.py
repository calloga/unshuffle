from __future__ import annotations

import struct
from pathlib import Path
from unittest import mock

from gui.utils.state import iter_staging_rows
from unshuffle.core import PlanRecord
from unshuffle.core.features import CURRENT_FEATURE_VECTOR_SIZE
from unshuffle.logic.coherence.service import run_coherence_audit
from unshuffle.persistence import UnshuffleDB


class _LegacyCoherenceDb:
    _GROUPED_METHODS = {
        "iter_coherence_group_keys",
        "coherence_group_records",
        "clear_generated_coherence_audit",
        "append_coherence_group",
    }

    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        if name in self._GROUPED_METHODS:
            raise AttributeError(name)
        return getattr(self._db, name)


def _records() -> list[PlanRecord]:
    records = []
    for index in range(20):
        category = "Kicks" if index < 10 else "Snares"
        base = 0.1 if category == "Kicks" else 0.8
        values = [base + ((index % 10) * 0.001) + (feature * 0.0001) for feature in range(CURRENT_FEATURE_VECTOR_SIZE)]
        vector = struct.pack("<" + ("f" * len(values)), *values)
        records.append(
            PlanRecord(
                source_path=Path(f"D:/Samples/{category}/{index}.wav"),
                pack=category,
                category=category,
                subcategory=None,
                audio_type="Oneshots",
                confidence="0.8",
                duration=0.5,
                feature_vector=vector,
                staging_row_id=index,
            )
        )
    return records


def _stable_result_rows(db, session_id: str):
    return [
        {
            key: value
            for key, value in row.items()
            if key not in {"created_at", "updated_at", "id", "session_id"}
        }
        for row in db.list_coherence_results(session_id)
    ]


def test_grouped_coherence_matches_materialized_audit(tmp_path):
    db = UnshuffleDB(tmp_path / "coherence.db")
    try:
        records = _records()
        for session_id in ("grouped", "legacy"):
            db.register_session(session_id, Path("D:/Samples"), Path("D:/Target"), "pending")
            db.add_staging_records_iter(session_id, iter_staging_rows(records))

        with mock.patch(
            "unshuffle.logic.coherence.service.GROUPED_COHERENCE_MIN_RECORDS",
            0,
        ):
            grouped = run_coherence_audit(db, "grouped", force=True)
        legacy = run_coherence_audit(_LegacyCoherenceDb(db), "legacy", force=True)

        assert grouped == legacy
        assert _stable_result_rows(db, "grouped") == _stable_result_rows(db, "legacy")
        assert db.list_refinement_candidates("grouped") == db.list_refinement_candidates("legacy")
        grouped_anchors = {row["anchor_id"] for row in db.list_anchor_candidates("grouped", state="candidate")}
        legacy_anchors = {row["anchor_id"] for row in db.list_anchor_candidates("legacy", state="candidate")}
        assert grouped_anchors == legacy_anchors
    finally:
        db.close()
