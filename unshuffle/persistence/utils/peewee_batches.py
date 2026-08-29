import sqlite3
from collections.abc import Iterator
from typing import Any, TypeVar, cast


_Row = TypeVar("_Row")

PEEWEE_INSERT_MAX_ROWS = 500
PEEWEE_INSERT_VARIABLE_RESERVE = 32


def peewee_insert_batches(
    connection: sqlite3.Connection,
    model: Any,
    rows: list[_Row],
) -> Iterator[list[_Row]]:
    """Yield insert batches that fit the active SQLite variable limit."""
    if not rows:
        return
    variable_limit = 999
    getlimit = getattr(connection, "getlimit", None)
    if callable(getlimit):
        try:
            variable_limit = int(cast(Any, getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)))
        except (TypeError, ValueError):
            pass
    model_field_count = len(getattr(model._meta, "sorted_fields", ()))
    variables_per_row = max(1, len(cast(Any, rows[0])), model_field_count)
    available_variables = max(1, variable_limit - PEEWEE_INSERT_VARIABLE_RESERVE)
    batch_size = max(
        1,
        min(PEEWEE_INSERT_MAX_ROWS, available_variables // variables_per_row),
    )
    for start in range(0, len(rows), batch_size):
        yield rows[start:start + batch_size]
