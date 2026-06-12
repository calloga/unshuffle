import argparse
import logging
import sys
import json
from pathlib import Path
from typing import Optional
from .bridge.workflow_bridge import create_workflow_bridge
from .core.logging import setup_logging
from .progress import print_progress
from .core.constants import Version

def _validate_args(parser, args):
    if args.no_prefix and not args.flat:
        parser.error("--no-prefix can only be used with --flat.")

    if args.session_id and not args.undo:
        parser.error("--session-id can only be used with --undo.")

    if args.undo:
        invalid = []
        if args.source:
            invalid.append("--source")
        if args.pack_name:
            invalid.append("--pack-name")
        if args.move:
            invalid.append("--move")
        if args.flat:
            invalid.append("--flat")
        if args.no_prefix:
            invalid.append("--no-prefix")
        if args.dry_run:
            invalid.append("--dry-run")
        if args.rebuild_cache:
            invalid.append("--rebuild-cache")
        if args.force_cache_reset:
            invalid.append("--force-cache-reset")
        if invalid:
            parser.error(f"--undo cannot be combined with: {', '.join(invalid)}")

    if not args.undo and not args.source and not (args.rebuild_cache or args.force_cache_reset):
        parser.error("No source directories specified.")

def main():
    parser = argparse.ArgumentParser(description="Unshuffle: A Pro-Grade CLI tool.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {Version}")
    parser.add_argument("-s", "--source", nargs='+', help="Messy folder path(s).")
    parser.add_argument("-o", "--output", required=True, help="Organized library path.")
    parser.add_argument("--pack-name", help="Force specific Sample Pack folder.")
    parser.add_argument("--move", action="store_true", help="Move files instead of copying.")
    parser.add_argument("--flat", action="store_true", help="Flatten and prepend prefixes.")
    parser.add_argument("--no-prefix", action="store_true", help="No prefixes in Flat Mode.")
    parser.add_argument("--dry-run", action="store_true", help="Analytical pass with transient metadata cleanup.")
    parser.add_argument("--rebuild-cache", action="store_true", help="Rebuild hash cache.")
    parser.add_argument("--force-cache-reset", action="store_true", help="Force cache reset.")
    parser.add_argument("-y", "--yes", action="store_true", help="Proceed non-interactively where confirmation would otherwise be required.")
    parser.add_argument("--undo", action="store_true", help="Reverse a previous organization session.")
    parser.add_argument("--session-id", help="Target a specific run for undo.")
    args = parser.parse_args()
    _validate_args(parser, args)
    target_dir = Path(args.output).resolve()

    def cli_callback(data: dict):
        current = data.get("current")
        total = data.get("total")
        message = data.get("message")
        if current is not None and total is not None:
            print_progress(current, total, prefix='Organizing:', suffix='Complete')
        elif message:
            print(f"  {message}")

    engine: Optional[object] = None
    try:
        engine = create_workflow_bridge(target_dir, progress_callback=cli_callback)
        if args.undo:
            setup_logging(target_dir, False, engine.session_id)
            sid = args.session_id
            if not sid:
                recent = engine.db.get_recent_sessions(1, only_executed=True, target_root=target_dir)
                if not recent:
                    print("Error: No recent sessions found to undo.")
                    return 1
                sid = recent[0]['session_id']
                print(f"No session ID specified. Targeting latest: {sid}")
                
            res = engine.undo_session(sid)
            if "error" in res:
                print(f"Undo Failed: {res['error']}")
                return 1
            print(f"Successfully rolled back {res.get('undone', 0)} operations.")
            return 0

        setup_logging(target_dir, args.dry_run, engine.session_id)
        
        try:
            if args.rebuild_cache or args.force_cache_reset:
                engine.load_cache(rebuild=args.rebuild_cache, force_reset=args.force_cache_reset)
        except (json.JSONDecodeError, OSError):
            if args.force_cache_reset:
                engine.load_cache(force_reset=True)
            elif args.yes:
                engine.load_cache(force_reset=True)
            elif not sys.stdin.isatty():
                return 1
            else:
                choice = input("Proceed and erase corrupt cache? (y/N): ")
                if choice.lower() != 'y':
                    return 1
                engine.load_cache(force_reset=True)

        sources = [Path(s) for s in args.source] if args.source else []
        if not sources and (args.rebuild_cache or args.force_cache_reset):
            return 0
            
        plan = engine.prepare_plan(sources, pack_name_override=args.pack_name)
        results = engine.execute_plan(
            plan,
            move=args.move,
            dry_run=args.dry_run,
            flat=args.flat,
            no_prefix=args.no_prefix
        )

        if results.get("error"):
            print(f"Error: {results['error']}")
            return 1
        if not results.get("interrupted") and results.get("total", 0) > 0:
            print("\n\n--- Migration Summary ---")
            action = "Moved" if results["move"] else "Copied"
            if results["dry_run"]:
                action = "Would have " + action.lower()
            print(f"Total Unique Files {action}: {results['copied']}")
            print(f"Clones/Duplicates Ignored: {results['duplicates']}")
            if results["dry_run"] and results["report_path"]:
                print(f"\nDry-run report saved to:\n  {results['report_path']}")

        return 0
    except Exception:
        import traceback
        traceback.print_exc()
        return 1
    finally:
        if engine is not None:
            engine.close()

if __name__ == "__main__":
    sys.exit(main())
