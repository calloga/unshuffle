"""Developer-only database cleanup for oversized test/dev metadata stores."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from unshuffle.persistence import get_db


def run_dev_database_maintenance(target: Path, *, vacuum: bool = True) -> dict:
    with get_db(target) as db:
        pruned = db.prune_ephemeral_state(
            set(),
            target_root=target,
            use_restorable_fallback=False,
        )
        compaction = db.force_compact() if vacuum else {"ran": False, "reason": "not_requested"}
    return {"pruned": pruned, "compaction": compaction}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Developer-only full Unshuffle DB prune/compact for oversized test databases.",
    )
    parser.add_argument("target", type=Path, help="Target library path whose Unshuffle DB should be maintained.")
    parser.add_argument("--no-vacuum", action="store_true", help="Prune stale ephemeral rows without compacting the DB file.")
    args = parser.parse_args()

    result = run_dev_database_maintenance(args.target, vacuum=not args.no_vacuum)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
