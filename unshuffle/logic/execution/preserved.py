import logging
from pathlib import Path

from ...core.path_safety import is_path_within_directory


def path_under_session_source_root(owner, path: Path) -> bool:
    source_roots = [Path(root).resolve() for root in getattr(owner, "session_source_roots", [])]
    if not source_roots:
        return True
    return any(is_path_within_directory(path, root) for root in source_roots)


def resolve_preserved_destination_root(owner, preserved_root: Path) -> Path:
    source_roots = sorted(
        [Path(root).resolve() for root in getattr(owner, "session_source_roots", [])],
        key=lambda path: len(path.parts),
        reverse=True,
    )
    preserved_root = Path(preserved_root).resolve()

    for source_root in source_roots:
        try:
            rel_parent = preserved_root.parent.relative_to(source_root)
        except ValueError:
            continue

        if rel_parent == Path("."):
            return owner.target_dir / preserved_root.name

        candidate_parent = owner.target_dir / rel_parent
        if candidate_parent.exists() and candidate_parent.is_dir():
            return candidate_parent / preserved_root.name
        return owner.target_dir / preserved_root.name

    return owner.target_dir / preserved_root.name


def process_preserved_record(owner, record, *, move: bool, dry_run: bool) -> tuple[str, Path, Path]:
    preserved_root_path = Path(record.preserved_root) if record.preserved_root else record.source_path
    source_roots = [Path(root).resolve() for root in getattr(owner, "session_source_roots", [])]
    if source_roots and not any(is_path_within_directory(preserved_root_path, root) for root in source_roots):
        owner.log(
            f"  ! PRESERVED ROOT ERROR: {preserved_root_path} is outside the session source roots",
            level=logging.ERROR,
        )
        return "error", preserved_root_path, preserved_root_path.parent

    try:
        rel_parts = record.source_path.relative_to(preserved_root_path).parts
    except ValueError:
        owner.log(
            f"  ! PRESERVED ROOT ERROR: {record.source_path} is not under {preserved_root_path}",
            level=logging.ERROR,
        )
        return "error", record.source_path, record.source_path.parent

    dest_root = owner._resolve_preserved_destination_root(preserved_root_path)
    dest_path = dest_root.joinpath(*rel_parts)
    dest_folder = dest_path.parent
    if not is_path_within_directory(dest_root, owner.target_dir) or not is_path_within_directory(dest_path, owner.target_dir):
        owner.log(f"  ! Refusing preserved destination outside target: {dest_path}", level=logging.ERROR)
        return "error", dest_path, dest_folder

    if preserved_root_path not in owner.moved_preserved_roots:
        owner.log(f"  > Bulk Handling Preserved Folder: {preserved_root_path.name}")
        if not dry_run:
            if owner._execute_folder_transfer(preserved_root_path, dest_root, move):
                owner.moved_preserved_roots.add(preserved_root_path)
            else:
                return "error", dest_path, dest_folder
        else:
            owner.log(f"  * Result: DRY RUN (Would {'move' if move else 'copy'} folder {preserved_root_path.name})")
            owner.moved_preserved_roots.add(preserved_root_path)

    owner._last_record_hash = "PRESERVED_SESS_SKIPPED"
    return "copied", dest_path, dest_folder

