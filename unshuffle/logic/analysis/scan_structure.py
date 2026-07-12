from __future__ import annotations

import json
from collections import Counter
from typing import Any

from ...core.constants import (
    ALIAS_TABLE,
    CHILD_DUP_BONUS,
    LARGE_BRAND_BONUS_MULT,
    LARGE_CONTAINER_MALUS,
    LEAF_GENERIC_MALUS,
    LEAF_IDENTITY_BONUS,
    LEAF_MALUS,
    LOSER_MALUS_MULT,
    MODEL_NUMBERS,
    NEIGHBOR_BOOST_BASE,
    NEIGHBOR_BOOST_MAX,
    NOISE_WORDS,
    PURE_CONTAINER_BONUS,
    PURE_GENERIC_BONUS,
    SHARED_BOOST_BASE,
    SHARED_BOOST_THRESHOLD,
    WINNER_BONUS_MULT,
)
from ...logic.classification import is_category_alias, tokenize


ROLE_LEAF = 1 << 0
ROLE_PURE = 1 << 1
ROLE_DUPLICATE = 1 << 2
ROLE_CHILD_OF_DUPLICATE = 1 << 3
ROLE_LARGE = 1 << 4
ROLE_STANDARD = 1 << 5
ROLE_ROOT = 1 << 6

_DUPLICATE_CHILD_SHARED_RATIO_THRESHOLD = 0.5


def _unweighted_tokens(name: str) -> list[str]:
    return [
        token for token in tokenize(name)
        if not is_category_alias(token) and token not in NOISE_WORDS
    ]


def _decode_tokens(value: Any) -> set[str]:
    if not value:
        return set()
    try:
        data = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return set()
    return {str(token) for token in data or () if str(token)}


def _decode_evidence(value: Any) -> dict[str, float]:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(data) if isinstance(data, dict) else {}


def analyze_scan_structure(db, scan_id: str) -> None:
    """Compute graph-equivalent directory state without hydrating file nodes."""
    conn = db.conn
    with db.write_transaction():
        conn.execute(
            """
            UPDATE scan_directories AS directory SET
                immediate_directory_count = (
                    SELECT COUNT(*) FROM scan_directories AS child
                    WHERE child.scan_id = directory.scan_id
                      AND child.parent_directory_id = directory.directory_id
                ),
                immediate_file_count = (
                    SELECT COUNT(*) FROM scan_items AS item
                    WHERE item.scan_id = directory.scan_id
                      AND item.parent_directory_id = directory.directory_id
                )
            WHERE directory.scan_id = ?
            """,
            (scan_id,),
        )

    directory_rows = conn.execute(
        """
        SELECT directory_id, parent_directory_id, depth, display_name,
               immediate_directory_count, immediate_file_count
        FROM scan_directories
        WHERE scan_id = ?
        ORDER BY depth DESC, discovery_order
        """,
        (scan_id,),
    )
    for row in directory_rows:
        directory_id = int(row[0])
        parent_id = row[1]
        name = str(row[3] or "")
        directory_count = int(row[4] or 0)
        file_count = int(row[5] or 0)
        own_tokens = _unweighted_tokens(name)
        descendant_tokens = set(own_tokens)
        descendant_count = directory_count + file_count
        child_count = 0
        pure_children = 0
        leaf_children = 0
        non_pure_containers = 0
        standard_children = 0
        only_child = None

        children = conn.execute(
            """
            SELECT directory_id, display_name, role_flags, descendant_count,
                   descendant_token_blob, token_blob
            FROM scan_directories
            WHERE scan_id = ? AND parent_directory_id = ?
            ORDER BY discovery_order
            """,
            (scan_id, directory_id),
        )
        for child in children:
            child_count += 1
            only_child = child
            flags = int(child[2] or 0)
            child_descendant_count = int(child[3] or 0)
            descendant_count += child_descendant_count
            descendant_tokens.update(_decode_tokens(child[4]))
            if flags & ROLE_PURE:
                pure_children += 1
            if flags & ROLE_LEAF:
                leaf_children += 1
            elif not flags & ROLE_PURE:
                non_pure_containers += 1
            if flags & ROLE_STANDARD:
                standard_children += 1

        files = conn.execute(
            "SELECT sample_name FROM scan_items WHERE scan_id = ? AND parent_directory_id = ? ORDER BY item_id",
            (scan_id, directory_id),
        )
        for file_row in files:
            descendant_tokens.update(_unweighted_tokens(str(file_row[0] or "")))

        flags = ROLE_ROOT if parent_id is None else 0
        if parent_id is not None and directory_count == 0:
            flags |= ROLE_LEAF
        total_children = directory_count + file_count
        if not flags & ROLE_LEAF and directory_count > 0 and leaf_children == directory_count:
            flags |= ROLE_PURE
        if not flags & ROLE_LEAF and total_children > 0 and not flags & ROLE_PURE:
            majority = pure_children + leaf_children + file_count
            if majority > total_children / 2:
                flags |= ROLE_STANDARD
        if not flags & ROLE_LEAF and total_children > 0:
            mostly_nonpure = non_pure_containers > total_children / 2
            multi_standard = standard_children >= 3 and standard_children > total_children * 0.30
            if mostly_nonpure or multi_standard:
                flags |= ROLE_LARGE

        if not flags & ROLE_LEAF and child_count == 1 and only_child is not None:
            node_tokens = {
                token for token in tokenize(name)
                if token in MODEL_NUMBERS or (token not in ALIAS_TABLE and token not in NOISE_WORDS)
            }
            child_tokens = {
                token for token in tokenize(str(only_child[1] or ""))
                if token in MODEL_NUMBERS or (token not in ALIAS_TABLE and token not in NOISE_WORDS)
            }
            shared_ratio = len(node_tokens & child_tokens) / max(len(node_tokens), len(child_tokens), 1)
            if shared_ratio > _DUPLICATE_CHILD_SHARED_RATIO_THRESHOLD:
                flags |= ROLE_DUPLICATE
                child_evidence = _decode_evidence(
                    conn.execute(
                        "SELECT weight_evidence_json FROM scan_directories WHERE scan_id = ? AND directory_id = ?",
                        (scan_id, int(only_child[0])),
                    ).fetchone()[0]
                )
                child_evidence["_duplicate_child_bonus"] = round(CHILD_DUP_BONUS * shared_ratio, 3)
                with db.write_transaction():
                    conn.execute(
                        """
                        UPDATE scan_directories
                        SET role_flags = role_flags | ?, weight_evidence_json = ?
                        WHERE scan_id = ? AND directory_id = ?
                        """,
                        (ROLE_CHILD_OF_DUPLICATE, json.dumps(child_evidence), scan_id, int(only_child[0])),
                    )

        with db.write_transaction():
            conn.execute(
                """
                UPDATE scan_directories SET
                    token_blob = ?, descendant_token_blob = ?, descendant_count = ?,
                    role_flags = ?, structure_state = 'roles_done'
                WHERE scan_id = ? AND directory_id = ?
                """,
                (
                    json.dumps(sorted(own_tokens)),
                    json.dumps(sorted(descendant_tokens)),
                    descendant_count,
                    flags,
                    scan_id,
                    directory_id,
                ),
            )

    _calculate_weights(db, scan_id)


def _calculate_weights(db, scan_id: str) -> None:
    conn = db.conn
    rows = conn.execute(
        """
        SELECT directory_id, parent_directory_id, display_name, role_flags,
               token_blob, descendant_count, weight_evidence_json
        FROM scan_directories WHERE scan_id = ? ORDER BY discovery_order
        """,
        (scan_id,),
    )
    for row in rows:
        directory_id = int(row[0])
        name = str(row[2] or "")
        flags = int(row[3] or 0)
        tokens = list(_decode_tokens(row[4]))
        total_descendants = int(row[5] or 0)
        temporary = _decode_evidence(row[6])
        evidence: dict[str, float] = {}
        weight = 0.0
        if flags & ROLE_PURE:
            if _unweighted_tokens(name):
                weight += PURE_CONTAINER_BONUS
                evidence["PURE"] = PURE_CONTAINER_BONUS
            else:
                weight += PURE_GENERIC_BONUS
                evidence["PURE_GENERIC"] = PURE_GENERIC_BONUS
        if flags & ROLE_LARGE:
            weight += LARGE_CONTAINER_MALUS
            evidence["LARGE_MALUS"] = LARGE_CONTAINER_MALUS
            identity_tokens = _unweighted_tokens(name)
            if identity_tokens:
                bonus = round(LARGE_BRAND_BONUS_MULT * len(identity_tokens), 3)
                weight += bonus
                evidence["LARGE_BRAND_BONUS"] = bonus
        if flags & ROLE_CHILD_OF_DUPLICATE:
            bonus = float(temporary.get("_duplicate_child_bonus") or CHILD_DUP_BONUS)
            weight += bonus
            evidence["CHILD_DUP"] = bonus
        if flags & ROLE_LEAF:
            if _unweighted_tokens(name):
                weight += LEAF_IDENTITY_BONUS
                evidence["LEAF_IDENTITY"] = LEAF_IDENTITY_BONUS
            else:
                weight += LEAF_GENERIC_MALUS
                evidence["LEAF_GENERIC"] = LEAF_GENERIC_MALUS

        has_boost = False
        if not flags & ROLE_ROOT and tokens and total_descendants > 0:
            match_count = 0
            parent_token_count = len(tokens)
            children = conn.execute(
                """
                SELECT token_blob, descendant_token_blob, descendant_count
                FROM scan_directories
                WHERE scan_id = ? AND parent_directory_id = ? ORDER BY discovery_order
                """,
                (scan_id, directory_id),
            )
            for child in children:
                child_own = _decode_tokens(child[0])
                if parent_token_count >= len(child_own):
                    if set(tokens) & _decode_tokens(child[1]):
                        match_count += 1 + int(child[2] or 0)
            files = conn.execute(
                "SELECT sample_name FROM scan_items WHERE scan_id = ? AND parent_directory_id = ? ORDER BY item_id",
                (scan_id, directory_id),
            )
            for file_row in files:
                if set(tokens) & set(_unweighted_tokens(str(file_row[0] or ""))):
                    match_count += 1
            if match_count / total_descendants >= SHARED_BOOST_THRESHOLD:
                token_count = min(4, parent_token_count)
                boost = SHARED_BOOST_BASE if token_count <= 1 else SHARED_BOOST_BASE + SHARED_BOOST_BASE * token_count
                weight += boost
                evidence["SHARED_BOOST"] = round(boost, 3)
                has_boost = True
        if flags & ROLE_LEAF and not has_boost:
            weight += LEAF_MALUS
            evidence["LEAF_MALUS"] = LEAF_MALUS
        with db.write_transaction():
            conn.execute(
                """
                UPDATE scan_directories SET pack_weight = ?, weight_evidence_json = ?, structure_state = 'weighted'
                WHERE scan_id = ? AND directory_id = ?
                """,
                (round(weight, 2), json.dumps(evidence), scan_id, directory_id),
            )

    _apply_neighbor_boosts(db, scan_id)
    _apply_parent_adjustments(db, scan_id)


def _apply_neighbor_boosts(db, scan_id: str) -> None:
    conn = db.conn
    boosted: set[int] = set()
    rows = conn.execute(
        """
        SELECT directory_id, parent_directory_id, display_name, pack_weight, role_flags
        FROM scan_directories WHERE scan_id = ? ORDER BY discovery_order
        """,
        (scan_id,),
    )
    for row in rows:
        directory_id = int(row[0])
        flags = int(row[4] or 0)
        name_tokens = tokenize(str(row[2] or ""))
        if flags & ROLE_ROOT or not name_tokens or not all(is_category_alias(token) for token in name_tokens):
            continue
        neighbors = []
        if row[1] is not None:
            parent = conn.execute(
                "SELECT directory_id, display_name, pack_weight, role_flags FROM scan_directories WHERE scan_id = ? AND directory_id = ?",
                (scan_id, int(row[1])),
            ).fetchone()
            if parent is not None and not int(parent[3] or 0) & ROLE_ROOT:
                neighbors.append(parent)
        neighbors.extend(
            conn.execute(
                """
                SELECT directory_id, display_name, pack_weight, role_flags
                FROM scan_directories WHERE scan_id = ? AND parent_directory_id = ? ORDER BY discovery_order
                """,
                (scan_id, directory_id),
            )
        )
        valid = [neighbor for neighbor in neighbors if _unweighted_tokens(str(neighbor[1] or ""))]
        if not valid:
            continue
        best = min(valid, key=lambda neighbor: abs(float(neighbor[2] or 0.0) - float(row[3] or 0.0)))
        best_id = int(best[0])
        if best_id in boosted:
            continue
        boost = min(
            round(abs(float(best[2] or 0.0) - float(row[3] or 0.0)) + NEIGHBOR_BOOST_BASE, 2),
            NEIGHBOR_BOOST_MAX,
        )
        evidence_row = conn.execute(
            "SELECT weight_evidence_json FROM scan_directories WHERE scan_id = ? AND directory_id = ?",
            (scan_id, best_id),
        ).fetchone()
        evidence = _decode_evidence(evidence_row[0] if evidence_row else None)
        evidence["NEIGHBOR_BOOST"] = boost
        with db.write_transaction():
            conn.execute(
                """
                UPDATE scan_directories SET pack_weight = pack_weight + ?, weight_evidence_json = ?
                WHERE scan_id = ? AND directory_id = ?
                """,
                (boost, json.dumps(evidence), scan_id, best_id),
            )
        boosted.add(best_id)


def _apply_parent_adjustments(db, scan_id: str) -> None:
    conn = db.conn
    rows = conn.execute(
        """
        SELECT parent.directory_id, parent.display_name, parent.role_flags, parent.pack_weight,
               SUM(CASE WHEN child.pack_weight - parent.pack_weight > 0.1 THEN 1 ELSE 0 END) AS losses,
               COUNT(*) AS total,
               SUM(CASE WHEN child.pack_weight - parent.pack_weight < -0.1 THEN 1 ELSE 0 END) AS wins
        FROM scan_directories AS child
        JOIN scan_directories AS parent
          ON parent.scan_id = child.scan_id AND parent.directory_id = child.parent_directory_id
        WHERE parent.scan_id = ?
        GROUP BY parent.directory_id
        ORDER BY parent.discovery_order
        """,
        (scan_id,),
    )
    for row in rows:
        parent_id = int(row[0])
        flags = int(row[2] or 0)
        weight = float(row[3] or 0.0)
        losses = int(row[4] or 0)
        total = int(row[5] or 0)
        wins = int(row[6] or 0)
        evidence_row = conn.execute(
            "SELECT weight_evidence_json FROM scan_directories WHERE scan_id = ? AND directory_id = ?",
            (scan_id, parent_id),
        ).fetchone()
        evidence = _decode_evidence(evidence_row[0] if evidence_row else None)
        if losses > 0:
            penalty = round(LOSER_MALUS_MULT * (losses / total), 3)
            weight += penalty
            evidence["LOSER_MALUS"] = penalty
        if wins > 0 and flags & ROLE_LARGE and _unweighted_tokens(str(row[1] or "")):
            bonus = round(WINNER_BONUS_MULT * (wins / total), 3)
            weight += bonus
            evidence["WINNER_BONUS"] = bonus
        with db.write_transaction():
            conn.execute(
                """
                UPDATE scan_directories SET pack_weight = ?, weight_evidence_json = ?, structure_state = 'done'
                WHERE scan_id = ? AND directory_id = ?
                """,
                (round(weight, 3), json.dumps(evidence), scan_id, parent_id),
            )
    with db.write_transaction():
        conn.execute(
            "UPDATE scan_directories SET structure_state = 'done' WHERE scan_id = ?",
            (scan_id,),
        )
