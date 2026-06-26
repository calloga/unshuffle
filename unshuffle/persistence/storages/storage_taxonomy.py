from typing import Dict, List, Tuple

from unshuffle.persistence.stores import taxonomy_store


def reset_adjustments(db) -> None:
    with db._write_transaction():
        db.conn.execute("DELETE FROM token_adjustments")


def seed_aliases_bulk(db, alias_list: List[tuple]) -> None:
    if not alias_list:
        return
    with db._write_transaction():
        taxonomy_store.seed_aliases_bulk(db.conn, alias_list)


def get_aliases(db) -> Dict[str, Tuple[str, float]]:
    return taxonomy_store.get_aliases(db.conn)


def get_aliases_with_source(db) -> Dict[str, Tuple[str, float, str]]:
    return taxonomy_store.get_aliases_with_source(db.conn)


def get_aliases_by_source(db, source: str) -> Dict[str, Tuple[str, float]]:
    return taxonomy_store.get_aliases_by_source(db.conn, source)


def seed_config_list(db, list_type: str, values: List[str], clear: bool = False) -> None:
    if clear:
        with db._write_transaction():
            db.conn.execute("DELETE FROM config_lists WHERE list_type = ?", (list_type,))
    if not values:
        return
    with db._write_transaction():
        taxonomy_store.seed_config_list(db.conn, list_type, values)


def seed_suppression_rules(db, rules: Dict[str, List[str]]) -> None:
    if not rules:
        return
    with db._write_transaction():
        taxonomy_store.seed_suppression_rules(db.conn, rules)


def seed_sub_taxonomy(db, mapping: Dict[str, Dict[str, str]]) -> None:
    if not mapping:
        return
    rows = taxonomy_store.sub_taxonomy_rows(mapping)
    if not rows:
        return
    with db._write_transaction():
        taxonomy_store.seed_sub_taxonomy(db.conn, rows)


def get_config_list(db, list_type: str) -> List[str]:
    return taxonomy_store.get_config_list(db.conn, list_type)


def get_suppression_rules(db) -> Dict[str, List[str]]:
    return taxonomy_store.get_suppression_rules(db.conn)


def get_sub_taxonomy(db) -> Dict[str, Dict[str, str]]:
    return taxonomy_store.get_sub_taxonomy(db.conn)


def add_exclusion(db, path: str) -> None:
    with db._write_transaction():
        db.conn.execute("INSERT OR IGNORE INTO exclusions (path) VALUES (?)", (path,))


def get_exclusions(db) -> List[str]:
    cursor = db.conn.execute("SELECT path FROM exclusions")
    return [row[0] for row in cursor.fetchall()]


def is_excluded(db, path: str) -> bool:
    cursor = db.conn.execute("SELECT 1 FROM exclusions WHERE path = ?", (path,))
    return cursor.fetchone() is not None
