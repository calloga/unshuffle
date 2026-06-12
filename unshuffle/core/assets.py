import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def asset_roots() -> list[Path]:
    roots: list[Path] = []
    env_root = os.environ.get("UNSHUFFLE_ASSET_ROOT")
    if env_root:
        roots.append(Path(env_root))
    roots.append(REPO_ROOT)
    roots.append(Path(sys.prefix) / "share" / "unshuffle")
    return roots


def asset_path(*parts: str) -> Path:
    relative = Path(*parts)
    for root in asset_roots():
        candidate = root / relative
        if candidate.exists():
            return candidate
    return asset_roots()[0] / relative
