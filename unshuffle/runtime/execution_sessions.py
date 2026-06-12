"""Session registration helpers for runtime execution."""

from pathlib import Path
from typing import Any, Iterable, Sequence


def execution_session_sources(
    plan: Sequence[Any],
    session_source_root: Path | None,
    session_source_roots: Iterable[Path],
    target_dir: Path,
) -> tuple[Path, list[Path]]:
    effective_sources = list(session_source_roots)
    if not effective_sources:
        seen_sources = set()
        for record in plan:
            source_root = record.source_path.parent.resolve()
            source_key = str(source_root)
            if source_key in seen_sources:
                continue
            seen_sources.add(source_key)
            effective_sources.append(source_root)
    effective_primary_source = session_source_root or (
        effective_sources[0] if effective_sources else (plan[0].source_path.parent if plan else target_dir)
    )
    return effective_primary_source, effective_sources


def register_execution_session(
    databases: Iterable[Any],
    *,
    session_id: str,
    source: Path,
    sources: Sequence[Path],
    target: Path,
    move: bool,
    flat: bool,
) -> None:
    for database in databases:
        database.register_session(
            session_id=session_id,
            source=source,
            target=target,
            mode="move" if move else "copy",
            is_flat=flat,
        )
        database.set_session_sources(session_id, sources)
