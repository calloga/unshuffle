"""
Backfill profile_json for verified anchor_profiles rows that have NULL or empty profile_json.

Usage:
    python scripts/backfill_anchor_profile_json.py <path-to-db>

The script is read-write but non-destructive: it only sets profile_json where it is
currently missing.  It does not change state, vectors, or any other column.

Exit codes:
    0  All missing rows repaired (or none were missing).
    1  One or more rows could not be repaired (missing feature_schema_json or vectors).
       Affected anchor_ids are printed to stderr.
"""

import json
import sqlite3
import sys
from pathlib import Path


def _find_broken(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT anchor_id, session_id, profile_json
        FROM anchor_profiles
        WHERE state = 'verified'
        ORDER BY session_id, anchor_id
        """
    ).fetchall()

    broken = []
    for row in rows:
        existing = row["profile_json"]
        if existing:
            try:
                parsed = json.loads(existing)
                if isinstance(parsed, dict) and parsed:
                    continue
            except (json.JSONDecodeError, TypeError):
                pass
        broken.append({"anchor_id": str(row["anchor_id"]), "session_id": str(row["session_id"])})
    return broken


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <path-to-db>", file=sys.stderr)
        return 2

    db_path = Path(sys.argv[1])
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2

    # Resolve the unshuffle package from the repo root (script lives in scripts/).
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from unshuffle.persistence.stores.coherence_store import repair_anchor_profile_json
    from unshuffle.logic.coherence.anchor_profiles import build_anchor_payload

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    broken = _find_broken(conn)
    if not broken:
        print("No verified anchors with missing profile_json. Nothing to do.")
        conn.close()
        return 0

    print(f"Found {len(broken)} verified anchor(s) with missing profile_json.")

    # Group by session_id for efficiency.
    by_session: dict[str, list[str]] = {}
    for item in broken:
        by_session.setdefault(item["session_id"], []).append(item["anchor_id"])

    all_failed: list[str] = []
    total_repaired = 0

    for session_id, anchor_ids in by_session.items():
        with conn:
            failed = repair_anchor_profile_json(conn, session_id, anchor_ids, build_anchor_payload)
        repaired = len(anchor_ids) - len(failed)
        total_repaired += repaired
        all_failed.extend(failed)
        if repaired:
            print(f"  Session {session_id}: repaired {repaired} anchor(s).")
        if failed:
            print(f"  Session {session_id}: could not repair {len(failed)} anchor(s):", file=sys.stderr)
            for aid in failed:
                print(f"    - {aid}", file=sys.stderr)

    conn.close()

    print(f"\nDone: {total_repaired} repaired, {len(all_failed)} failed.")
    return 1 if all_failed else 0


if __name__ == "__main__":
    sys.exit(main())
