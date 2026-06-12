import collections
import sqlite3


def seed_aliases_bulk(conn: sqlite3.Connection, alias_list: list[tuple]) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO aliases (alias, category, weight, source)
        VALUES (?, ?, ?, ?)
        """,
        alias_list,
    )


def get_aliases(conn: sqlite3.Connection) -> dict[str, tuple[str, float]]:
    cursor = conn.execute("SELECT alias, category, weight FROM aliases")
    return {row["alias"]: (row["category"], row["weight"]) for row in cursor.fetchall()}


def get_aliases_with_source(conn: sqlite3.Connection) -> dict[str, tuple[str, float, str]]:
    cursor = conn.execute("SELECT alias, category, weight, source FROM aliases")
    return {row["alias"]: (row["category"], row["weight"], row["source"]) for row in cursor.fetchall()}


def get_aliases_by_source(conn: sqlite3.Connection, source: str) -> dict[str, tuple[str, float]]:
    cursor = conn.execute(
        "SELECT alias, category, weight FROM aliases WHERE source = ? ORDER BY alias ASC",
        (source,),
    )
    return {row["alias"]: (row["category"], row["weight"]) for row in cursor.fetchall()}


def seed_config_list(conn: sqlite3.Connection, list_type: str, values: list[str]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO config_lists (list_type, value) VALUES (?, ?)",
        [(list_type, value) for value in values],
    )


def seed_suppression_rules(conn: sqlite3.Connection, rules: dict[str, list[str]]) -> None:
    data = [(suppressor, target) for suppressor, targets in rules.items() for target in targets]
    conn.executemany(
        "INSERT OR REPLACE INTO suppression_rules (suppressor, target) VALUES (?, ?)",
        data,
    )


def sub_taxonomy_rows(mapping: dict[str, dict[str, str]]) -> list[tuple[str, str, str]]:
    rows = []
    for category, token_map in mapping.items():
        if not isinstance(token_map, dict):
            continue
        for token, sub_category in token_map.items():
            rows.append((category, token, sub_category))
    return rows


def seed_sub_taxonomy(conn: sqlite3.Connection, rows: list[tuple[str, str, str]]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO sub_taxonomy (category, token, sub_category) VALUES (?, ?, ?)",
        rows,
    )


def get_config_list(conn: sqlite3.Connection, list_type: str) -> list[str]:
    cursor = conn.execute(
        "SELECT value FROM config_lists WHERE list_type = ? ORDER BY rowid ASC",
        (list_type,),
    )
    return [row["value"] for row in cursor.fetchall()]


def get_suppression_rules(conn: sqlite3.Connection) -> dict[str, list[str]]:
    cursor = conn.execute("SELECT * FROM suppression_rules")
    result = collections.defaultdict(list)
    for row in cursor.fetchall():
        result[row["suppressor"]].append(row["target"])
    return dict(result)


def get_sub_taxonomy(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    cursor = conn.execute("SELECT * FROM sub_taxonomy")
    result = collections.defaultdict(dict)
    for row in cursor.fetchall():
        result[row["category"]][row["token"]] = row["sub_category"]
    return dict(result)
