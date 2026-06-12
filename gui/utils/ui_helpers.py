from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHeaderView
from .constants import (
    STAGING_HEADERS,
    StagingColumn,
    TABLE_HEADER_DEFAULT_HEIGHT,
    TABLE_HEADER_FIXED_WIDTH,
    TABLE_HEADER_MIN_WIDTH,
    TABLE_HEADER_ROW0_HEIGHT,
)
from .styles import apply_style, scaled_px, vertical_header_style
from .widget_helpers import apply_fixed_width, apply_minimum_width

def setup_view_headers(app):
    """Configures the main library table headers and delegates."""
    from ..widgets.delegates import TagPillDelegate
    
    view = app.library_tab.view_table
    vh = view.verticalHeader()
    vh.setVisible(True)
    apply_style(vh, vertical_header_style())
    vh.setFont(app.font())
    vh.setDefaultAlignment(Qt.AlignCenter)
    apply_minimum_width(vh, scaled_px(TABLE_HEADER_MIN_WIDTH))
    vh.setDefaultSectionSize(scaled_px(TABLE_HEADER_DEFAULT_HEIGHT))
    vh.setSectionResizeMode(QHeaderView.Interactive)
    vh.resizeSection(0, scaled_px(TABLE_HEADER_ROW0_HEIGHT))
    apply_fixed_width(vh, scaled_px(TABLE_HEADER_FIXED_WIDTH))

    if not getattr(app, "_view_headers_initialized", False):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            try:
                vh.sectionResized.disconnect(app.library_tab._on_row_resized)
            except (RuntimeError, TypeError): pass
        vh.sectionResized.connect(app.library_tab._on_row_resized)

        hh = view.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setMinimumSectionSize(scaled_px(app.library_tab.minimum_table_column_width()))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            try:
                hh.sectionResized.disconnect(app.library_tab._on_column_resized)
            except (RuntimeError, TypeError): pass
        hh.sectionResized.connect(app.library_tab._on_column_resized)
        
        view.setItemDelegateForColumn(StagingColumn.TAGS, TagPillDelegate(view))
        app._view_headers_initialized = True
    else:
        hh = view.horizontalHeader()
    hh.setFont(app.font())
    hh.setMinimumSectionSize(scaled_px(app.library_tab.minimum_table_column_width()))

    if hasattr(app.library_tab, "apply_table_column_visibility"):
        app.library_tab.apply_table_column_visibility()

    visible_cols = [c for c in range(hh.count()) if not view.isColumnHidden(c)]
    for col in visible_cols:
        hh.setSectionResizeMode(col, QHeaderView.Interactive)

def open_explorer(app, target):
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtCore import QUrl, QModelIndex
    try:
        if isinstance(target, QModelIndex):
            row = app.proxy_model.mapToSource(target).row()
            rec = app.model.records[row]
        else:
            rec = target
        path = str(rec.source_path.parent.absolute())
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
    except Exception as e:
        app.footer.set_status(f"Error opening explorer: {e}")

def open_explorer_path(app, path):
    from pathlib import Path
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtCore import QUrl
    try:
        target = Path(path)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.absolute())))
    except Exception as e:
        app.footer.set_status(f"Error opening explorer: {e}")

def setup_global_actions(app):
    from PySide6.QtGui import QAction, QKeySequence
    app.act_undo = QAction("Undo", app)
    app.act_undo.setShortcuts(QKeySequence.keyBindings(QKeySequence.Undo))
    app.act_undo.triggered.connect(app.undo_stack.undo)
    app.addAction(app.act_undo)

    app.act_redo = QAction("Redo", app)
    redo_shortcuts = list(QKeySequence.keyBindings(QKeySequence.Redo))
    for seq in ("Ctrl+Y", "Ctrl+Shift+Z"):
        key = QKeySequence(seq)
        if all(existing != key for existing in redo_shortcuts):
            redo_shortcuts.append(key)
    app.act_redo.setShortcuts(redo_shortcuts)
    app.act_redo.triggered.connect(app.undo_stack.redo)
    app.addAction(app.act_redo)

    app.act_new = QAction("New Staging Session...", app)
    app.act_new.setShortcut("Ctrl+N")
    app.act_new.triggered.connect(lambda: app.library_tab._on_new_clicked())
    app.addAction(app.act_new)

    app.act_add = QAction("Expand Current Session...", app)
    app.act_add.setShortcut("Ctrl+Shift+A")
    app.act_add.triggered.connect(lambda: app.library_tab._on_add_clicked())
    app.addAction(app.act_add)
    
    app.act_refresh = QAction("Refresh All Staged Folders", app)
    app.act_refresh.setShortcut("Ctrl+R")
    app.act_refresh.triggered.connect(lambda: handle_refresh_all(app))
    app.addAction(app.act_refresh)

    app.act_delete = QAction("Delete from Disk", app)
    app.act_delete.setShortcut("Delete")
    from ..main.actions.selection import delete_selection_from_disk
    app.act_delete.triggered.connect(lambda: delete_selection_from_disk(app))
    app.addAction(app.act_delete)
    
    app.footer.discardRequested.connect(app.drafting_controller.discard_reorg_draft)
    app.footer.saveRequested.connect(app.drafting_controller.save_reorg_draft)
    app.footer.cancelRequested.connect(lambda: app.worker_manager.request_cancel())
    app.footer.reviewCoherenceRequested.connect(app.coherence_controller.review_refinements)
    app.footer.buildRequested.connect(app.open_build_workspace)
    app.footer.viewSummaryRequested.connect(app.workflow_controller.show_active_scan_summary)
    app.footer.openBuildTargetRequested.connect(app.workflow_controller.open_build_handover_target)
    app.footer.openBuildSourceRequested.connect(app.workflow_controller.open_build_handover_source)
    app.footer.undoBuildRequested.connect(app.workflow_controller.undo_build_handover)
    
    app.custom_menu_bar.syncRequested.connect(lambda: app.workflow_controller.start_refresh([]))
    app.custom_menu_bar.toggleViewRequested.connect(app.view_controller.cycle_view_mode)
    app.custom_menu_bar.saveViewDefaultRequested.connect(lambda: _save_current_view_default(app))
    app.custom_menu_bar.libraryViewAvailabilityRequested.connect(app.set_library_view_available)
    app.custom_menu_bar.startupLauncherVisibilityRequested.connect(app.set_startup_launcher_visible)
    app.custom_menu_bar.tableColumnVisibilityRequested.connect(app.library_tab.set_column_visible)
    app.custom_menu_bar.treeOrganizationEditRequested.connect(app.tree_organization_controller.open_editor)
    app.custom_menu_bar.showNonAudioAssetsRequested.connect(lambda checked: _set_show_non_audio_assets(app, checked))
    app.custom_menu_bar.toggleDockedRequested.connect(app.view_controller.toggle_docked)
    app.custom_menu_bar.undoRequested.connect(app.undo_stack.undo)
    app.custom_menu_bar.redoRequested.connect(app.undo_stack.redo)
    app.custom_menu_bar.zoomRequested.connect(lambda zoom: _set_zoom(app, zoom))
    app.custom_menu_bar.themeRequested.connect(lambda theme: _set_theme(app, theme))
    app.custom_menu_bar.libraryRequested.connect(lambda: app.open_library_workspace())
    app.custom_menu_bar.systemRequested.connect(app.open_system_workspace)
    app.custom_menu_bar.systemTaxonomyDryRunRequested.connect(lambda: app.system_controller.run_dry_run())
    app.custom_menu_bar.systemTaxonomyRescanRequested.connect(lambda: app.system_controller.run_rescan())
    app.custom_menu_bar.systemTaxonomyResetWeightsRequested.connect(lambda: app.system_controller.reset_weights())
    app.custom_menu_bar.systemTaxonomyRefreshConflictsRequested.connect(lambda: app.system_controller.refresh_conflicts())
    app.custom_menu_bar.systemTaxonomySyncApplyRequested.connect(lambda: app.system_controller.sync_apply())
    app.custom_menu_bar.historyRequested.connect(app.open_history_workspace)
    app.system_page.runCoherenceRequested.connect(lambda: app.coherence_controller.start_coherence_audit(force=True, mode="manual"))
    app.system_page.continuousRefinementRequested.connect(app.coherence_controller.start_continuous_refinement)
    app.system_page.autoCheckCoherenceChanged.connect(app.settings_controller.set_auto_check_coherence_on_start)
    
    from ..main import actions
    app.custom_menu_bar.libraryAboutToShow.connect(lambda: actions.refresh_library_menu(app))
    app.custom_menu_bar.buildAboutToShow.connect(lambda: actions.refresh_build_menu(app))
    app.custom_menu_bar.selectionAboutToShow.connect(lambda: actions.refresh_selection_menu(app))
    app.custom_menu_bar.historyAboutToShow.connect(lambda: actions.refresh_history_menu(app))

def connect_orchestrator_signals(app):
    """
    Wires up all the signals between components, controllers, and the main app.
    """
    from ..main import actions
    app.library_tab.scanRequested.connect(app.workflow_controller.start_scan)
    app.library_tab.undoRequested.connect(app.undo_stack.undo)
    app.library_tab.redoRequested.connect(app.undo_stack.redo)
    app.library_tab.searchChanged.connect(app.search_controller.set_query)
    app.library_tab.sortChanged.connect(app.view_controller.apply_current_sort_state)
    app.library_tab.typeToggleClicked.connect(app.search_controller.handle_type_filter)
    app.library_tab.viewModeRequested.connect(app.view_controller.set_view_mode)
    app.library_tab.viewSwitchRequested.connect(app.view_controller.cycle_view_mode)
    app.library_tab.categoryFilterRequested.connect(app.search_controller.handle_category_filter)
    app.library_tab.playRequested.connect(lambda t: app.audio_controller.handle_play_request(t, app.model, app.proxy_model))
    app.library_tab.similarityRequested.connect(app.acoustic_controller.handle_similarity_request_compact)
    app.library_tab.headerMenuRequested.connect(app.filter_controller.show_header_menu)
    app.library_tab.removeFolderRequested.connect(lambda path: actions.remove_folder_clicked_via_pill(app, path))
    app.library_tab.deleteRecordsRequested.connect(app.workflow_controller.delete_records_physically)
    app.library_tab.reorganizeRecordsRequested.connect(app.drafting_controller.handle_tree_reorganize)
    app.library_tab.saveFilterRequested.connect(app.filter_controller.prompt_save_filter)
    app.library_tab.savedFilterRequested.connect(app.filter_controller.handle_saved_filter)
    app.library_tab.removeSavedFilterRequested.connect(app.filter_controller.remove_saved_filter)
    app.library_tab.quickFilterRequested.connect(app.filter_controller.handle_quick_filter)
    app.library_tab.refreshRequested.connect(lambda p: app.workflow_controller.start_refresh([p]))
    app.library_tab.toggleFilterRequested.connect(lambda path, active, mode="replace": app.filter_controller.apply_filter_query(f'source:"{path}"', active, mode=mode))
    app.library_tab.openExplorerRequested.connect(lambda t: open_explorer(app, t))
    app.library_tab.focusSearchRequested.connect(lambda: focus_global_search(app))
    app.library_tab.tagsEditRequested.connect(app.drafting_controller.apply_bulk_tags)
    app.library_tab.preserveRequested.connect(app.drafting_controller.apply_preserve_pack)
    app.library_tab.unpreserveRequested.connect(app.drafting_controller.apply_unpreserve_pack)
    app.library_tab.treeOrganizationEditRequested.connect(app.tree_organization_controller.open_editor)
    
    app.library_tab.bulkCategoryRequested.connect(app.drafting_controller.apply_bulk_category)
    app.library_tab.bulkSubcategoryRequested.connect(app.drafting_controller.apply_bulk_subcategory)
    app.library_tab.bulkTypeRequested.connect(app.drafting_controller.apply_bulk_type)
    app.library_tab.rangeChanged.connect(app.search_controller.set_confidence_range)

    app.dock_view.searchChanged.connect(app.search_controller.set_query)
    app.dock_view.saveSearchRequested.connect(app.filter_controller.prompt_save_filter)
    app.dock_view.filterRequested.connect(app.filter_controller.handle_saved_filter)
    app.dock_view.typeToggleClicked.connect(app.search_controller.handle_type_filter)
    app.dock_view.playRequested.connect(lambda t: app.audio_controller.handle_play_request(t, app.model, app.proxy_model))
    app.dock_view.similarityRequested.connect(app.acoustic_controller.handle_similarity_request_compact)
    app.dock_view.excludeRequested.connect(app.workflow_controller.handle_tree_exclude)
    app.dock_view.quickFilterRequested.connect(app.filter_controller.handle_quick_filter)
    app.dock_view.categoryFilterRequested.connect(app.search_controller.handle_category_filter)
    app.dock_view.categoryChangeRequested.connect(app.drafting_controller.handle_tree_category_change)
    app.dock_view.tagsEditRequested.connect(app.drafting_controller.apply_bulk_tags)
    app.dock_view.openExplorerRequested.connect(lambda t: open_explorer(app, t))
    app.dock_view.rangeChanged.connect(app.search_controller.set_confidence_range)
    app.dock_view.viewModeChanged.connect(app.view_controller.on_docked_view_mode_changed)
    app.dock_view.audioPreviewRequested.connect(app.coherence_controller.preview_audio_path)
    app.dock_view.anchorRequested.connect(app.coherence_controller.promote_record_as_anchor)
    app.dock_view.findRequested.connect(app.coherence_controller.find_audio_path)
    
    app.search_controller.searchFinished.connect(app.search_controller.on_search_finished_logic)
    app.search_controller.filterChanged.connect(app.search_controller.sync_search_ui)
    if hasattr(app.library_tab, "refresh_search_suggestions"):
        app.tagging_controller.taggingFinished.connect(app.library_tab.refresh_search_suggestions)
        app.coherence_controller.coherenceFinished.connect(app.library_tab.refresh_search_suggestions)
    app.workflow_controller.engineChanged.connect(lambda e: on_engine_changed(app, e))
    app.workflow_controller.scanStarted.connect(lambda sources, append: app.footer.log(f"<b>Mode: Deep Analysis (Append={append})</b>"))
    app.workflow_controller.scanStarted.connect(lambda sources, append: app.footer.set_count("Scanning..."))
    app.workflow_controller.scanDataReady.connect(app.workflow_controller.finalize_scan_data_from_signal)
    app.workflow_controller.exclusionAdded.connect(app.workflow_controller.on_exclusion_added)
    
    app.drafting_controller.draftChanged.connect(lambda: app.footer.set_reorg_draft_state("Draft changes pending", app.drafting_controller.has_changes(), can_save=app.drafting_controller.has_changes()))
    
    app.acoustic_controller.vibeStarted.connect(lambda name: [app.vibe_bar.set_anchor_text(name), app.vibe_bar.show()])
    app.acoustic_controller.vibeCleared.connect(lambda: [app.vibe_bar.hide(), app.vibe_bar.set_value(0), app.vibe_bar.set_anchor_text("Similarity Explorer")])
    app.acoustic_controller.biasChanged.connect(lambda: app.view_controller.update_library_views(0))
    app.vibe_bar.biasChanged.connect(app.acoustic_controller.on_similarity_bias_changed)
    app.vibe_bar.closeRequested.connect(app.acoustic_controller.clear_vibe)
    
    app.worker_manager.progress.connect(lambda d: handle_progress(app, d))
    app.worker_manager.finished.connect(lambda wt, res: on_worker_finished(app, wt, res))
    from PySide6.QtWidgets import QMessageBox
    app.worker_manager.error.connect(lambda e: QMessageBox.critical(app, "Error", e))
    app.worker_manager.busyStateChanged.connect(lambda b: set_ui_busy(app, b))
    app.worker_manager.cancelling.connect(lambda: app.footer.set_status("Stopping..."))

    app.audio_controller.statusRequested.connect(app.footer.set_status)
    app.audio_controller.similaritySearchRequested.connect(lambda q: [app.library_tab.edit_search.setText(q), app.search_controller.execute_search()])
    
    app.undo_stack.canUndoChanged.connect(lambda: update_undo_redo_states(app))
    app.undo_stack.canRedoChanged.connect(lambda: update_undo_redo_states(app))
    app.undo_stack.indexChanged.connect(lambda i: on_undo_stack_changed(app, i))
    app.drafting_controller.draftChanged.connect(lambda: update_undo_redo_states(app))


def _save_current_view_default(app) -> None:
    from PySide6.QtWidgets import QMessageBox

    view_mode = str(app.library_tab.current_view_mode() or "table").strip().lower()
    app.settings_controller.save_view_default(view_mode)
    label = {"table": "Table", "tree": "Tree", "map": "Map"}.get(view_mode, view_mode.title() or "Table")
    QMessageBox.information(app, "Default View", f"{label} view will open by default.")


def update_undo_redo_states(app):
    import shiboken6
    undo_stack = getattr(app, "undo_stack", None)
    if undo_stack is None or not shiboken6.isValid(undo_stack):
        return
    has_draft = getattr(app, "drafting_controller", None) is not None and app.drafting_controller.has_changes()
    can_undo = app.undo_stack.canUndo() and not has_draft
    can_redo = app.undo_stack.canRedo() and not has_draft

    if hasattr(app, "library_tab") and app.library_tab is not None:
        if hasattr(app.library_tab, "btn_undo") and app.library_tab.btn_undo is not None:
            app.library_tab.btn_undo.setEnabled(can_undo)
        if hasattr(app.library_tab, "btn_redo") and app.library_tab.btn_redo is not None:
            app.library_tab.btn_redo.setEnabled(can_redo)
    if hasattr(app, "act_undo") and app.act_undo is not None:
        app.act_undo.setEnabled(can_undo)
    if hasattr(app, "act_redo") and app.act_redo is not None:
        app.act_redo.setEnabled(can_redo)
    if hasattr(app, "custom_menu_bar") and app.custom_menu_bar is not None:
        if hasattr(app.custom_menu_bar, "act_undo") and app.custom_menu_bar.act_undo is not None:
            app.custom_menu_bar.act_undo.setEnabled(can_undo)
        if hasattr(app.custom_menu_bar, "act_redo") and app.custom_menu_bar.act_redo is not None:
            app.custom_menu_bar.act_redo.setEnabled(can_redo)

def set_ui_busy(app, busy):
    if not busy and getattr(app, "_scan_finalizing", False):
        return
    if hasattr(app, "library_tab") and app.library_tab is not None:
        if hasattr(app.library_tab, "set_busy"):
            app.library_tab.set_busy(busy)
    if hasattr(app, "footer") and app.footer is not None:
        app.footer.set_busy_state(busy)
    is_docked = bool(
        getattr(app, "stack", None) is not None
        and getattr(app, "dock_view", None) is not None
        and app.stack.currentWidget() is app.dock_view
    )
    if hasattr(app, "audio_controller") and app.audio_controller is not None:
        app.audio_controller.toggle_audio_bar(not busy if is_docked else True)

def handle_progress(app, d):
    if "message" in d: 
        app.footer.log(d['message'], html=False)
        app.footer.set_status(d['message'])
    if "current" in d and "total" in d:
        app.footer.set_progress(d['current'], d['total'])

def on_worker_finished(app, worker_type, res):
    from .history import invalidate_history_cache

    if worker_type == "scan":
        invalidate_history_cache(app.settings.value("last_target", ""))
        new_records, is_append, stats = res
        app.workflow_controller.handle_scan_finished(new_records, is_append, stats)
    elif worker_type == "commit":
        invalidate_history_cache(app.settings.value("last_target", ""))
        app.workflow_controller.handle_commit_finished(res)
    elif worker_type == "undo":
        invalidate_history_cache(app.settings.value("last_target", ""))
        app.workflow_controller.handle_undo_finished(res)

def on_engine_changed(app, engine):
    app.set_runtime_context(engine=engine)
    if getattr(app, "tree_organization_controller", None):
        app.tree_organization_controller._sync_active_profile()

def on_undo_stack_changed(app, index):
    if getattr(app, "_is_closing", False):
        return
    if not getattr(app, "model", None) or not getattr(app, "view_controller", None):
        return

    try:
        app.view_controller.apply_current_sort_state()
    except RuntimeError:
        return

    if getattr(app, "search_controller", None) and app.search_controller.current_query:
        app.search_controller.execute_search()
    else:
        app.view_controller.update_library_views(tree_delay_ms=0)

def focus_global_search(app):
    app.library_tab.edit_search.setFocus()
    app.library_tab.edit_search.selectAll()

def handle_refresh_all(app):
    from ..main import actions
    actions.handle_refresh_all(app)

def import_csv(app):
    drafting = getattr(app, "drafting_controller", None)
    if drafting is not None and not drafting.confirm_clear_pending_draft("import a CSV"):
        return
    from PySide6.QtWidgets import QFileDialog
    from pathlib import Path
    last_tgt = app.settings.value("last_target", "")
    default_dir = str(last_tgt) if last_tgt and Path(last_tgt).exists() else str(Path.home())
    path, _ = QFileDialog.getOpenFileName(app, "Import CSV", default_dir, "CSV Files (*.csv)")
    if path:
        from PySide6.QtWidgets import QMessageBox
        try:
            recs = app.data_manager.import_from_csv(path)
        except Exception as exc:
            QMessageBox.warning(app, "Import CSV", f"Could not import CSV: {exc}")
            return
        if recs:
            app.workflow_controller.handle_scan_finished(recs, False, {})

def export_csv(app):
    if not app.model: return
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    from pathlib import Path
    last_tgt = app.settings.value("last_target", "")
    default_dir = str(last_tgt) if last_tgt and Path(last_tgt).exists() else str(Path.home())
    path, _ = QFileDialog.getSaveFileName(app, "Export CSV", default_dir, "CSV Files (*.csv)")
    if path:
        try:
            exported = app.data_manager.export_to_csv(path, app.model.records)
        except Exception as exc:
            QMessageBox.warning(app, "Export CSV", f"Could not export CSV: {exc}")
            return
        if exported:
            QMessageBox.information(app, "Success", "Exported successfully.")


def _set_zoom(app, zoom_percent: int) -> None:
    app.settings_controller.set_zoom_percent(zoom_percent)
    app.apply_zoom(zoom_percent)


def _set_theme(app, theme_key: str) -> None:
    app.settings_controller.set_theme_key(theme_key)
    app.apply_theme(theme_key)


def _set_show_non_audio_assets(app, checked: bool) -> None:
    if getattr(app, "proxy_model", None) is not None:
        app.proxy_model.set_show_non_audio_assets(checked)
    app.view_controller.update_library_views(tree_delay_ms=0)
