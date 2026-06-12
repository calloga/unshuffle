from collections import Counter
from pathlib import Path

from ..core.hashing import get_file_hash
from ..core.path_safety import is_path_within_directory


def effective_undo_target_root(session_target: Path, records: list) -> Path:
    if not records:
        return session_target

    target_paths = []
    for record in records:
        try:
            target_paths.append(Path(record["target_path"]).resolve())
        except (KeyError, OSError):
            return session_target

    if all(is_path_within_directory(path, session_target) for path in target_paths):
        return session_target

    category_roots = {"oneshots", "loops", "non-audio assets", "utility"}
    if session_target.name.casefold() not in category_roots:
        return session_target

    parent = session_target.parent
    if parent == session_target:
        return session_target
    if all(is_path_within_directory(path, parent) for path in target_paths):
        return parent
    return session_target


def undoable_records(records: list) -> list:
    undoable = []
    for record in records:
        status = str(record.get("status") or "")
        step_status = record.get("step_status")
        if status not in {"copied", "duplicate"}:
            continue
        if step_status is not None and str(step_status) != "COMMITTED":
            continue
        undoable.append(record)
    return undoable


def validate_undo_records(
    *,
    session_db,
    session_id: str,
    session,
    records: list,
    mode: str,
    target_dir: Path,
    allow_preserved: bool = False,
) -> str | None:
    if mode not in {"copy", "move"}:
        return f"Unsafe undo mode: {mode}"

    source_roots = undo_source_roots(session_db, session_id, session)
    allowed_statuses = {"copied", "duplicate"}

    for record in records:
        status = str(record.get("status") or "")
        if status not in allowed_statuses:
            return f"Unsafe undo status: {status or '<missing>'}"

        record_action = undo_record_action(record, mode)
        if record_action not in {"copy", "move"}:
            return f"Unsafe undo action: {record_action or '<missing>'}"
        if record_action != mode and not (mode == "move" and record_action == "copy"):
            return f"Undo action mismatch: {record_action} record in {mode} session"

        step_status = record.get("step_status")
        if step_status is not None and str(step_status) != "COMMITTED":
            return f"Unsafe undo step status: {step_status}"

        target_path = Path(record["target_path"])
        if not is_path_within_directory(target_path, target_dir):
            return f"Unsafe undo target outside session target: {target_path}"
        is_preserved = bool(record.get("is_preserved"))
        if target_path.exists() and target_path.is_dir():
            if is_preserved and not allow_preserved:
                return f"Preserved folder undo requires manifest support: {target_path}"
            if not is_preserved:
                return f"Unsafe undo target is a directory: {target_path}"

        source_path = Path(record["source_path"])
        if source_roots and not any(is_path_within_directory(source_path, root) for root in source_roots):
            return f"Unsafe undo source outside session sources: {source_path}"

        if status == "duplicate":
            if record_action == "move" and source_path.exists():
                return f"Undo source already exists: {source_path}"
            continue

        if is_preserved:
            if record_action == "move" and source_path.exists():
                return f"Undo source already exists: {source_path}"
            if not target_path.exists():
                return f"Undo target missing: {target_path}"
            if target_path.exists() and not target_path.is_dir():
                return f"Unsafe preserved undo target is not a directory: {target_path}"
            if not allow_preserved:
                return f"Preserved folder undo requires manifest support: {target_path}"
            continue

        if target_path.exists() and target_path.is_dir():
            return f"Preserved folder undo requires manifest support: {target_path}"

        expected_hash = undo_expected_hash(record)
        if not expected_hash:
            return f"Cannot verify undo target hash for legacy record: {target_path.name}"
        if not target_path.exists():
            if record_action == "copy":
                continue
            return f"Undo target missing: {target_path}"
        actual_hash = get_file_hash(target_path)
        if not actual_hash or actual_hash != expected_hash:
            return f"Undo target hash mismatch: {target_path.name}"
        if record_action == "move" and source_path.exists():
            return f"Undo source already exists: {source_path}"

    if mode == "move":
        from ..persistence import get_trash_dir

        trash_dir = get_trash_dir(target_dir, session_id)
        for record in records:
            if record.get("status") != "duplicate":
                continue
            if undo_record_action(record, mode) != "move":
                continue
            trash_path = undo_duplicate_trash_path(record, trash_dir)
            if not is_path_within_directory(trash_path, trash_dir):
                return f"Unsafe duplicate trash path outside session trash: {trash_path}"
            if record.get("trash_path") and not trash_path.exists():
                return f"Duplicate trash path missing: {trash_path}"
            expected_hash = undo_expected_hash(record)
            if expected_hash and trash_path.exists():
                actual_hash = get_file_hash(trash_path)
                if actual_hash and actual_hash != expected_hash:
                    return f"Duplicate trash hash mismatch: {Path(record['source_path']).name}"

        duplicate_names = Counter(
            Path(record["source_path"]).name
            for record in records
            if record.get("status") == "duplicate"
            and undo_record_action(record, mode) == "move"
            and not record.get("trash_path")
        )
        ambiguous = [name for name, count in duplicate_names.items() if count > 1]
        if ambiguous:
            return f"Ambiguous duplicate trash restore: {ambiguous[0]}"

    return None


def preserved_undo_confirmation(session_id: str, records: list, mode: str, target_dir: Path) -> dict | None:
    items = []
    for record in records:
        if not bool(record.get("is_preserved")):
            continue
        record_action = undo_record_action(record, mode)
        source_path = Path(record["source_path"])
        target_path = Path(record["target_path"])
        if not is_path_within_directory(target_path, target_dir):
            return None
        action = "restore_to_source" if record_action == "move" else "delete_from_target"
        items.append(
            {
                "source_path": str(source_path),
                "target_path": str(target_path),
                "action": action,
                "record_action": record_action,
            }
        )
    if not items:
        return None
    return {
        "requires_preserved_confirmation": True,
        "session_id": session_id,
        "mode": mode,
        "items": items,
    }


def undo_record_action(record: dict, session_mode: str) -> str:
    return str(record.get("original_action") or session_mode)


def undo_expected_hash(record: dict) -> str | None:
    expected_hash = record.get("file_hash") or record.get("hash")
    if not expected_hash:
        return None
    expected_hash = str(expected_hash)
    if expected_hash == "PRESERVED_SESS_SKIPPED":
        return None
    return expected_hash


def undo_duplicate_trash_path(record: dict, trash_dir: Path) -> Path:
    trash_path = record.get("trash_path")
    if trash_path:
        return Path(trash_path)
    return trash_dir / Path(record["source_path"]).name


def undo_source_roots(session_db, session_id: str, session) -> list[Path]:
    roots: list[Path] = []
    get_sources = getattr(session_db, "get_session_sources", None)
    if callable(get_sources):
        try:
            source_values = get_sources(session_id)
            if isinstance(source_values, (list, tuple, set)):
                roots.extend(Path(path) for path in source_values if str(path))
        except Exception:
            roots = []
    if not roots and session and session.get("source_path"):
        roots.append(Path(session["source_path"]))
    resolved_roots: list[Path] = []
    for root in roots:
        try:
            resolved_roots.append(root.resolve())
        except OSError:
            resolved_roots.append(root)
    return resolved_roots
