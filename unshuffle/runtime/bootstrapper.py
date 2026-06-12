from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..core.logging import logger, setup_logging
from ..persistence import get_local_db
from ..logic.planning import run_plan


@dataclass(frozen=True)
class EngineBootstrapper:
    """Minimal composition root for the runtime engine layer."""

    logger_instance: object = logger
    setup_logging_fn: Callable[..., None] = setup_logging
    get_local_db_fn: Callable[[Path], object] = get_local_db
    run_plan_fn: Callable[..., list] = run_plan
