import logging
import sys
from pathlib import Path

from .paths import get_system_dir


logger = logging.getLogger("unshuffle")


def _resolve_log_level(log_level: str | int | None) -> int:
    if isinstance(log_level, int):
        return log_level

    if log_level is None:
        from .config import get_config

        config_level = get_config().get("LOG_LEVEL", "INFO")
        log_level = str(config_level)

    return getattr(logging, log_level.upper(), logging.INFO)


def setup_logging(
    target_dir: Path,
    is_dry_run: bool,
    session_id: str,
    log_level: str | int | None = None,
) -> None:
    """Configures the logging system with session-specific file handlers."""
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        try:
            handler.close()
        except OSError:
            pass

    resolved_level = _resolve_log_level(log_level)
    logger.setLevel(resolved_level)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(resolved_level)
    formatter = logging.Formatter("%(levelname)s: %(message)s")
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    try:
        log_dir = get_system_dir(target_dir, is_dry_run)
        log_file = log_dir / f"unshuffle_{session_id}.log"
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(resolved_level)
        file_formatter = logging.Formatter(
            "[%(asctime)s] %(levelname)s: %(message)s",
            "%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
    except OSError as exc:
        logger.warning("Could not create file logger: %s", exc)
