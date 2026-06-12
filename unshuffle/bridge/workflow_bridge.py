from pathlib import Path
from typing import Optional


class WorkflowBridge:
    """Facade over the runtime engine used by GUI and CLI workflows."""

    def __init__(self, engine):
        self._engine = engine

    @property
    def engine(self):
        return self._engine

    @property
    def db(self):
        return self._engine.db

    @property
    def local_db(self):
        return self._engine.local_db

    @property
    def target_dir(self):
        return self._engine.target_dir

    @target_dir.setter
    def target_dir(self, value):
        self.update_state(target_dir=value)

    @property
    def session_id(self):
        return self._engine.session_id

    @property
    def session_source_root(self):
        return self._engine.session_source_root

    @session_source_root.setter
    def session_source_root(self, value):
        self.update_state(session_source_root=value)

    @property
    def session_source_roots(self):
        return self._engine.session_source_roots

    @session_source_roots.setter
    def session_source_roots(self, value):
        self.update_state(session_source_roots=value)

    @property
    def interrupted(self):
        return self._engine.interrupted

    @interrupted.setter
    def interrupted(self, value):
        self.update_state(interrupted=value)

    @property
    def progress_callback(self):
        return self._engine.progress_callback

    @progress_callback.setter
    def progress_callback(self, value):
        self.update_state(progress_callback=value)

    def update_state(self, **kwargs):
        for name, value in kwargs.items():
            setattr(self._engine, name, value)

    def prepare_plan(self, *args, **kwargs):
        return self._engine.prepare_plan(*args, **kwargs)

    def execute_plan(self, *args, **kwargs):
        return self._engine.execute_plan(*args, **kwargs)

    def undo_session(self, *args, **kwargs):
        return self._engine.undo_session(*args, **kwargs)

    def close(self):
        return self._engine.close()

    def load_cache(self, *args, **kwargs):
        return self._engine.load_cache(*args, **kwargs)

    def _init_db_and_hashes(self):
        return self._engine._init_db_and_hashes()

    def log(self, *args, **kwargs):
        return self._engine.log(*args, **kwargs)


def create_workflow_bridge(
    target_dir: Path,
    progress_callback=None,
    logger_instance=None,
    session_id: Optional[str] = None,
    engine_factory=None,
):
    kwargs = {}
    if progress_callback is not None:
        kwargs["progress_callback"] = progress_callback
    if logger_instance is not None:
        kwargs["logger_instance"] = logger_instance
    if session_id is not None:
        kwargs["session_id"] = session_id

    if engine_factory is None:
        from ..runtime.engine import RuntimeUnshuffler
        from ..runtime.bootstrapper import EngineBootstrapper
        from ..core.logging import logger, setup_logging
        from ..persistence import get_local_db
        from ..logic.planning import run_plan

        bootstrapper = EngineBootstrapper(
            logger_instance=logger_instance or logger,
            setup_logging_fn=setup_logging,
            get_local_db_fn=get_local_db,
            run_plan_fn=run_plan,
        )
        kwargs["bootstrapper"] = bootstrapper
        engine_factory = RuntimeUnshuffler

    engine = engine_factory(target_dir, **kwargs)
    return WorkflowBridge(engine)
