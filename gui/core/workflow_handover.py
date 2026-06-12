from __future__ import annotations

import json
import logging
from pathlib import Path

from .workflow_summary import format_bytes, remaining_source_footprint


BUILD_HANDOVER_STATE_KEY = "build_handover_state_json"


def clear_build_handover_state(controller, *, preserve_persisted: bool = False) -> None:
    app = getattr(controller, "app", None)
    if app is None:
        return
    setattr(app, "_build_handover_state", None)
    if not preserve_persisted:
        settings = getattr(app, "settings", None)
        if settings is not None and hasattr(settings, "remove"):
            try:
                settings.remove(BUILD_HANDOVER_STATE_KEY)
            except Exception:
                logging.debug("Could not clear persisted build handover state.", exc_info=True)
    footer = getattr(app, "footer", None)
    if footer is not None and hasattr(footer, "clear_build_handover_state"):
        footer.clear_build_handover_state()


def _handover_status_and_count(controller, state: dict) -> tuple[str, str]:
    mode = str(state.get("mode") or "move")
    copied = int(state.get("moved_or_copied_count", 0) or 0)
    if mode == "move":
        remaining_count = int(state.get("remaining_source_file_count", 0) or 0)
        remaining_bytes = int(state.get("remaining_source_bytes", 0) or 0)
        leftover = (
            "Source has no remaining files."
            if remaining_count == 0
            else f"Source has {remaining_count} file{'s' if remaining_count != 1 else ''} / {format_bytes(remaining_bytes)} remaining."
        )
        return f"Move complete. {copied} file{'s' if copied != 1 else ''} moved. {leftover}", "0 files ready"

    record_count = len(getattr(getattr(getattr(controller, "app", None), "model", None), "records", []) or [])
    return f"Copy complete. {copied} file{'s' if copied != 1 else ''} copied.", f"{record_count} files ready"


def _source_tooltip(source_paths: list[str]) -> str:
    tooltip = "\n".join(source_paths)
    if len(source_paths) > 1:
        tooltip = f"Open first source:\n{source_paths[0]}\n\nAll sources:\n" + "\n".join(source_paths)
    return tooltip


def _apply_build_handover_state(controller, state: dict) -> None:
    app = getattr(controller, "app", None)
    if app is None:
        return
    source_paths = [str(path) for path in (state.get("source_paths") or []) if str(path)]
    target_path = str(state.get("target_path") or "")
    state = dict(state)
    state["source_paths"] = source_paths
    state["target_path"] = target_path
    setattr(app, "_build_handover_state", state)

    status, count_text = _handover_status_and_count(controller, state)
    footer = getattr(app, "footer", None)
    if footer is None:
        return
    mode = str(state.get("mode") or "move")
    if hasattr(footer, "set_status"):
        footer.set_status("Move complete" if mode == "move" else "Copy complete")
    if hasattr(footer, "set_count"):
        footer.set_count(count_text)
    if hasattr(footer, "set_build_handover_state"):
        footer.set_build_handover_state(
            status,
            True,
            can_open_target=bool(target_path),
            can_open_source=bool(source_paths),
            can_undo=mode == "move" and bool(state.get("session_id")),
            target_tooltip=f"Open target:\n{target_path}" if target_path else "",
            source_tooltip=_source_tooltip(source_paths),
        )


def _persist_build_handover_state(controller, state: dict) -> None:
    settings = getattr(getattr(controller, "app", None), "settings", None)
    if settings is None or not hasattr(settings, "setValue"):
        return
    try:
        settings.setValue(BUILD_HANDOVER_STATE_KEY, json.dumps(state, sort_keys=True))
    except Exception:
        logging.debug("Could not persist build handover state.", exc_info=True)


def restore_build_handover_state(controller) -> bool:
    settings = getattr(getattr(controller, "app", None), "settings", None)
    if settings is None or not hasattr(settings, "value"):
        return False
    try:
        raw = settings.value(BUILD_HANDOVER_STATE_KEY, "")
    except Exception:
        logging.debug("Could not read persisted build handover state.", exc_info=True)
        return False
    if not raw:
        return False
    try:
        state = json.loads(str(raw))
    except (TypeError, json.JSONDecodeError):
        clear_build_handover_state(controller)
        return False
    if not isinstance(state, dict):
        clear_build_handover_state(controller)
        return False
    if not (state.get("target_path") or state.get("source_paths") or state.get("session_id")):
        clear_build_handover_state(controller)
        return False
    _apply_build_handover_state(controller, state)
    return True


def open_build_handover_path(controller, state_key: str | None = None, *, path: str | None = None) -> None:
    if path is None:
        state = getattr(controller.app, "_build_handover_state", None) or {}
        path = str(state.get(state_key or "") or "")
    if not path:
        return
    from ..utils import ui_helpers

    ui_helpers.open_explorer_path(controller.app, path)


def open_build_handover_source(controller) -> None:
    state = getattr(controller.app, "_build_handover_state", None) or {}
    sources = state.get("source_paths") or []
    if not sources:
        return
    open_build_handover_path(controller, path=str(sources[0]))


def undo_build_handover(controller) -> None:
    state = getattr(controller.app, "_build_handover_state", None) or {}
    session_id = str(state.get("session_id") or "")
    if session_id:
        controller.start_undo(session_id)


def enter_build_handover_state(controller, res: dict, summary: str) -> None:
    if not controller._engine:
        return
    opts = getattr(controller, "_last_build_options", {}) or {}
    mode = "move" if bool(opts.get("move", res.get("move", True))) else "copy"
    source_paths = [
        str(root)
        for root in (getattr(controller._engine, "session_source_roots", []) or [])
        if root
    ]
    target_path = str(getattr(controller._engine, "target_dir", "") or opts.get("target") or "")
    copied = int(res.get("copied", 0) or 0)
    skipped = int(res.get("skipped_duplicates", 0) or 0) + int(res.get("duplicates", 0) or 0)
    failed = int(res.get("failed", 0) or 0)
    remaining_count, remaining_bytes = remaining_source_footprint(source_paths) if mode == "move" else (0, 0)
    state = {
        "mode": mode,
        "source_paths": source_paths,
        "target_path": target_path,
        "moved_or_copied_count": copied,
        "skipped_duplicate_count": skipped,
        "failed_count": failed,
        "remaining_source_file_count": remaining_count,
        "remaining_source_bytes": remaining_bytes,
        "session_id": str(res.get("session_id") or getattr(controller._engine, "session_id", "") or ""),
        "summary": summary,
    }

    if mode == "move":
        clear_workbench_after_move_handover(controller)

    _apply_build_handover_state(controller, state)
    _persist_build_handover_state(controller, state)
    footer = getattr(controller.app, "footer", None)
    if footer is not None:
        if hasattr(footer, "log"):
            footer.log("<b>Build:</b> " + summary.replace("\n", "<br>"))


def clear_workbench_after_move_handover(controller) -> None:
    from ..models import StagingTableModel

    data_manager = getattr(controller.app, "data_manager", None)
    model = StagingTableModel(
        [],
        getattr(controller.app, "undo_stack", None),
        sync_callback=getattr(data_manager, "sync_record_to_db", None),
    )
    if hasattr(controller.app, "set_runtime_context"):
        controller.app.set_runtime_context(model=model)
    else:
        controller.app.model = model
    if getattr(controller.app, "proxy_model", None) is not None:
        controller.app.proxy_model.setSourceModel(model)
    if getattr(controller.app, "search_controller", None) is not None:
        if hasattr(controller.app.search_controller, "clear_query_state"):
            controller.app.search_controller.clear_query_state(sync_ui=True)
        elif hasattr(controller.app.search_controller, "set_query"):
            controller.app.search_controller.set_query("", immediate=True)
    if getattr(controller.app, "view_controller", None) is not None:
        controller.app.view_controller.update_library_views(tree_delay_ms=0)


def confirm_category_target_root(controller, target: Path) -> Path:
    category_roots = {"oneshots", "loops", "non-audio assets", "utility"}
    if target.name.casefold() not in category_roots:
        return target
    parent = target.parent
    if parent == target:
        return target

    from PySide6.QtWidgets import QMessageBox

    dialog = QMessageBox(controller._parent_widget())
    dialog.setIcon(QMessageBox.Warning)
    dialog.setWindowTitle("Build Target Looks Narrow")
    dialog.setText(
        "The selected build target looks like a category folder.\n\n"
        f"Selected:\n{target}\n\n"
        "Use the parent folder as the library root instead?\n\n"
        f"Parent:\n{parent}"
    )
    use_parent = dialog.addButton("Use Parent", QMessageBox.AcceptRole)
    dialog.addButton("Keep Selected", QMessageBox.RejectRole)
    dialog.exec()
    if dialog.clickedButton() is use_parent:
        return parent
    return target
