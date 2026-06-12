from pathlib import Path

from unshuffle.core.paths import DB_FILE_NAME, SYSTEM_FOLDER_NAME
from unshuffle.persistence import get_db, get_local_db

_SESSION_CACHE: dict[tuple[str, str, int], list[dict]] = {}
_STAGING_CACHE: dict[tuple[str, str], list[dict]] = {}
_SOURCES_CACHE: dict[tuple[str, str], list[str]] = {}
_SESSION_DETAIL_CACHE: dict[tuple[str, str], dict | None] = {}


def invalidate_history_cache(target: str | None = None, session_id: str | None = None) -> None:
    target_key = (target or "").strip().lower()
    session_key = (session_id or "").strip()

    def _keep_target_key(parts: tuple) -> bool:
        key_target = str(parts[0]).strip().lower() if parts else ""
        if target_key and key_target != target_key:
            return True
        return False

    def _keep_session_key(parts: tuple) -> bool:
        key_target = str(parts[0]).strip().lower() if parts else ""
        if target_key and key_target != target_key:
            return True
        if session_key and len(parts) > 1 and str(parts[1]) != session_key:
            return True
        return False

    if not target_key and not session_key:
        _SESSION_CACHE.clear()
        _STAGING_CACHE.clear()
        _SOURCES_CACHE.clear()
        _SESSION_DETAIL_CACHE.clear()
        return

    stale = [key for key in _SESSION_CACHE if not _keep_target_key(key)]
    for key in stale:
        _SESSION_CACHE.pop(key, None)

    stale = [key for key in _STAGING_CACHE if not _keep_session_key(key)]
    for key in stale:
        _STAGING_CACHE.pop(key, None)

    stale = [key for key in _SOURCES_CACHE if not _keep_session_key(key)]
    for key in stale:
        _SOURCES_CACHE.pop(key, None)

    stale = [key for key in _SESSION_DETAIL_CACHE if not _keep_session_key(key)]
    for key in stale:
        _SESSION_DETAIL_CACHE.pop(key, None)


def _local_db_exists(target: str | Path) -> bool:
    return (Path(target) / SYSTEM_FOLDER_NAME / DB_FILE_NAME).exists()


def _db_candidates(target: str | Path):
    target_path = Path(target)
    if _local_db_exists(target_path):
        yield get_local_db(target_path)
    yield get_db(target_path)


def _history_db_candidates(target: str | Path):
    target_path = Path(target)
    yield "global", get_db(target_path)
    if _local_db_exists(target_path):
        yield "local", get_local_db(target_path)


def _first_nonempty(target: str, loader):
    for db in _db_candidates(target):
        try:
            if hasattr(db, "__enter__"):
                with db as ctx_db:
                    value = loader(ctx_db)
            else:
                value = loader(db)
            if value:
                return value
        finally:
            if not hasattr(db, "__enter__") and hasattr(db, "close"):
                db.close()
    return []


def _all_results(target: str, loader):
    results = []
    for db in _db_candidates(target):
        try:
            if hasattr(db, "__enter__"):
                with db as ctx_db:
                    value = loader(ctx_db)
            else:
                value = loader(db)
            if value:
                results.extend(value)
        finally:
            if not hasattr(db, "__enter__") and hasattr(db, "close"):
                db.close()
    return results


def _all_history_results(target: str, loader):
    results = []
    for scope, db in _history_db_candidates(target):
        try:
            if hasattr(db, "__enter__"):
                with db as ctx_db:
                    value = loader(ctx_db)
            else:
                value = loader(db)
            if value:
                results.extend((scope, row) for row in value)
        finally:
            if not hasattr(db, "__enter__") and hasattr(db, "close"):
                db.close()
    return results


def _session_timestamp_key(session: dict) -> str:
    return str(session.get("timestamp") or "")


def _dedupe_history_sessions_by_id(rows: list[tuple[str, dict]]) -> list[dict]:
    by_id: dict[str, tuple[str, dict]] = {}
    anonymous = []
    for scope, session in rows:
        session_id = str(session.get("session_id") or "")
        if not session_id:
            anonymous.append(session)
            continue
        current = by_id.get(session_id)
        if current is None:
            by_id[session_id] = (scope, session)
            continue
        current_scope, current_session = current
        if scope == "global" and current_scope != "global":
            by_id[session_id] = (scope, session)
            continue
        if scope == current_scope and _session_timestamp_key(session) >= _session_timestamp_key(current_session):
            by_id[session_id] = (scope, session)
    return sorted([session for _scope, session in by_id.values()] + anonymous, key=_session_timestamp_key, reverse=True)


def _dedupe_sessions_by_id(sessions: list[dict]) -> list[dict]:
    return _dedupe_history_sessions_by_id([("unknown", session) for session in sessions])


def load_executed_sessions(target: str, limit: int = 10) -> list[dict]:
    if not target:
        return []
    cache_key = (target, "executed", limit)
    cached = _SESSION_CACHE.get(cache_key)
    if cached is not None:
        return list(cached)
    def _load(db):
        try:
            return db.get_recent_sessions(limit=limit, only_executed=True, target_root=Path(target))
        except TypeError:
            return db.get_recent_sessions(limit=limit, only_executed=True)

    sessions = _dedupe_history_sessions_by_id(_all_history_results(target, _load))[:limit]
    _SESSION_CACHE[cache_key] = list(sessions)
    return list(sessions)


def load_latest_history_target(limit: int = 100) -> str:
    try:
        with get_db(Path(".")) as db:
            sessions = db.get_recent_sessions(limit=limit, only_executed=True)
    except Exception:
        return ""
    for session in sessions:
        target_root = str(session.get("target_root") or "").strip()
        if target_root:
            return target_root
    return ""


def resolve_history_target(settings) -> str:
    history_target = str(settings.value("last_history_target", "") or "").strip()
    if history_target:
        return history_target

    active_target = str(settings.value("last_target", "") or "").strip()
    try:
        if active_target and load_executed_sessions(active_target, limit=1):
            return active_target
    except Exception:
        return active_target

    latest_target = load_latest_history_target()
    if latest_target:
        try:
            settings.setValue("last_history_target", latest_target)
        except Exception:
            pass
        return latest_target
    return active_target


def load_pending_scan_sessions(target: str, limit: int = 10) -> list[dict]:
    if not target:
        return []
    cache_key = (target, "pending", limit)
    cached = _SESSION_CACHE.get(cache_key)
    if cached is not None:
        return list(cached)
    sessions = _first_nonempty(
        target,
        lambda db: db.get_recent_sessions(limit=limit, only_executed=False),
    )
    pending = [s for s in sessions if s.get("file_count", 0) == 0]
    _SESSION_CACHE[cache_key] = list(pending)
    return list(pending)


def load_staging_records(target: str, session_id: str) -> list[dict]:
    if not target or not session_id:
        return []
    cache_key = (target, session_id)
    cached = _STAGING_CACHE.get(cache_key)
    if cached is not None:
        return list(cached)
    records = _first_nonempty(target, lambda db: db.get_staging_records(session_id))
    _STAGING_CACHE[cache_key] = list(records)
    return list(records)


def load_session(target: str, session_id: str) -> dict | None:
    if not target or not session_id:
        return None
    cache_key = (target, session_id)
    if cache_key in _SESSION_DETAIL_CACHE:
        return _SESSION_DETAIL_CACHE[cache_key]
    session = None
    for db in _db_candidates(target):
        try:
            if hasattr(db, "__enter__"):
                with db as ctx_db:
                    session = ctx_db.get_session(session_id)
            else:
                session = db.get_session(session_id)
            if session:
                break
        finally:
            if not hasattr(db, "__enter__") and hasattr(db, "close"):
                db.close()
    _SESSION_DETAIL_CACHE[cache_key] = session
    return session


def session_has_execution_records(target: str, session_id: str) -> bool:
    if not target or not session_id:
        return False
    return bool(_first_nonempty(target, lambda db: db.get_session_records(session_id)))


def load_session_sources(target: str, session_id: str) -> list[str]:
    if not target or not session_id:
        return []
    cache_key = (target, session_id)
    cached = _SOURCES_CACHE.get(cache_key)
    if cached is not None:
        return list(cached)
    sources = [str(source) for source in _first_nonempty(target, lambda db: db.get_session_sources(session_id))]
    _SOURCES_CACHE[cache_key] = list(sources)
    return list(sources)


def reset_learning_weights(target: str) -> None:
    if not target:
        return
    with get_db(Path(target)) as db:
        db.reset_adjustments()


def clear_migration_history(target: str) -> None:
    if not target:
        return
    target_path = Path(target)
    databases = [get_db(target_path)]
    if _local_db_exists(target_path):
        databases.append(get_local_db(target_path))
    for db in databases:
        with db:
            clear_target = getattr(db, "clear_history_for_target", None)
            if callable(clear_target):
                clear_target(target_path)
            else:
                db.clear_all_history()
    invalidate_history_cache(target)
