from __future__ import annotations

import json

from .settings_controller import STARTUP_LAUNCHER_LAST_CHOICE_KEY


def restore_choice_payload(target: str, session_id: str = "", roots=None) -> dict:
    return {
        "mode": "restore",
        "target": str(target),
        "session_id": str(session_id or ""),
        "roots": [str(root) for root in (roots or []) if root],
    }


def persist_scan_session(settings, engine) -> None:
    settings.setValue("last_scan_session_id", engine.session_id)
    settings.setValue("last_library_target", str(engine.target_dir))
    source_roots = [str(root) for root in (engine.session_source_roots or []) if root]
    settings.setValue(
        STARTUP_LAUNCHER_LAST_CHOICE_KEY,
        json.dumps(restore_choice_payload(str(engine.target_dir), engine.session_id, source_roots)),
    )


def persist_build_session(settings, engine, session_id: str) -> None:
    session_id = str(session_id or "")
    if session_id:
        settings.setValue("last_scan_session_id", session_id)
    settings.setValue("last_library_target", str(engine.target_dir))
    settings.setValue("last_target", str(engine.target_dir))
    settings.setValue("last_history_target", str(engine.target_dir))
    if session_id:
        source_roots = [str(root) for root in (getattr(engine, "session_source_roots", []) or []) if root]
        settings.setValue(
            STARTUP_LAUNCHER_LAST_CHOICE_KEY,
            json.dumps(restore_choice_payload(str(engine.target_dir), session_id, source_roots)),
        )


def persist_restored_source(settings, source: str, *, session_id: str = "") -> None:
    source = str(source or "").strip()
    if not source:
        return
    settings.setValue("last_library_target", source)
    settings.setValue("last_scan_source", source)
    settings.setValue("last_target", source)
    settings.setValue(
        STARTUP_LAUNCHER_LAST_CHOICE_KEY,
        json.dumps(restore_choice_payload(source, session_id, [source])),
    )


def persist_restored_source_scan_target(settings, source: str) -> None:
    source = str(source or "").strip()
    if not source:
        return
    settings.setValue("last_library_target", source)
    settings.setValue("last_scan_source", source)
    settings.setValue("last_target", source)

