from typing import Dict, List, Set

from unshuffle.core.constants import TOKEN_ADJUSTMENT_CAP, get_runtime_config_snapshot
from unshuffle.core.tokenizer import tokenize


def _weighted_adjustment_tokens(tokens: Set[str]) -> Set[str]:
    runtime = get_runtime_config_snapshot()
    noise_words = set(runtime.get("noise_words") or set())
    weighted_tokens = set()
    for alias in (runtime.get("alias_table") or {}).keys():
        weighted_tokens.update(token for token in tokenize(str(alias), flatten=False) if token not in noise_words)
    return {str(token).strip().lower() for token in tokens if str(token).strip().lower() in weighted_tokens}


def search_staging(db, session_id: str, query_text: str) -> List[int] | Set[int]:
    from unshuffle.persistence.search import search_staging as search_staging_impl

    return search_staging_impl(db.conn, session_id, query_text, db.search_similar_records)


def search_similar_records(
    db,
    session_id: str,
    target_id: int,
    limit: int = 50,
    candidate_ids: Set[int] | None = None,
) -> List[int]:
    from unshuffle.persistence.search import search_similar_records as search_similar_records_impl

    return search_similar_records_impl(db.conn, session_id, target_id, limit, candidate_ids=candidate_ids)


def update_token_adjustment(db, token: str, category: str, delta: float) -> None:
    token = str(token or "").strip().lower()
    category = str(category or "").strip()
    if not token or not category or token not in _weighted_adjustment_tokens({token}):
        return
    with db._write_transaction():
        db.conn.execute(
            """
            INSERT INTO token_adjustments (token, category, weight_offset)
            VALUES (?, ?, ?)
            ON CONFLICT(token, category) DO UPDATE SET
                weight_offset = MIN(?, MAX(?, weight_offset + excluded.weight_offset))
        """,
            (token, category, delta, TOKEN_ADJUSTMENT_CAP, -TOKEN_ADJUSTMENT_CAP),
        )


def update_token_adjustments_bulk(db, adjustment_list: List[tuple]) -> None:
    if not adjustment_list:
        return
    tokens = {str(token or "").strip().lower() for token, _category, _delta in adjustment_list}
    weighted_tokens = _weighted_adjustment_tokens(tokens)
    rows = [
        (token, category, delta)
        for token, category, delta in (
            (str(token or "").strip().lower(), str(category or "").strip(), delta)
            for token, category, delta in adjustment_list
        )
        if token and category and token in weighted_tokens
    ]
    if not rows:
        return
    with db._write_transaction():
        db.conn.executemany(
            """
            INSERT INTO token_adjustments (token, category, weight_offset)
            VALUES (?, ?, ?)
            ON CONFLICT(token, category) DO UPDATE SET
                weight_offset = MIN(?, MAX(?, weight_offset + excluded.weight_offset))
        """,
            [
                (token, category, delta, TOKEN_ADJUSTMENT_CAP, -TOKEN_ADJUSTMENT_CAP)
                for token, category, delta in rows
            ],
        )


def _normalize_source_key(value) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while "//" in text:
        text = text.replace("//", "/")
    return text.lower()


def update_token_adjustments_from_events(db, event_list: List[tuple]) -> int:
    if not event_list:
        return 0
    normalized_events = []
    tokens = set()
    for event in event_list:
        if len(event) != 6:
            continue
        source_key, token, old_category, new_category, old_delta, new_delta = event
        source_key = _normalize_source_key(source_key)
        token = str(token or "").strip().lower()
        old_category = str(old_category or "").strip()
        new_category = str(new_category or "").strip()
        if not source_key or not token or not old_category or not new_category or old_category == new_category:
            continue
        tokens.add(token)
        normalized_events.append((source_key, token, old_category, new_category, old_delta, new_delta))
    weighted_tokens = _weighted_adjustment_tokens(tokens)
    normalized_events = [event for event in normalized_events if event[1] in weighted_tokens]
    if not normalized_events:
        return 0

    inserted_events = []
    with db._write_transaction():
        for source_key, token, old_category, new_category, old_delta, new_delta in normalized_events:
            cursor = db.conn.execute(
                """
                INSERT OR IGNORE INTO learned_correction_events (
                    source_key, token, old_category, new_category
                )
                VALUES (?, ?, ?, ?)
                """,
                (source_key, token, old_category, new_category),
            )
            if cursor.rowcount:
                inserted_events.append((token, old_category, old_delta))
                inserted_events.append((token, new_category, new_delta))
        if inserted_events:
            db.conn.executemany(
                """
                INSERT INTO token_adjustments (token, category, weight_offset)
                VALUES (?, ?, ?)
                ON CONFLICT(token, category) DO UPDATE SET
                    weight_offset = MIN(?, MAX(?, weight_offset + excluded.weight_offset))
                """,
                [
                    (token, category, delta, TOKEN_ADJUSTMENT_CAP, -TOKEN_ADJUSTMENT_CAP)
                    for token, category, delta in inserted_events
                ],
            )
    return len(inserted_events)


def prune_unweighted_token_adjustments(db) -> int:
    cursor = db.conn.execute("SELECT DISTINCT token FROM token_adjustments")
    stored_tokens = [str(row["token"] or "").strip().lower() for row in cursor.fetchall()]
    if not stored_tokens:
        return 0
    weighted_tokens = _weighted_adjustment_tokens(set(stored_tokens))
    stale_tokens = sorted({token for token in stored_tokens if token and token not in weighted_tokens})
    if not stale_tokens:
        return 0
    placeholders = ", ".join("?" for _ in stale_tokens)
    with db._write_transaction():
        cursor = db.conn.execute(f"DELETE FROM token_adjustments WHERE lower(token) IN ({placeholders})", stale_tokens)
    return int(cursor.rowcount or 0)


def get_token_adjustments(db) -> Dict[str, Dict[str, float]]:
    prune_unweighted_token_adjustments(db)
    cursor = db.conn.execute("SELECT * FROM token_adjustments")
    result = {}
    for row in cursor.fetchall():
        token, category, weight = row["token"], row["category"], row["weight_offset"]
        if token not in result:
            result[token] = {}
        result[token][category] = min(TOKEN_ADJUSTMENT_CAP, max(-TOKEN_ADJUSTMENT_CAP, float(weight or 0.0)))
    return result


def delete_token_adjustments(db, adjustment_keys: List[tuple]) -> int:
    if not adjustment_keys:
        return 0
    normalized = [
        (str(token).strip().lower(), str(category).strip())
        for token, category in adjustment_keys
        if str(token).strip() and str(category).strip()
    ]
    if not normalized:
        return 0
    with db._write_transaction():
        cursor = db.conn.executemany(
            "DELETE FROM token_adjustments WHERE token = ? AND category = ?",
            normalized,
        )
    return cursor.rowcount or 0
