from pathlib import Path

from ..logic.classification.diagnostics import diagnose_file as diagnose_path
from ..logic.classification.diagnostics import format_file_diagnosis


class SearchBridge:
    """Bridge facade for staging search operations."""

    def __init__(self, workflow=None):
        self.workflow = workflow

    def _get_workflow(self):
        return self.workflow

    def _get_db(self):
        workflow = self._get_workflow()
        return getattr(workflow, "db", None) if workflow else None

    @property
    def engine(self):
        workflow = self._get_workflow()
        if not workflow:
            return None
        return getattr(workflow, "engine", workflow)

    @property
    def session_id(self):
        workflow = self._get_workflow()
        return workflow.session_id if workflow else None

    def has_session(self) -> bool:
        workflow = self._get_workflow()
        return bool(workflow and self._get_db() and workflow.session_id)

    def search_staging(self, canonical_query):
        if not self.has_session():
            return set()
        return self._get_db().search_staging(self.session_id, canonical_query)

    def diagnose_file(self, target_file, scan_root=None, db=None):
        resolved_db = db
        if resolved_db is None:
            resolved_db = self._get_db()
        resolved_scan_root = scan_root
        if resolved_scan_root is None:
            workflow = self._get_workflow()
            resolved_scan_root = getattr(workflow, "session_source_root", None) if workflow else None
        return diagnose_path(
            Path(target_file),
            scan_root=Path(resolved_scan_root) if resolved_scan_root is not None else None,
            db=resolved_db,
        )

    @staticmethod
    def format_diagnosis(diagnosis) -> str:
        return format_file_diagnosis(diagnosis)
