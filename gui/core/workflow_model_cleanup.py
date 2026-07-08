from __future__ import annotations

from pathlib import Path


def normalized_model_path(model, row: int, record) -> str:
    if hasattr(model, "normalized_source_path"):
        return model.normalized_source_path(row)
    return str(record.source_path.resolve()).replace("\\", "/").lower()


def _refresh_after_db_delete(model) -> None:
    if hasattr(model, "refresh_index"):
        model.refresh_index()
    refresh_model_caches(model)


def _delete_db_path_prefix(model, exclude_path: Path) -> int | None:
    store = getattr(model, "store", None)
    if store is None:
        return None
    prefix = exclude_path.resolve().as_posix().rstrip("/").lower()
    pattern = prefix.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "/%"
    cursor = store.conn.execute(
        """
        DELETE FROM staging_records
        WHERE session_id = ?
          AND (
            LOWER(REPLACE(source_path, '\\', '/')) = ?
            OR LOWER(REPLACE(source_path, '\\', '/')) LIKE ? ESCAPE '!'
          )
        """,
        (store.session_id, prefix, pattern),
    )
    removed_count = int(getattr(cursor, "rowcount", 0) or 0)
    _refresh_after_db_delete(model)
    return removed_count


def _delete_db_paths(model, deleted_paths: list[Path]) -> int | None:
    store = getattr(model, "store", None)
    if store is None:
        return None
    paths = [path.resolve().as_posix().lower() for path in deleted_paths]
    if not paths:
        return 0
    placeholders = ", ".join("?" for _ in paths)
    cursor = store.conn.execute(
        f"""
        DELETE FROM staging_records
        WHERE session_id = ?
          AND LOWER(REPLACE(source_path, '\\', '/')) IN ({placeholders})
        """,
        [store.session_id, *paths],
    )
    removed_count = int(getattr(cursor, "rowcount", 0) or 0)
    _refresh_after_db_delete(model)
    return removed_count


def rebuild_model_after_filter(model, keep_record) -> int:
    if not model or not hasattr(model, "records"):
        return 0
    removed_count = 0
    model.beginResetModel()
    kept = []
    for row, rec in enumerate(model.records):
        if keep_record(row, rec):
            kept.append(rec)
        else:
            removed_count += 1
    model.records = kept
    refresh_model_caches(model)
    model.endResetModel()
    return removed_count


def refresh_model_caches(model) -> None:
    if hasattr(model, "_invalidate_unique_values"):
        model._invalidate_unique_values()
    if hasattr(model, "_rebuild_row_and_color_caches"):
        model._rebuild_row_and_color_caches()
    else:
        if hasattr(model, "_rebuild_path_row_cache"):
            model._rebuild_path_row_cache()
        if hasattr(model, "_precalculate_colors"):
            model._precalculate_colors()


def remove_excluded_prefix(model, exclude_path: Path) -> int:
    db_removed = _delete_db_path_prefix(model, exclude_path)
    if db_removed is not None:
        return db_removed
    prefix = exclude_path.as_posix().lower()
    return rebuild_model_after_filter(
        model,
        lambda row, rec: not (
            (path := normalized_model_path(model, row, rec)) == prefix
            or path.startswith(prefix + "/")
        ),
    )


def remove_deleted_paths(model, deleted_paths: list[Path]) -> int:
    db_removed = _delete_db_paths(model, deleted_paths)
    if db_removed is not None:
        return db_removed
    to_remove = {str(path.resolve()).replace("\\", "/").lower() for path in deleted_paths}
    return rebuild_model_after_filter(
        model,
        lambda row, rec: normalized_model_path(model, row, rec) not in to_remove,
    )

