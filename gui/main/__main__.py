import os
import platform
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path


if __name__ == "__main__":
    try:
        from .launcher import main

        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        try:
            root = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Unshuffle"
            root.mkdir(parents=True, exist_ok=True)
            with open(root / "gui_launcher_import_crash.log", "a", encoding="utf-8") as handle:
                handle.write(
                    "\n".join(
                        [
                            f"[{datetime.now(timezone.utc).isoformat()}] import/bootstrap crash",
                            f"python={platform.python_version()}",
                            f"platform={platform.platform()}",
                            f"executable={sys.executable}",
                            traceback.format_exc().rstrip(),
                            "",
                        ]
                    )
                )
                handle.write("\n")
        finally:
            raise
