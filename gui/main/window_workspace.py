from __future__ import annotations


def should_auto_check_coherence_on_start(window) -> bool:
    return bool(
        getattr(window, "engine", None)
        and getattr(window, "model", None)
        and getattr(window, "coherence_controller", None)
        and window.settings_controller.get_auto_check_coherence_on_start()
    )


def is_library_map_enabled(window) -> bool:
    library_tab = getattr(window, "library_tab", None)
    is_available = getattr(library_tab, "is_view_available", None)
    if callable(is_available):
        return bool(is_available("map"))
    settings_controller = getattr(window, "settings_controller", None)
    get_modes = getattr(settings_controller, "get_library_view_modes", None)
    if callable(get_modes):
        modes = get_modes()
        if isinstance(modes, (list, tuple, set)):
            return "map" in {str(mode).lower() for mode in modes}
        return False
    return True


def should_prepare_sound_map(window) -> bool:
    return window._is_library_map_enabled()


def open_system_workspace(window, section: str | None = None) -> None:
    target_section = section or "tree_organization"
    if (
        target_section == "tree_organization"
        and getattr(getattr(window, "system_page", None), "tree_organization_panel", None) is not None
        and getattr(window, "tree_organization_controller", None) is not None
    ):
        window.tree_organization_controller.show_profile_list()
    if (
        target_section == "tree_organization"
        and getattr(getattr(window, "system_page", None), "tree_organization_panel", None) is None
        and getattr(window, "tree_organization_controller", None) is not None
    ):
        window.tree_organization_controller.open_editor()
        return
    window._suppress_page_history = True
    try:
        if window.system_controller is not None:
            window.system_controller.open_workspace()
        if getattr(window, "system_page", None) is not None:
            window.system_page._set_section(target_section)
    finally:
        window._suppress_page_history = False
    window._record_current_page()


def open_coherence_map(window) -> None:
    window._suppress_page_history = True
    try:
        if not window.library_tab.is_view_available("map"):
            window.set_library_view_available("map", True)
        window.stack.setCurrentWidget(window.library_tab)
        window.view_controller.set_view_mode("map")
    finally:
        window._suppress_page_history = False
    window._record_current_page()


def open_library_workspace(window) -> None:
    window.stack.setCurrentWidget(window.library_tab)
    window._record_current_page()


def open_history_workspace(window) -> None:
    window._refresh_history_page()
    window.stack.setCurrentWidget(window.history_page)
    window._record_current_page()


def refresh_history_page(window) -> None:
    if getattr(window, "history_page", None):
        from ..utils.history import resolve_history_target

        target = resolve_history_target(window.settings)
        window.history_page.refresh_from_target(str(target or ""))


def open_build_workspace(window, *, build_page_cls, message_box) -> None:
    records = window.current_records()
    if not records or not window.engine:
        message_box.information(window, "Empty Workbench", "Scan some folders first!")
        return
    active_profile = getattr(getattr(window, "tree_organization_controller", None), "active_profile", None)
    source_roots = list(getattr(window.engine, "session_source_roots", []) or [])
    signature = window._build_workspace_signature(records, source_roots, active_profile)
    if window.build_page is not None and window._build_page_signature == signature:
        window.stack.setCurrentWidget(window.build_page)
        window._record_current_page()
        return

    if window.build_page is not None:
        window.stack.removeWidget(window.build_page)
        window.build_page.deleteLater()
        window.build_page = None
        window._build_page_signature = None

    page = build_page_cls(
        window.settings,
        records,
        source_roots,
        window,
        active_tree_profile=active_profile,
    )
    window.build_page = page
    window._build_page_signature = signature
    window.stack.addWidget(page)

    def _finish_build(accepted: bool) -> None:
        opts = page.get_options() if accepted else None
        window.stack.setCurrentWidget(window.library_tab)
        window._record_current_page()
        window.stack.removeWidget(page)
        page.deleteLater()
        if window.build_page is page:
            window.build_page = None
            window._build_page_signature = None
        if opts:
            window.workflow_controller.start_commit(
                window.current_records(),
                opts["target"],
                move=opts.get("move", True),
                flat=opts.get("flat", False),
                no_px=opts.get("no_px", False),
            )

    page.accepted.connect(lambda: _finish_build(True))
    page.rejected.connect(lambda: _finish_build(False))
    window.stack.setCurrentWidget(page)
    window._record_current_page()


def build_workspace_signature(records, source_roots, active_profile) -> tuple:
    record_signature = tuple(
        (
            getattr(record, "staging_row_id", None),
            str(getattr(record, "source_path", "") or ""),
            str(getattr(record, "audio_type", "") or ""),
            str(getattr(record, "category", "") or ""),
            str(getattr(record, "subcategory", "") or ""),
            str(getattr(record, "pack", "") or ""),
        )
        for record in records
    )
    roots_signature = tuple(str(root) for root in source_roots or [])
    profile_signature = (
        getattr(active_profile, "id", None),
        getattr(active_profile, "updated_at", None),
        len(getattr(active_profile, "nodes", []) or []),
    )
    return (record_signature, roots_signature, profile_signature)
