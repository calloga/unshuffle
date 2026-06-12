from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from unshuffle.core import PlanRecord
from unshuffle.logic.execution.destination import DestinationContainmentError, DefaultDestinationResolver, DestinationResolver
from unshuffle.logic.tree_organization import (
    FilterEvaluator,
    TreeOrganizationNode,
    TreeOrganizationProfile,
    TreeOrganizationProfileStoreError,
    TreeOrganizationRepository,
    TreeOrganizationResolver,
    TreeRouteBuilder,
)
from unshuffle.logic.tree_organization.filter_evaluator import parse_query_groups as parse_tree_filter_groups
from gui.core.search_engine import SearchEngine


def _record(name: str, *, pack="Pack", category="Bass", subcategory="Sub", audio_type="Oneshots", tags=None, row_id=1):
    return PlanRecord(
        Path(f"D:/Samples/{pack}/{name}"),
        pack,
        category,
        audio_type,
        "0.9",
        subcategory=subcategory,
        tags=list(tags or []),
        staging_row_id=row_id,
    )


def _profile(nodes):
    root = TreeOrganizationNode("root", None, "Root", None, "system", 0, True)
    return TreeOrganizationProfile("p1", "Custom", "root", [root, *nodes], "now", "now")


def test_default_destination_resolver_preserves_normal_flat_and_non_audio_paths():
    resolver = DefaultDestinationResolver()
    target = Path("D:/Out")

    normal = resolver.resolve(_record("kick.wav", pack="A Pack", category="Kicks", subcategory="Hard"), target, False, False, {})
    assert normal.relative_path == Path("Oneshots") / "Kicks" / "Hard" / "A Pack" / "kick.wav"

    prefix_map = {}
    flat = resolver.resolve(_record("kick.wav", pack="A Pack", category="Kicks", subcategory="Hard"), target, True, False, prefix_map)
    assert flat.dest_folder == target / "Oneshots" / "Kicks" / "Hard"
    assert flat.final_name.endswith("kick.wav")

    non_audio = resolver.resolve(_record("cover.jpg", pack="A Pack", audio_type="Non-Audio Assets"), target, False, False, {})
    assert non_audio.relative_path == Path("Non-Audio Assets") / "A Pack" / "cover.jpg"


def test_filter_evaluator_matches_existing_saved_filter_shapes():
    rec = _record("warm_kick.wav", pack="Aden Pack", category="Kicks", tags=["warm", "dry"])
    evaluator = FilterEvaluator()
    assert evaluator.matches(rec, 'pack:"Aden", tag:"warm"')
    assert evaluator.matches(rec, 'source:"D:/Samples/Aden Pack"')
    assert not evaluator.matches(rec, 'cat:"Bass"')


def test_tree_filter_and_library_search_parse_and_or_the_same_way():
    queries = [
        'category:"Kicks" AND tag:"warm"',
        'category:"Kicks", tag:"warm"',
        'category:"Kicks" & tag:"warm"',
        'category:"Kicks" OR category:"Snares"',
        'category:"Kicks" | category:"Snares"',
        'category:"Kicks" AND tag:"warm" OR category:"Snares"',
    ]

    for query in queries:
        assert parse_tree_filter_groups(query) == SearchEngine.parse_query_groups(query)


def test_filter_evaluator_explicit_and_or_semantics_match_library_filter_language():
    warm_kick = _record("warm_kick.wav", category="Kicks", tags=["warm"], row_id=1)
    dry_snare = _record("dry_snare.wav", category="Snares", tags=["dry"], row_id=2)
    evaluator = FilterEvaluator()

    assert evaluator.matches(warm_kick, 'category:"Kicks" AND tag:"warm"')
    assert not evaluator.matches(dry_snare, 'category:"Kicks" AND tag:"warm"')
    assert evaluator.matches(warm_kick, 'category:"Kicks" OR category:"Snares"')
    assert evaluator.matches(dry_snare, 'category:"Kicks" OR category:"Snares"')


def test_custom_routing_child_inheritance_and_fallback():
    records = [
        _record("kick.wav", category="Kicks", tags=["aden"], row_id=1),
        _record("bass.wav", category="Bass", tags=["other"], row_id=2),
    ]
    profile = _profile(
        [
            TreeOrganizationNode("aden", "root", "Aden", 'tag:"aden"', "custom", 1),
            TreeOrganizationNode("kicks", "aden", "Kicks", 'cat:"Kicks"', "custom", 1),
            TreeOrganizationNode("fallback", "root", "Other", None, "fallback", 99),
        ]
    )
    resolver = TreeOrganizationResolver()
    assert resolver.validate_profile(profile, records).valid
    assert resolver.resolve_record(records[0], profile, records) == Path("Aden") / "Kicks"
    assert resolver.resolve_record(records[1], profile, records) == Path("Other")


def test_route_builder_default_routes_match_native_tree_levels():
    rec = _record("kick.wav", pack="A Pack", category="Kicks", subcategory="Hard", audio_type="Oneshots")
    route = TreeRouteBuilder().routes_for([rec])[0]
    assert [(part.kind, part.label, part.fields) for part in route.parts] == [
        ("type", "Oneshots", {"audio_type": "Oneshots"}),
        ("category", "Kicks", {"category": "Kicks"}),
        ("subcategory", "Hard", {"subcategory": "Hard"}),
        ("pack", "A Pack", {"pack": "A Pack"}),
    ]


def test_route_builder_exact_custom_filters_consume_duplicate_native_levels():
    rec = _record("kick.wav", pack="A Pack", category="Kicks", subcategory="Hard", audio_type="Oneshots")
    profile = _profile(
        [
            TreeOrganizationNode("oneshots", "root", "Oneshots", 'type:"Oneshots"', "system", 1),
            TreeOrganizationNode("kicks", "oneshots", "Kicks", 'cat:"Kicks"', "system", 1),
        ]
    )
    route = TreeRouteBuilder().routes_for([rec], profile)[0]
    assert [part.label for part in route.parts] == ["Oneshots", "Kicks", "Hard", "A Pack"]


def test_route_builder_materializes_arbitrary_custom_filters_as_filter_parts():
    rec = _record("warm.wav", category="Kicks", tags=["warm"])
    profile = _profile([TreeOrganizationNode("warm", "root", "Warm", 'tag:"warm"', "custom", 1)])
    route = TreeRouteBuilder().routes_for([rec], profile)[0]
    assert route.parts[0].label == "Warm"
    assert route.parts[0].kind == "filter"
    assert route.parts[0].source_node_id == "warm"


def test_custom_sibling_overlap_is_rejected():
    records = [_record("kick.wav", category="Kicks", tags=["warm"], row_id=1)]
    profile = _profile(
        [
            TreeOrganizationNode("a", "root", "A", 'tag:"warm"', "custom", 1),
            TreeOrganizationNode("b", "root", "B", 'cat:"Kicks"', "custom", 2),
        ]
    )
    result = TreeOrganizationResolver().validate_profile(profile, records)
    assert not result.valid
    assert "overlap" in result.blocking_messages[0]


def test_custom_sibling_sanitized_destination_collision_is_rejected():
    records = [_record("kick.wav", category="Kicks", row_id=1)]
    profile = _profile(
        [
            TreeOrganizationNode("a", "root", "A/B", 'cat:"Kicks"', "custom", 1),
            TreeOrganizationNode("b", "root", "A?B", 'cat:"Snares"', "custom", 2),
        ]
    )

    result = TreeOrganizationResolver().validate_profile(profile, records)

    assert not result.valid
    assert "same folder name" in result.blocking_messages[0]


def test_destination_resolver_uses_custom_folder_but_keeps_factual_filename():
    rec = _record("kick.wav", category="Kicks", tags=["aden"])
    profile = _profile([TreeOrganizationNode("aden", "root", "Aden Packs", 'tag:"aden"', "custom", 1, enabled=True, hide_subbranches=True)])
    resolution = DestinationResolver().resolve(
        rec,
        Path("D:/Out"),
        False,
        False,
        {},
        active_tree_profile=profile,
        records=[rec],
    )
    assert resolution.used_custom_tree
    assert resolution.relative_path == Path("Aden Packs") / "kick.wav"
    assert rec.category == "Kicks"


def test_destination_resolver_collapses_display_only_other_subcategory_for_custom_tree():
    rec = _record("bass.wav", category="Bass", subcategory="", audio_type="Loops", pack="Pack A", tags=["deep"])
    profile = _profile([TreeOrganizationNode("deep", "root", "Deep", 'tag:"deep"', "custom", 1)])

    resolution = DestinationResolver().resolve(
        rec,
        Path("D:/Out"),
        False,
        False,
        {},
        active_tree_profile=profile,
        records=[rec],
    )

    assert resolution.relative_path == Path("Deep") / "Loops" / "Bass" / "Pack A" / "bass.wav"
    assert "Other" not in resolution.relative_path.parts


def test_destination_resolver_keeps_utility_presentation_out_of_build_paths():
    rec = _record("cover.jpg", pack="Pack A", category="Non-Audio Assets", audio_type="Non-Audio Assets")
    profile = _profile([TreeOrganizationNode("utility", "root", "Utility", 'type:"Non-Audio Assets"', "system", 1)])

    resolution = DestinationResolver().resolve(
        rec,
        Path("D:/Out"),
        False,
        False,
        {},
        active_tree_profile=profile,
        records=[rec],
    )

    assert resolution.relative_path == Path("Non-Audio Assets") / "Pack A" / "cover.jpg"


def test_build_compare_shows_flat_options_for_custom_tree(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.build_page import BuildPage

    class FakeSettings:
        def __init__(self):
            self.values = {
                "exec_move": False,
                "exec_flat": True,
                "exec_no_px": True,
                "last_target": "D:/Out",
            }

        def value(self, key, default=None, type=None):
            value = self.values.get(key, default)
            if type is bool:
                return bool(value)
            return value

        def setValue(self, key, value):
            self.values[key] = value

    _app = QApplication.instance() or QApplication([])
    rec = _record("kick.wav", category="Kicks", tags=["aden"])
    profile = _profile([TreeOrganizationNode("aden", "root", "Aden Packs", 'tag:"aden"', "custom", 1)])

    dialog = BuildPage(FakeSettings(), [rec], [], active_tree_profile=profile)

    assert dialog.edit_target.text() == "D:/Out"
    assert not dialog.check_flat.isHidden()
    assert not dialog.check_no_px.isHidden()
    assert dialog.get_options()["flat"] is True
    assert dialog.get_options()["no_px"] is True
    assert "custom tree + flat native levels" in dialog.after_footer.text()
    assert "strip pack prefixes" in dialog.after_footer.text()
    assert "Structure:" not in dialog.after_footer.text()
    assert "Filename handling:" not in dialog.after_footer.text()


def test_build_compare_preview_keeps_preserved_utility_out_of_build_paths(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.build_page import BuildPage

    class FakeSettings:
        def __init__(self):
            self.values = {
                "exec_move": False,
                "exec_flat": False,
                "exec_no_px": False,
                "last_target": "D:/Out",
            }

        def value(self, key, default=None, type=None):
            value = self.values.get(key, default)
            if type is bool:
                return bool(value)
            return value

        def setValue(self, key, value):
            self.values[key] = value

    _app = QApplication.instance() or QApplication([])
    rec = _record("file.wav", pack="HANDSOFF", category="Preserved", subcategory="", audio_type="Utility")
    rec.source_path = Path("D:/Samples/HANDSOFF")
    rec.is_preserved = True
    rec.preserved_root = Path("D:/Samples/HANDSOFF")

    dialog = BuildPage(FakeSettings(), [rec], [Path("D:/Samples")])

    assert dialog._projected_relative_path(rec) == Path("HANDSOFF")


def test_build_compare_preview_uses_execution_destination_resolver_for_custom_tree(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.build_page import BuildPage

    class FakeSettings:
        def __init__(self):
            self.values = {
                "exec_move": False,
                "exec_flat": True,
                "exec_no_px": False,
                "last_target": "D:/Out",
            }

        def value(self, key, default=None, type=None):
            value = self.values.get(key, default)
            if type is bool:
                return bool(value)
            return value

        def setValue(self, key, value):
            self.values[key] = value

    _app = QApplication.instance() or QApplication([])
    rec = _record("kick.wav", pack="A Pack", category="Kicks", subcategory="Hard", audio_type="Oneshots", tags=["aden"])
    profile = _profile([TreeOrganizationNode("aden", "root", "Aden Packs", 'tag:"aden"', "custom", 1)])
    dialog = BuildPage(FakeSettings(), [rec], [], active_tree_profile=profile)
    expected = DestinationResolver().resolve(
        rec,
        Path("D:/Out"),
        True,
        False,
        {},
        active_tree_profile=profile,
        records=[rec],
    ).relative_path

    assert dialog._projected_relative_path(rec) == expected


def test_build_compare_default_preview_avoids_build_destination_resolver(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    import gui.widgets.build_page as build_page_module
    from gui.widgets.build_page import BuildPage

    class FakeSettings:
        def __init__(self):
            self.values = {
                "exec_move": False,
                "exec_flat": False,
                "exec_no_px": False,
                "last_target": "D:/Out",
            }

        def value(self, key, default=None, type=None):
            value = self.values.get(key, default)
            if type is bool:
                return bool(value)
            return value

        def setValue(self, key, value):
            self.values[key] = value

    class RaisingDestinationResolver:
        def __init__(self, *args, **kwargs):
            pass

        def resolve(self, *args, **kwargs):
            raise AssertionError("default preview should not use build-time destination resolution")

    _app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(build_page_module, "DestinationResolver", RaisingDestinationResolver)
    rec = _record("kick.wav", pack="A Pack", category="Kicks", subcategory="Hard", audio_type="Oneshots")

    dialog = BuildPage(FakeSettings(), [rec], [])

    assert dialog._projected_tree_records()[0][0] == Path("Oneshots") / "Kicks" / "Hard" / "A Pack" / "kick.wav"
    dialog.close()


def test_build_compare_option_refresh_does_not_rebuild_source_preview(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QHeaderView, QTreeWidget
    from gui.widgets.build_page import BuildPage

    class FakeSettings:
        def __init__(self):
            self.values = {
                "exec_move": False,
                "exec_flat": False,
                "exec_no_px": False,
                "last_target": "D:/Out",
            }

        def value(self, key, default=None, type=None):
            value = self.values.get(key, default)
            if type is bool:
                return bool(value)
            return value

        def setValue(self, key, value):
            self.values[key] = value

    _app = QApplication.instance() or QApplication([])
    dialog = BuildPage(FakeSettings(), [_record("kick.wav")], [Path("D:/Samples")])
    trees = dialog.findChildren(QTreeWidget)
    assert trees
    assert all(tree.layoutDirection() == Qt.LeftToRight for tree in trees)
    assert all(tree.header().sectionResizeMode(1) == QHeaderView.Fixed for tree in trees)
    calls = {"before": 0}

    def count_before():
        calls["before"] += 1
        raise AssertionError("source preview should not rebuild for option-only refreshes")

    monkeypatch.setattr(dialog, "_build_before_panel", count_before)

    dialog._refresh_compare_views(refresh_before=False)

    assert calls["before"] == 0


def test_build_compare_target_change_rebuilds_projected_preview_with_stable_row_height(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QTreeWidget
    from gui.widgets.build_page import BuildPage

    class FakeSettings:
        def __init__(self):
            self.values = {
                "exec_move": False,
                "exec_flat": False,
                "exec_no_px": False,
                "last_target": "D:/Out",
            }

        def value(self, key, default=None, type=None):
            value = self.values.get(key, default)
            if type is bool:
                return bool(value)
            return value

        def setValue(self, key, value):
            self.values[key] = value

    app = QApplication.instance() or QApplication([])
    dialog = BuildPage(FakeSettings(), [_record("kick.wav")], [Path("D:/Samples")])
    try:
        target_tree = dialog.after_panel.layout().itemAt(1).widget()
        assert isinstance(target_tree, QTreeWidget)
        target_root = target_tree.topLevelItem(0)
        row_height = target_root.sizeHint(0).height()
        assert target_root.text(0) == "Out"

        dialog.edit_target.setText("D:/Music/test")
        app.processEvents()

        rebuilt_tree = dialog.after_panel.layout().itemAt(1).widget()
        assert isinstance(rebuilt_tree, QTreeWidget)
        rebuilt_root = rebuilt_tree.topLevelItem(0)
        assert rebuilt_root.text(0) == "test"
        assert rebuilt_root.sizeHint(0).height() == row_height
        assert dialog.target_dir == Path("D:/Music/test")
    finally:
        dialog.close()


def test_build_compare_browse_defaults_to_source_root(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QFileDialog
    from gui.widgets.build_page import BuildPage

    class FakeSettings:
        def __init__(self):
            self.values = {
                "exec_move": False,
                "exec_flat": False,
                "exec_no_px": False,
                "last_target": "",
            }

        def value(self, key, default=None, type=None):
            value = self.values.get(key, default)
            if type is bool:
                return bool(value)
            return value

        def setValue(self, key, value):
            self.values[key] = value

    _app = QApplication.instance() or QApplication([])
    source = tmp_path / "source"
    chosen = tmp_path / "target"
    source.mkdir()
    chosen.mkdir()
    starts = []

    def fake_browse(parent, title, start_dir):
        starts.append((parent, title, start_dir))
        return str(chosen)

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", fake_browse)
    dialog = BuildPage(FakeSettings(), [_record("kick.wav")], [source])

    dialog._browse_target()

    assert starts
    assert starts[0][2] == str(source.resolve())
    assert dialog.edit_target.text() == str(chosen)
    dialog.close()


def test_build_compare_target_preview_caps_visible_file_rows(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QTreeWidget
    from gui.widgets.build_page import BuildPage, MAX_COMPARE_FILE_ITEMS

    class FakeSettings:
        def __init__(self):
            self.values = {
                "exec_move": False,
                "exec_flat": True,
                "exec_no_px": True,
                "last_target": "D:/Out",
            }

        def value(self, key, default=None, type=None):
            value = self.values.get(key, default)
            if type is bool:
                return bool(value)
            return value

        def setValue(self, key, value):
            self.values[key] = value

    def walk(item):
        yield item
        for index in range(item.childCount()):
            yield from walk(item.child(index))

    _app = QApplication.instance() or QApplication([])
    records = [
        _record(f"sample_{index:04}.wav", category=f"Category {index}", row_id=index)
        for index in range(MAX_COMPARE_FILE_ITEMS + 25)
    ]

    dialog = BuildPage(FakeSettings(), records, [])
    target_tree = next(tree for tree in dialog.findChildren(QTreeWidget) if tree.property("compareTone") == "target")
    target_root = target_tree.topLevelItem(0)
    visible_files = [item for item in walk(target_root) if item.text(1) == "file"]
    capped_markers = [item for item in walk(target_root) if "more file(s)" in item.text(1)]

    assert len(visible_files) == MAX_COMPARE_FILE_ITEMS
    assert capped_markers
    assert "preview capped" in target_root.text(1)
    dialog.close()


def test_build_compare_blocks_in_place_target_inline(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.build_page import BuildPage

    class FakeSettings:
        def __init__(self):
            self.values = {
                "exec_move": False,
                "exec_flat": False,
                "exec_no_px": False,
                "last_target": "",
            }

        def value(self, key, default=None, type=None):
            value = self.values.get(key, default)
            if type is bool:
                return bool(value)
            return value

        def setValue(self, key, value):
            self.values[key] = value

    _app = QApplication.instance() or QApplication([])
    source = tmp_path / "source"
    nested_target = source / "built"
    outside_target = tmp_path / "target"
    source.mkdir()
    nested_target.mkdir()
    outside_target.mkdir()

    dialog = BuildPage(FakeSettings(), [_record("kick.wav")], [source])

    assert "QPushButton:disabled" in dialog.btn_build.styleSheet()
    assert dialog.target_error.text() == "Select a target directory."
    assert not dialog.btn_build.isEnabled()

    dialog.edit_target.setText(str(source))
    assert dialog.target_error.text() == "Target must be different from source."
    assert not dialog.btn_build.isEnabled()

    dialog.edit_target.setText(str(nested_target))
    assert dialog.target_error.text() == "Target must be different from source."
    assert not dialog.btn_build.isEnabled()

    dialog.edit_target.setText(str(tmp_path))
    assert dialog.target_error.text() == "Target must be different from source."
    assert not dialog.btn_build.isEnabled()

    dialog.edit_target.setText("")
    assert dialog.target_error.text() == "Select a target directory."
    assert not dialog.btn_build.isEnabled()

    dialog.edit_target.setText(str(outside_target))
    assert dialog.target_error.text() == " "
    assert dialog.btn_build.isEnabled()
    dialog.close()


def test_destination_resolver_refuses_custom_tree_escape():
    class _EscapingTreeResolver:
        def resolve_record(self, *_args, **_kwargs):
            return Path("..") / "outside"

    rec = _record("kick.wav")
    with pytest.raises(DestinationContainmentError):
        DestinationResolver(tree_resolver=cast(TreeOrganizationResolver, _EscapingTreeResolver())).resolve(
            rec,
            Path("D:/Out"),
            False,
            False,
            {},
            active_tree_profile=_profile([]),
            records=[rec],
        )


def test_active_profile_without_matching_child_can_route_to_target_root():
    rec = _record("kick.wav", category="Kicks", tags=["plain"])
    profile = _profile([TreeOrganizationNode("aden", "root", "Aden Packs", 'tag:"aden"', "custom", 1)])
    relative = TreeOrganizationResolver().resolve_record(rec, profile, [rec])
    assert relative == Path(".")


def test_repository_persists_profiles_without_auto_activation(tmp_path):
    repo = TreeOrganizationRepository(tmp_path / "profiles.json")
    created = repo.create_profile("Mine")
    assert repo.get_profile(created.id).name == "Mine"
    repo.delete_profile(created.id)
    assert repo.get_profile(created.id) is None


def test_repository_refuses_to_overwrite_corrupt_profile_store(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text("{not json", encoding="utf-8")
    repo = TreeOrganizationRepository(path)

    with pytest.raises(TreeOrganizationProfileStoreError):
        repo.save_profile(_profile([]))

    assert path.read_text(encoding="utf-8") == "{not json"
    backups = list(tmp_path.glob("profiles.json.corrupt-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{not json"


def test_repository_skips_malformed_profile_entries_but_keeps_valid_profiles(tmp_path):
    path = tmp_path / "profiles.json"
    valid = _profile([TreeOrganizationNode("dupes", "root", "Dupes", 'tag:"dupe"', "custom", 1)])
    payload = {
        "version": 1,
        "profiles": [
            valid.to_dict(),
            {
                "id": "bad",
                "name": "Bad",
                "root_node_id": "root",
                "nodes": [{"id": "bad_node", "sort_order": "not-an-int"}],
            },
        ],
    }
    import json

    path.write_text(json.dumps(payload), encoding="utf-8")

    profiles = TreeOrganizationRepository(path).list_profiles()

    assert [profile.id for profile in profiles] == [valid.id]


def test_first_edit_profile_mirrors_current_tree_and_allows_custom_override():
    from gui.core.tree_organization_controller import TreeOrganizationController
    from gui.models.library_tree import LibraryTreeModel
    from PySide6.QtCore import QObject

    class FakeLibraryTab:
        def __init__(self):
            self.tree_model = LibraryTreeModel()

    class FakeApp(QObject):
        def __init__(self):
            super().__init__()
            self.library_tab = FakeLibraryTab()

    records = [
        _record("kick.wav", pack="Aden Pack", category="Kicks", tags=["aden"], row_id=1),
        _record("bass.wav", pack="Plain Pack", category="Bass", tags=[], row_id=2),
    ]
    controller = TreeOrganizationController(FakeApp())
    profile = controller._profile_from_current_tree(records)
    assert profile.name == "Default"
    assert any(node.name == "Oneshots" and node.node_type == "system" for node in profile.nodes)
    assert any(node.name == "Kicks" and node.node_type == "system" for node in profile.nodes)
    assert not any(node.filter_query and node.filter_query.startswith('pack:') for node in profile.nodes)
    assert not any(node.name in {"Aden Pack", "Plain Pack"} for node in profile.nodes)

    profile = TreeOrganizationProfile(
        profile.id,
        profile.name,
        profile.root_node_id,
        [
            *profile.nodes,
            TreeOrganizationNode("aden_custom", "root", "Aden Packs", 'tag:"aden"', "custom", 0),
        ],
        profile.created_at,
        profile.updated_at,
    )
    resolver = TreeOrganizationResolver()
    assert resolver.validate_profile(profile, records).valid
    assert resolver.resolve_record(records[0], profile, records) == Path("Aden Packs")
    assert resolver.resolve_record(records[1], profile, records).parts[0] == "Oneshots"


def test_default_profile_opens_as_editable_copy():
    from gui.core.tree_organization_controller import TreeOrganizationController
    from gui.models.library_tree import LibraryTreeModel
    from PySide6.QtCore import QObject

    class FakeLibraryTab:
        def __init__(self):
            self.tree_model = LibraryTreeModel()

    class FakeApp(QObject):
        def __init__(self):
            super().__init__()
            self.library_tab = FakeLibraryTab()

    controller = TreeOrganizationController(FakeApp())
    default = controller._profile_from_current_tree([_record("kick.wav", category="Kicks")])
    editable = controller._editable_profile_from_default([_record("kick.wav", category="Kicks")])

    assert default.name == "Default"
    assert editable.name == "Custom Tree"
    assert editable.id != default.id
    assert editable.nodes == default.nodes


def test_default_copy_always_routes_non_audio_assets_to_utility():
    from gui.core.tree_organization_controller import TreeOrganizationController
    from gui.models.library_tree import LibraryTreeModel
    from PySide6.QtCore import QObject

    class FakeLibraryTab:
        def __init__(self):
            self.tree_model = LibraryTreeModel()

    class FakeApp(QObject):
        def __init__(self):
            super().__init__()
            self.library_tab = FakeLibraryTab()

    controller = TreeOrganizationController(FakeApp())
    profile = controller._editable_profile_from_default([_record("kick.wav", category="Kicks")])
    utility = _record("cover.jpg", audio_type="Non-Audio Assets", row_id=2)

    assert any(node.name == "Utility" and node.filter_query == 'type:"Non-Audio Assets"' for node in profile.nodes)
    assert TreeOrganizationResolver().resolve_record(utility, profile, [utility]) == Path("Utility")


def test_tree_organization_sync_can_skip_immediate_refresh(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from gui.core.tree_organization_controller import TreeOrganizationController
    from gui.models.library_tree import LibraryTreeModel

    _app = QApplication.instance() or QApplication([])

    class FakeLibraryTab:
        def __init__(self):
            self.tree_model = LibraryTreeModel()
            self.states = []

        def set_tree_organization_state(self, active, profile_name=""):
            self.states.append((active, profile_name))

    class FakeViewController:
        def __init__(self):
            self.refresh_calls = []

        def update_library_views(self, tree_delay_ms=100):
            self.refresh_calls.append(tree_delay_ms)

    class FakeApp(QObject):
        def __init__(self):
            super().__init__()
            self.library_tab = FakeLibraryTab()
            self.view_controller = FakeViewController()
            self.engine = None

    app = FakeApp()
    controller = TreeOrganizationController(app)
    controller.active_profile = _profile([TreeOrganizationNode("dupes", "root", "Dupes", 'tag:"dupe"', "custom", 1)])

    controller._sync_active_profile(refresh=False)
    assert app.library_tab.states == [(True, "Custom")]
    assert app.view_controller.refresh_calls == []

    controller._sync_active_profile()
    assert app.view_controller.refresh_calls == [0]


def test_tree_organization_controller_restores_persisted_active_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from gui.core import tree_organization_controller as tree_org_module
    from gui.core.tree_organization_controller import ACTIVE_PROFILE_ID_KEY, TreeOrganizationController

    _app = QApplication.instance() or QApplication([])
    repo = TreeOrganizationRepository(tmp_path / "profiles.json")
    profile = repo.save_profile(_profile([TreeOrganizationNode("dupes", "root", "Dupes", 'tag:"dupe"', "custom", 1)]))
    monkeypatch.setattr(tree_org_module, "TreeOrganizationRepository", lambda: repo)

    class FakeSettings:
        def __init__(self):
            self.values = {ACTIVE_PROFILE_ID_KEY: profile.id}

        def value(self, key, default=None):
            return self.values.get(key, default)

        def setValue(self, key, value):
            self.values[key] = value

        def remove(self, key):
            self.values.pop(key, None)

    class FakeApp(QObject):
        def __init__(self):
            super().__init__()
            self.settings = FakeSettings()

    app = FakeApp()
    controller = TreeOrganizationController(app)

    assert controller.active_profile is not None
    assert controller.active_profile.id == profile.id

    controller.disable_profile()
    assert ACTIVE_PROFILE_ID_KEY not in app.settings.values


def test_tree_organization_controller_opens_profile_list_for_active_tree(monkeypatch):
    from PySide6.QtCore import QObject
    from gui.core.tree_organization_controller import TreeOrganizationController
    from gui.widgets import tree_organization as tree_org_widgets

    events = []

    class FakeSignal:
        def connect(self, _callback):
            pass

    class FakeEditor:
        profileSaved = FakeSignal()
        profileApplied = FakeSignal()
        profileDeleted = FakeSignal()
        profileDisabled = FakeSignal()

        def __init__(self, _profiles, _profile, _records, _parent, *, embedded=False):
            self.embedded = embedded

        def open_current_profile_editor(self):
            events.append("opened-editor")

        def show_profile_list(self):
            events.append("show-list")

    monkeypatch.setattr(tree_org_widgets, "TreeOrganizationEditor", FakeEditor)

    class FakeSystemPage:
        def __init__(self):
            self.panels = []

        def set_tree_organization_panel(self, panel):
            self.panels.append(panel)

    class FakeApp(QObject):
        def __init__(self):
            super().__init__()
            self.model = SimpleNamespace(records=[_record("kick.wav", category="Kicks")])
            self.system_page = FakeSystemPage()
            self.opened_sections = []

        def open_system_workspace(self, section=None):
            self.opened_sections.append(section)

    app = FakeApp()
    controller = TreeOrganizationController(app)
    controller.active_profile = _profile([TreeOrganizationNode("kicks", "root", "Kicks", 'cat:"Kicks"', "system", 1)])

    controller.open_editor()

    assert "opened-editor" not in events
    assert app.system_page.panels == [controller.editor_widget]
    assert app.opened_sections == ["tree_organization"]

    controller.editor_widget.show_profile_list()
    assert events == ["show-list"]


def test_custom_tree_items_expose_normal_reorganization_fields(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.models.library_tree import FIELDS_ROLE, LibraryTreeModel, RAW_NAME_ROLE

    _app = QApplication.instance() or QApplication([])
    model = LibraryTreeModel()
    profile = _profile(
        [
            TreeOrganizationNode("oneshots", "root", "Oneshots", 'type:"Oneshots"', "system", 1),
            TreeOrganizationNode("kicks", "oneshots", "Kicks", 'cat:"Kicks"', "system", 1),
            TreeOrganizationNode("hard", "kicks", "Hard", 'sub:"Hard"', "system", 1),
        ]
    )
    model.set_custom_tree_profile(profile)
    model.rebuild([_record("kick.wav", category="Kicks", subcategory="Hard")])

    def find_item(parent, name):
        for row in range(parent.rowCount()):
            item = parent.child(row, 0)
            if item.data(RAW_NAME_ROLE) == name:
                return item
            nested = find_item(item, name)
            if nested is not None:
                return nested
        return None

    kicks = find_item(model.invisibleRootItem(), "Kicks")
    hard = find_item(model.invisibleRootItem(), "Hard")

    assert kicks is not None
    assert kicks.data(FIELDS_ROLE) == {
        "audio_type": "Oneshots",
        "category": "Kicks",
        "custom_path": "Oneshots/Kicks",
    }
    assert hard is not None
    assert hard.data(FIELDS_ROLE) == {
        "audio_type": "Oneshots",
        "category": "Kicks",
        "subcategory": "Hard",
        "custom_path": "Oneshots/Kicks/Hard",
    }


def test_utility_tree_row_is_read_only_but_children_keep_actions(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.models.library_tree import FIELDS_ROLE, LibraryTreeModel, RAW_NAME_ROLE
    from gui.views.library_tree import LibraryTreeView

    _app = QApplication.instance() or QApplication([])
    model = LibraryTreeModel()
    model.rebuild([_record("cover.jpg", audio_type="Non-Audio Assets", category="Docs", subcategory="Art")])
    view = LibraryTreeView()
    view.setModel(model)

    def find_item(parent, name):
        for row in range(parent.rowCount()):
            item = parent.child(row, 0)
            if item.data(RAW_NAME_ROLE) == name:
                return item
            nested = find_item(item, name)
            if nested is not None:
                return nested
        return None

    utility = find_item(model.invisibleRootItem(), "Utility")
    docs = find_item(model.invisibleRootItem(), "Docs")

    assert utility is not None
    utility_index = model.indexFromItem(utility)
    assert view._drop_target_fields(utility_index) is None
    assert view._quick_filter_query_for_index(utility_index) == ""

    assert docs is not None
    docs_index = model.indexFromItem(docs)
    assert view._drop_target_fields(docs_index) == {"audio_type": "Utility", "category": "Docs"}
    assert docs.data(FIELDS_ROLE) == {"audio_type": "Utility", "category": "Docs"}
    assert view._quick_filter_query_for_index(docs_index) == 'cat:"Docs"'


def test_custom_tree_routes_unmatched_utility_to_utility_not_root_or_other(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.models.library_tree import LibraryTreeModel, RAW_NAME_ROLE

    _app = QApplication.instance() or QApplication([])
    model = LibraryTreeModel()
    profile = _profile([TreeOrganizationNode("dupes", "root", "Dupes", 'tag:"dupe"', "custom", 1)])
    model.set_custom_tree_profile(profile)
    model.rebuild([_record("cover.jpg", audio_type="Non-Audio Assets", category="Docs", row_id=2)])
    root_labels = {
        str(model.invisibleRootItem().child(row, 0).data(RAW_NAME_ROLE))
        for row in range(model.invisibleRootItem().rowCount())
    }

    assert "Utility" in root_labels
    assert "Root" not in root_labels
    assert "Other" not in root_labels


def test_custom_tree_routes_unmatched_audio_to_other_not_root(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.models.library_tree import LibraryTreeModel, RAW_NAME_ROLE

    _app = QApplication.instance() or QApplication([])
    model = LibraryTreeModel()
    profile = _profile([TreeOrganizationNode("dupes", "root", "Dupes", 'tag:"dupe"', "custom", 1)])
    model.set_custom_tree_profile(profile)
    model.rebuild([_record("kick.wav", category="Kicks", tags=[], row_id=1)])
    root_labels = {
        str(model.invisibleRootItem().child(row, 0).data(RAW_NAME_ROLE))
        for row in range(model.invisibleRootItem().rowCount())
    }

    assert "Other" in root_labels
    assert "Root" not in root_labels


def test_custom_presentation_bucket_preserves_semantic_children_without_duplicate_levels(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.models.library_tree import LibraryTreeModel
    from gui.views.library_tree import LibraryTreeView

    _app = QApplication.instance() or QApplication([])
    model = LibraryTreeModel()
    profile = _profile([TreeOrganizationNode("warm", "root", "Warm", 'tag:"warm"', "custom", 1)])
    model.set_custom_tree_profile(profile)
    model.rebuild([_record("kick.wav", category="Kicks", subcategory="Hard", tags=["warm"])])
    view = LibraryTreeView()
    view.setModel(model)

    assert model.index_for_path(("Warm", "Oneshots", "Kicks", "Hard", "Pack")) is not None


def test_tree_drop_bucket_aware_fields_skip_invalid_oneshot_category(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.models.library_tree import LibraryTreeModel
    from gui.views.library_tree import LibraryTreeView

    _app = QApplication.instance() or QApplication([])
    model = LibraryTreeModel()
    view = LibraryTreeView()
    view.setModel(model)

    target = {"audio_type": "Oneshots"}

    assert view._bucket_aware_drop_fields(
        {"node_type": "category", "name": "Bass"},
        target,
        "type",
    ) == {"audio_type": "Oneshots", "category": "Bass"}
    assert view._bucket_aware_drop_fields(
        {"node_type": "category", "name": "Full Drums"},
        target,
        "type",
    ) == {"audio_type": "Oneshots", "category": "Uncategorized"}
    assert view._bucket_aware_drop_fields(
        {"node_type": "type", "name": "Loops"},
        target,
        "type",
    ) == {"audio_type": "Oneshots"}


def test_custom_exact_category_can_drag_into_exact_type(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.models.library_tree import LibraryTreeModel, NODE_TYPE_ROLE, RAW_NAME_ROLE
    from gui.views.library_tree import LibraryTreeView

    _app = QApplication.instance() or QApplication([])
    model = LibraryTreeModel()
    profile = _profile(
        [
            TreeOrganizationNode("oneshots", "root", "Oneshots", 'type:"Oneshots"', "system", 1),
            TreeOrganizationNode("dupes", "root", "Dupes", 'cat:"Dupes"', "custom", 2),
        ]
    )
    model.set_custom_tree_profile(profile)
    model.rebuild(
        [
            _record("dupe.wav", category="Dupes", audio_type="Oneshots", row_id=1),
            _record("kick.wav", category="Kicks", audio_type="Oneshots", row_id=2),
        ]
    )
    view = LibraryTreeView()
    view.setModel(model)
    dupes_index = model.index_for_path(("Dupes",))
    oneshots_index = model.index_for_path(("Oneshots",))

    assert dupes_index is not None
    assert oneshots_index is not None
    assert dupes_index.data(NODE_TYPE_ROLE) == "category"
    assert view._drop_target_fields(oneshots_index) == {"audio_type": "Oneshots"}
    applied = view._folderized_drop_fields(
        {
            "node_type": str(dupes_index.data(NODE_TYPE_ROLE)),
            "name": str(dupes_index.data(RAW_NAME_ROLE)),
        },
        view._drop_target_fields(oneshots_index),
        str(oneshots_index.data(NODE_TYPE_ROLE)),
    )
    assert applied == {"audio_type": "Oneshots", "category": "Dupes"}


def test_custom_label_matching_shared_category_can_drag_into_exact_type(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.models.library_tree import LibraryTreeModel, NODE_TYPE_ROLE, RAW_NAME_ROLE
    from gui.views.library_tree import LibraryTreeView

    _app = QApplication.instance() or QApplication([])
    model = LibraryTreeModel()
    profile = _profile(
        [
            TreeOrganizationNode("dupes", "root", "Dupes", 'tag:"dupe"', "custom", 1),
            TreeOrganizationNode("oneshots", "root", "Oneshots", 'type:"Oneshots"', "system", 2),
        ]
    )
    model.set_custom_tree_profile(profile)
    model.rebuild(
        [
            _record("dupe.wav", category="Dupes", audio_type="Loops", tags=["dupe"], row_id=1),
            _record("kick.wav", category="Kicks", audio_type="Oneshots", tags=[], row_id=2),
        ]
    )
    view = LibraryTreeView()
    view.setModel(model)
    dupes_index = model.index_for_path(("Dupes",))
    oneshots_index = model.index_for_path(("Oneshots",))

    assert dupes_index is not None
    assert oneshots_index is not None
    assert dupes_index.data(NODE_TYPE_ROLE) == "category"
    applied = view._folderized_drop_fields(
        {
            "node_type": str(dupes_index.data(NODE_TYPE_ROLE)),
            "name": str(dupes_index.data(RAW_NAME_ROLE)),
        },
        view._drop_target_fields(oneshots_index),
        str(oneshots_index.data(NODE_TYPE_ROLE)),
    )
    assert applied == {"audio_type": "Oneshots", "category": "Dupes"}


def test_custom_semantic_override_moves_record_out_of_presentation_bucket(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.models.library_tree import LibraryTreeModel
    from gui.models.library_tree_resolution import SEMANTIC_OVERRIDE_ATTR

    _app = QApplication.instance() or QApplication([])
    model = LibraryTreeModel()
    profile = _profile(
        [
            TreeOrganizationNode("dupes", "root", "Dupes", 'tag:"dupe"', "custom", 1),
            TreeOrganizationNode("oneshots", "root", "Oneshots", 'type:"Oneshots"', "system", 2),
        ]
    )
    dupe = _record("dupe.wav", category="Dupes", audio_type="Loops", subcategory="Hard", tags=["dupe"], row_id=1)
    model.set_custom_tree_profile(profile)
    model.rebuild([dupe])
    assert model.index_for_path(("Dupes",)) is not None

    dupe.audio_type = "Oneshots"
    setattr(dupe, SEMANTIC_OVERRIDE_ATTR, True)
    model.rebuild([dupe])

    assert model.index_for_path(("Dupes",)) is None
    assert model.index_for_path(("Oneshots", "Dupes", "Hard", "Pack")) is not None


def test_custom_profile_move_keeps_bucket_under_drop_target(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.models.library_tree import LibraryTreeModel

    _app = QApplication.instance() or QApplication([])
    model = LibraryTreeModel()
    profile = _profile(
        [
            TreeOrganizationNode("dupes", "root", "Dupes", 'tag:"dupe"', "custom", 1),
            TreeOrganizationNode("loops", "root", "Loops", 'type:"Loops"', "system", 2),
            TreeOrganizationNode("bass_node", "loops", "Bass", 'cat:"Bass"', "system", 1),
        ]
    )
    bass = _record("bass.wav", category="Bass", audio_type="Oneshots", subcategory="Hard", tags=["dupe"], row_id=1)
    kick = _record("kick.wav", category="Kicks", audio_type="Oneshots", subcategory="Hard", tags=["dupe"], row_id=2)
    model.set_custom_tree_profile(profile)
    model.rebuild([bass, kick])
    assert model.index_for_path(("Dupes",)) is not None

    for record in (bass, kick):
        record.audio_type = "Loops"
        record.category = "Bass"
    profile = TreeOrganizationProfile(
        profile.id,
        profile.name,
        profile.root_node_id,
        [
            node if node.id != "dupes" else TreeOrganizationNode("dupes", "bass_node", "Dupes", 'tag:"dupe"', "custom", 1)
            for node in profile.nodes
        ],
        profile.created_at,
        profile.updated_at,
    )
    model.set_custom_tree_profile(profile)
    model.rebuild([bass, kick])

    assert model.index_for_path(("Dupes",)) is None
    assert model.index_for_path(("Loops", "Bass", "Dupes", "Hard", "Pack")) is not None


def test_custom_profile_move_avoids_duplicate_consumed_levels(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.models.library_tree import LibraryTreeModel

    _app = QApplication.instance() or QApplication([])
    model = LibraryTreeModel()
    profile = _profile(
        [
            TreeOrganizationNode("loops", "root", "Loops", 'type:"Loops"', "system", 1),
            TreeOrganizationNode("bass_node", "loops", "Bass", 'cat:"Bass"', "system", 1),
            TreeOrganizationNode("dupes", "bass_node", "Dupes", 'tag:"dupe"', "custom", 1),
        ]
    )
    dupe = _record("dupe.wav", category="Bass", audio_type="Loops", subcategory="Hard", tags=["dupe"], row_id=1)
    model.set_custom_tree_profile(profile)
    model.rebuild([dupe])

    assert model.index_for_path(("Loops", "Bass", "Dupes", "Bass")) is None
    assert model.index_for_path(("Loops", "Bass", "Dupes", "Hard", "Pack")) is not None


def test_moved_semantic_node_uses_destination_context_over_own_exact_clause(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.models.library_tree import LibraryTreeModel

    _app = QApplication.instance() or QApplication([])
    model = LibraryTreeModel()
    profile = _profile(
        [
            TreeOrganizationNode("oneshots", "root", "Oneshots", 'type:"Oneshots"', "system", 1),
            TreeOrganizationNode("bass", "oneshots", "Bass", 'cat:"Bass"', "system", 1),
            TreeOrganizationNode("dupes", "bass", "Dupes", 'cat:"Dupes"', "system", 1),
        ]
    )
    dupe = _record("dupe.wav", category="Bass", audio_type="Oneshots", subcategory="Hard", row_id=1)
    model.set_custom_tree_profile(profile)
    model.rebuild([dupe])

    assert TreeOrganizationResolver().resolve_record(dupe, profile, [dupe]) == Path("Oneshots") / "Bass" / "Dupes"
    assert model.index_for_path(("Oneshots", "Bass", "Dupes", "Hard", "Pack")) is not None
    assert model.index_for_path(("Oneshots", "Bass", "Dupes", "Dupes")) is None


def test_tree_model_shows_placeholder_for_empty_filtered_records(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.models.library_tree import LibraryTreeModel, RAW_NAME_ROLE

    _app = QApplication.instance() or QApplication([])
    model = LibraryTreeModel()
    model.rebuild([])

    root = model.invisibleRootItem()
    assert root.rowCount() == 1
    assert root.child(0, 0).data(RAW_NAME_ROLE) == "No matching files"
    assert not root.child(0, 0).isEnabled()


def test_tree_model_hides_internal_unshuffle_system_records(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.models.library_tree import LibraryTreeModel, RAW_NAME_ROLE

    _app = QApplication.instance() or QApplication([])
    visible = _record("kick.wav", category="Kicks", audio_type="Oneshots", row_id=1)
    internal = PlanRecord(
        Path("D:/Samples/DO_NOT_DELETE_unshuffle/trash/unshuffle.log"),
        "System",
        "Non-Audio Assets",
        "Non-Audio Assets",
        "1.0",
        subcategory="",
        staging_row_id=2,
    )
    model = LibraryTreeModel()
    model.rebuild([visible, internal])

    labels = [
        model.invisibleRootItem().child(row, 0).data(RAW_NAME_ROLE)
        for row in range(model.invisibleRootItem().rowCount())
    ]
    assert labels == ["Oneshots"]


def test_proxy_non_audio_toggle_shows_rescanned_assets(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.models.proxy import MultiFilterProxyModel
    from gui.models.staging_table import StagingTableModel

    _app = QApplication.instance() or QApplication([])
    model = StagingTableModel(
        [
            _record("kick.wav", category="Kicks", audio_type="Oneshots", row_id=1),
            _record("LICENSE.pdf", pack="Pack A", category="Non-Audio Assets", audio_type="Non-Audio Assets", row_id=2),
        ],
        undo_stack=None,
        sync_callback=None,
    )
    proxy = MultiFilterProxyModel()
    proxy.setSourceModel(model)

    assert proxy.rowCount() == 1

    proxy.set_show_non_audio_assets(True)

    assert proxy.rowCount() == 2


def test_tree_model_collapses_only_other_subcategory_leaf(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.models.library_tree import LibraryTreeModel, POPULATED_ROLE, RAW_NAME_ROLE
    from gui.utils.constants import StagingColumn

    _app = QApplication.instance() or QApplication([])
    record = _record("bass.wav", category="Bass", subcategory="", row_id=1)
    model = LibraryTreeModel()
    model.set_sort_column(StagingColumn.CATEGORY)
    model.rebuild([record], skip_fields={"audio_type", "pack"})

    root = model.invisibleRootItem()
    bass = root.child(0, 0)

    assert bass.data(RAW_NAME_ROLE) == "Bass"
    assert bass.rowCount() == 1
    assert bass.child(0, 0).data(RAW_NAME_ROLE) != "Other"
    assert bass.data(POPULATED_ROLE) is False


def test_resolved_tree_collapses_only_other_subcategory_leaf():
    from gui.models.library_tree_resolution import build_normal_resolved_tree
    from gui.models.library_tree import build_tree_payload

    record = _record("bass.wav", category="Bass", subcategory="", row_id=1)
    nodes = build_normal_resolved_tree(
        [record],
        [("category", "category"), ("subcategory", "subcategory")],
        lambda records, levels: build_tree_payload(records, levels),
    )

    assert [node.label for node in nodes] == ["Bass"]
    assert nodes[0].children == []


def test_resolved_tree_collapses_custom_profile_only_other_subcategory():
    from gui.models.library_tree_resolution import build_custom_resolved_tree
    from gui.models.library_tree import build_tree_payload

    record = _record("bass.wav", category="Bass", subcategory="", pack="MyPack", audio_type="Loops", row_id=1)
    profile = _profile(
        [
            TreeOrganizationNode("loops", "root", "Loops", 'type:"Loops"', "custom", 1),
            TreeOrganizationNode("bass", "loops", "Bass", 'cat:"Bass"', "system", 2),
            TreeOrganizationNode("bass_other", "bass", "Other", None, "fallback", 3),
        ]
    )

    nodes = build_custom_resolved_tree(
        profile,
        [record],
        [("audio_type", "type"), ("category", "category"), ("subcategory", "subcategory"), ("pack", "pack")],
        lambda records, levels: build_tree_payload(records, levels),
    )

    # Output structure: Loops -> Bass -> MyPack (collapse Other)
    assert len(nodes) == 1
    loops = nodes[0]
    assert loops.label == "Loops"
    assert len(loops.children) == 1
    bass = loops.children[0]
    assert bass.label == "Bass"
    assert len(bass.children) == 1
    mypack = bass.children[0]
    assert mypack.label == "MyPack"


def test_custom_tree_routing_value_error_shows_invalid_tree_row(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.models import library_tree
    from gui.models.library_tree import LibraryTreeModel, RAW_NAME_ROLE

    _app = QApplication.instance() or QApplication([])
    model = LibraryTreeModel()
    profile = _profile([TreeOrganizationNode("dupes", "root", "Dupes", 'tag:"dupe"', "custom", 1)])
    model.set_custom_tree_profile(profile)

    def fake_build_custom_resolved_tree(*_args, **_kwargs):
        raise ValueError("Custom sibling overlap must be resolved before routing.")

    monkeypatch.setattr(library_tree, "build_custom_resolved_tree", fake_build_custom_resolved_tree)
    model.rebuild([_record("dupe.wav", category="Dupes", tags=["dupe"])])

    root = model.invisibleRootItem()
    assert root.rowCount() == 1
    assert root.child(0, 0).data(RAW_NAME_ROLE) == "Invalid Custom Tree"
    assert "Custom sibling overlap" in root.child(0, 0).toolTip()


def test_drafting_controller_stages_original_values_for_discard(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from gui.core.drafting_controller import DraftingController
    from gui.utils.constants import StagingColumn

    _app = QApplication.instance() or QApplication([])
    rec = _record("kick.wav", category="Kicks")

    class FakeModel:
        records = [rec]

        def _get_record_value(self, record, column):
            assert record is rec
            assert column == StagingColumn.CATEGORY
            return record.category

    class FakeApp(QObject):
        def __init__(self):
            super().__init__()
            self.model = FakeModel()

    controller = DraftingController(FakeApp())
    controller.stage_updates([(rec, StagingColumn.CATEGORY, "Bass")])

    assert controller.reorg_manager.get_revert_list() == [(rec, StagingColumn.CATEGORY, "Kicks")]


def test_save_summary_marks_only_changed_detail_columns(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from gui.core.drafting_controller import DraftingController
    from gui.utils.constants import StagingColumn
    from unshuffle.core import stable_record_identity

    _app = QApplication.instance() or QApplication([])
    rec = _record("kick.wav", category="Kicks", audio_type="Loops")

    class FakeApp(QObject):
        def __init__(self):
            super().__init__()
            self.model = object()

    controller = DraftingController(FakeApp())
    controller.reorg_manager.originals[(stable_record_identity(rec), StagingColumn.TYPE)] = (
        rec,
        StagingColumn.TYPE,
        "Oneshots",
    )

    _summary, rows = controller.build_save_summary(controller.app.model)

    assert rows[0]["filename"] == "kick.wav"
    assert rows[0]["type"] == "Loops"
    assert rows[0]["category"] == "Kicks"
    assert rows[0]["_changed_keys"] == {"type"}
    assert controller._save_detail_table_columns(rows) == (["filename", "type"], ["Filename", "Audio type"])


def test_drafting_controller_skips_non_learning_category_changes(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from gui.core.drafting_controller import DraftingController
    from gui.utils.constants import StagingColumn

    _app = QApplication.instance() or QApplication([])
    rec = _record("snare.wav", category="Percussion")

    class FakeModel:
        records = [rec]

        def _get_record_value(self, record, column):
            assert record is rec
            assert column == StagingColumn.CATEGORY
            return record.category

    class FakeBridge:
        def __init__(self):
            self.events = []

        def update_token_adjustments_from_events(self, events):
            self.events.extend(events)
            return len(events) * 2

    class FakeApp(QObject):
        def __init__(self):
            super().__init__()
            self.model = FakeModel()
            self.data_manager = SimpleNamespace(bridge=FakeBridge())

    app = FakeApp()
    controller = DraftingController(app)
    controller.stage_updates([(rec, StagingColumn.CATEGORY, "Snares")], learn=False)
    rec.category = "Snares"

    assert controller._learn_category_corrections_from_draft() == 0
    assert app.data_manager.bridge.events == []


def test_drafting_controller_learns_manual_category_changes(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from gui.core.drafting_controller import DraftingController
    from gui.utils.constants import StagingColumn

    _app = QApplication.instance() or QApplication([])
    rec = _record("shaker 808.wav", category="Percussion")
    rec.evidence = {
        "trace": {
            "components": {
                "filename": {
                    "weight": 0.72,
                    "token_trace": [
                        {
                            "token": "808",
                            "status": "matched",
                            "matches": [{"category": "Percussion", "contribution": 0.2}],
                        },
                        {
                            "token": "shaker",
                            "status": "matched",
                            "matches": [{"category": "Percussion", "contribution": 0.9}],
                        },
                    ],
                }
            }
        }
    }

    class FakeModel:
        records = [rec]

        def _get_record_value(self, record, column):
            assert record is rec
            assert column == StagingColumn.CATEGORY
            return record.category

    class FakeBridge:
        def __init__(self):
            self.events = []

        def update_token_adjustments_from_events(self, events):
            self.events.extend(events)
            return len(events) * 2

    class FakeApp(QObject):
        def __init__(self):
            super().__init__()
            self.model = FakeModel()
            self.data_manager = SimpleNamespace(bridge=FakeBridge())

    app = FakeApp()
    controller = DraftingController(app)
    controller.stage_updates([(rec, StagingColumn.CATEGORY, "Snares")])
    rec.category = "Snares"

    assert controller._learn_category_corrections_from_draft() > 0
    assert (
        "path:d:/samples/pack/shaker 808.wav",
        "shaker",
        "Percussion",
        "Snares",
        -0.01,
        0.01,
    ) in app.data_manager.bridge.events
    assert all(event[1] != "808" for event in app.data_manager.bridge.events)


def test_drafting_controller_does_not_learn_without_original_evidence(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from gui.core.drafting_controller import DraftingController
    from gui.utils.constants import StagingColumn

    _app = QApplication.instance() or QApplication([])
    rec = _record("shaker.wav", category="Percussion")

    class FakeModel:
        records = [rec]

        def _get_record_value(self, record, column):
            assert record is rec
            assert column == StagingColumn.CATEGORY
            return record.category

    class FakeBridge:
        def __init__(self):
            self.events = []

        def update_token_adjustments_from_events(self, events):
            self.events.extend(events)
            return len(events) * 2

    class FakeApp(QObject):
        def __init__(self):
            super().__init__()
            self.model = FakeModel()
            self.data_manager = SimpleNamespace(bridge=FakeBridge())

    app = FakeApp()
    controller = DraftingController(app)
    controller.stage_updates([(rec, StagingColumn.CATEGORY, "Snares")])
    rec.category = "Snares"

    assert controller._learn_category_corrections_from_draft() == 0
    assert app.data_manager.bridge.events == []


def test_drafting_controller_accepts_source_rows_for_bulk_type(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from gui.core.drafting_controller import DraftingController
    from gui.utils.constants import StagingColumn

    _app = QApplication.instance() or QApplication([])
    rec = _record("loop.wav", audio_type="Loops")

    class FakeModel:
        records = [rec]

        def record(self, row):
            return self.records[row]

        def _get_record_value(self, record, column):
            assert record is rec
            assert column == StagingColumn.TYPE
            return record.audio_type

        def _apply_bulk_values(self, updates):
            for record, column, value in updates:
                if column == StagingColumn.TYPE:
                    record.audio_type = value

    class FakeFooter:
        def toggle_footer(self, _visible):
            pass

        def log(self, _message):
            pass

        def set_status(self, _message):
            pass

        def set_reorg_draft_state(self, _text, _visible, can_save=False):
            pass

    class FakeViewController:
        def is_tree_visible(self):
            return False

        def update_library_views(self, tree_delay_ms=0):
            pass

    class FakeApp(QObject):
        def __init__(self):
            super().__init__()
            self.model = FakeModel()
            self.footer = FakeFooter()
            self.view_controller = FakeViewController()

    controller = DraftingController(FakeApp())

    changed = controller.apply_bulk_type("Oneshots", [0])

    assert [(record, column, value) for record, column, value in changed] == [(rec, StagingColumn.TYPE, "Oneshots")]
    assert rec.audio_type == "Oneshots"


def test_drafting_controller_tree_reorganize_uses_shared_undo_stack(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtGui import QUndoStack
    from PySide6.QtWidgets import QApplication
    from gui.core.drafting_controller import DraftingController
    from gui.models.staging_table import StagingTableModel
    from gui.utils.constants import StagingColumn

    _app = QApplication.instance() or QApplication([])
    rec = _record("loop.wav", audio_type="Loops")
    undo_stack = QUndoStack()

    class FakeFooter:
        def toggle_footer(self, _visible):
            pass

        def log(self, _message):
            pass

        def set_status(self, _message):
            pass

        def set_reorg_draft_state(self, _text, _visible, can_save=False):
            pass

    class FakeViewController:
        def is_tree_visible(self):
            return False

        def update_library_views(self, tree_delay_ms=0):
            pass

    class FakeApp(QObject):
        def __init__(self):
            super().__init__()
            self.undo_stack = undo_stack
            self.model = StagingTableModel([rec], undo_stack=None, sync_callback=None)
            self.footer = FakeFooter()
            self.view_controller = FakeViewController()
            self.library_tab = SimpleNamespace()

    app = FakeApp()
    controller = DraftingController(app)

    controller.handle_tree_reorganize([rec], {"audio_type": "Oneshots"})

    assert rec.audio_type == "Oneshots"
    assert undo_stack.canUndo()
    assert controller.has_changes()

    undo_stack.undo()

    assert rec.audio_type == "Loops"
    assert not controller.has_changes()

    undo_stack.redo()

    assert rec.audio_type == "Oneshots"
    assert controller.has_changes()


def test_drafting_controller_tree_type_move_falls_back_invalid_category(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from gui.core.drafting_controller import DraftingController
    from gui.models.library_tree import LibraryTreeModel
    from gui.models.staging_table import StagingTableModel

    _app = QApplication.instance() or QApplication([])
    bass = _record("bass-loop.wav", audio_type="Loops", category="Bass", row_id=1)
    full_drums = _record("drum-loop.wav", audio_type="Loops", category="Full Drums", row_id=2)

    class FakeFooter:
        def log(self, _message):
            pass

    class FakeViewController:
        def is_tree_visible(self):
            return False

    class FakeApp(QObject):
        def __init__(self):
            super().__init__()
            self.model = StagingTableModel([bass, full_drums])
            self.footer = FakeFooter()
            self.view_controller = FakeViewController()
            self.library_tab = SimpleNamespace(tree_model=LibraryTreeModel())

    app = FakeApp()
    controller = DraftingController(app)
    controller.schedule_reorg_impact_analysis = lambda: None

    controller.handle_tree_reorganize([bass, full_drums], {"audio_type": "Oneshots"})

    assert bass.audio_type == "Oneshots"
    assert bass.category == "Bass"
    assert full_drums.audio_type == "Oneshots"
    assert full_drums.category == "Uncategorized"
    assert not full_drums.subcategory


def test_custom_tree_full_drums_subset_moves_to_oneshot_uncategorized(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtGui import QUndoStack
    from PySide6.QtWidgets import QApplication
    from gui.core.drafting_controller import DraftingController
    from gui.models.library_tree import LibraryTreeModel
    from gui.models.staging_table import StagingTableModel

    _app = QApplication.instance() or QApplication([])
    full_drums_a = _record("full-a.wav", audio_type="Loops", category="Full Drums", subcategory="no-sub", tags=["dupe"], row_id=1)
    full_drums_b = _record("full-b.wav", audio_type="Loops", category="Full Drums", subcategory="no-sub", tags=["dupe"], row_id=2)
    kick = _record("kick.wav", audio_type="Oneshots", category="Kicks", subcategory="", tags=[], row_id=3)
    records = [full_drums_a, full_drums_b, kick]
    profile = _profile(
        [
            TreeOrganizationNode("dupes", "root", "Dupes", 'tag:"dupe"', "custom", 1),
            TreeOrganizationNode("loops", "root", "Loops", 'type:"Loops"', "system", 2),
            TreeOrganizationNode("oneshots", "root", "Oneshots", 'type:"Oneshots"', "system", 3),
        ]
    )

    class FakeFooter:
        def log(self, _message):
            pass

    class FakeViewController:
        def is_tree_visible(self):
            return False

        def update_library_views(self, tree_delay_ms=0):
            pass

    class FakeTreeOrganizationController:
        active_profile = profile

    class FakeApp(QObject):
        def __init__(self):
            super().__init__()
            self.model = StagingTableModel(records)
            self.footer = FakeFooter()
            self.view_controller = FakeViewController()
            self.tree_organization_controller = FakeTreeOrganizationController()
            self.library_tab = SimpleNamespace(tree_model=LibraryTreeModel())
            self.undo_stack = QUndoStack()

    app = FakeApp()
    controller = DraftingController(app)
    controller.schedule_reorg_impact_analysis = lambda: None

    controller.handle_tree_reorganize([full_drums_a, full_drums_b], {"audio_type": "Oneshots"})

    for record in (full_drums_a, full_drums_b):
        assert record.audio_type == "Oneshots"
        assert record.category == "Uncategorized"
        assert not record.subcategory
        assert not hasattr(record, "_unshuffle_custom_tree_semantic_override")

    tree_model = LibraryTreeModel()
    tree_model.set_custom_tree_profile(profile)
    tree_model.rebuild(records)

    assert tree_model.index_for_path(("Dupes", "Oneshots", "Uncategorized", "Pack")) is not None


def test_custom_tree_drag_to_type_keeps_custom_bucket_and_consolidates_child(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtGui import QUndoStack
    from PySide6.QtWidgets import QApplication
    from gui.core.drafting_controller import DraftingController
    from gui.models.library_tree import LibraryTreeModel
    from gui.models.staging_table import StagingTableModel

    _app = QApplication.instance() or QApplication([])
    already_loop = _record("loop.wav", audio_type="Loops", category="Full Drums", tags=["dupe"], row_id=1)
    shot = _record("shot.wav", audio_type="Oneshots", category="Kicks", tags=["dupe"], row_id=2)
    records = [already_loop, shot]
    profile = _profile(
        [
            TreeOrganizationNode("dupes", "root", "Dupes", 'tag:"dupe"', "custom", 1),
            TreeOrganizationNode("loops", "root", "Loops", 'type:"Loops"', "system", 2),
            TreeOrganizationNode("oneshots", "root", "Oneshots", 'type:"Oneshots"', "system", 3),
        ]
    )

    class FakeFooter:
        def log(self, _message):
            pass

    class FakeViewController:
        def is_tree_visible(self):
            return False

        def update_library_views(self, tree_delay_ms=0):
            pass

    class FakeTreeOrganizationController:
        active_profile = profile

    class FakeApp(QObject):
        def __init__(self):
            super().__init__()
            self.model = StagingTableModel(records)
            self.footer = FakeFooter()
            self.view_controller = FakeViewController()
            self.tree_organization_controller = FakeTreeOrganizationController()
            self.library_tab = SimpleNamespace(tree_model=LibraryTreeModel())
            self.undo_stack = QUndoStack()

    app = FakeApp()
    controller = DraftingController(app)
    controller.schedule_reorg_impact_analysis = lambda: None

    controller.handle_tree_reorganize(records, {"audio_type": "Loops"})

    assert already_loop.audio_type == "Loops"
    assert shot.audio_type == "Loops"
    for record in records:
        assert not hasattr(record, "_unshuffle_custom_tree_semantic_override")

    tree_model = LibraryTreeModel()
    tree_model.set_custom_tree_profile(profile)
    tree_model.rebuild(records)

    assert tree_model.index_for_path(("Dupes", "Loops")) is not None
    assert tree_model.index_for_path(("Dupes", "Oneshots")) is None
    assert tree_model.index_for_path(("Loops",)) is None


def test_drafting_controller_collapses_footer_after_discard(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from gui.core.drafting_controller import DraftingController
    from gui.utils.constants import StagingColumn

    _app = QApplication.instance() or QApplication([])
    rec = _record("kick.wav", category="Kicks")

    class FakeModel:
        records = [rec]

        def _get_record_value(self, record, column):
            return record.category

        def _apply_bulk_values(self, updates):
            for record, column, value in updates:
                if column == StagingColumn.CATEGORY:
                    record.category = value

    class FakeFooter:
        def __init__(self):
            self.toggle_calls = []

        def set_reorg_draft_state(self, _text, _visible, can_save=False):
            pass

        def log(self, _message):
            pass

        def toggle_footer(self, visible):
            self.toggle_calls.append(visible)

    class FakeViewController:
        def update_library_views(self, tree_delay_ms=0):
            pass

    class FakeApp(QObject):
        def __init__(self):
            super().__init__()
            self.model = FakeModel()
            self.footer = FakeFooter()
            self.view_controller = FakeViewController()

    app = FakeApp()
    controller = DraftingController(app)
    controller.stage_updates([(rec, StagingColumn.CATEGORY, "Claps")])

    controller.discard_reorg_draft(confirm=False)

    assert app.footer.toggle_calls[-1] is False
    assert rec.category == "Kicks"


def test_drafting_controller_moves_custom_node_under_target_filter_path(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from gui.core.drafting_controller import DraftingController
    from gui.models.staging_table import StagingTableModel

    _app = QApplication.instance() or QApplication([])
    rec = _record("dupe.wav", category="Kicks", audio_type="Oneshots", tags=["dupe"])
    model = StagingTableModel([rec])
    profile = _profile(
        [
            TreeOrganizationNode("loops", "root", "Loops", 'type:"Loops"', "system", 1),
            TreeOrganizationNode("dupes", "root", "Dupes", 'tag:"dupe"', "custom", 2),
        ]
    )

    class FakeFooter:
        def toggle_footer(self, _visible):
            pass

        def log(self, _message):
            pass

        def set_status(self, _message):
            pass

    class FakeViewController:
        def __init__(self):
            self.refresh_calls = []

        def is_tree_visible(self):
            return True

        def update_library_views(self, tree_delay_ms=0):
            self.refresh_calls.append(tree_delay_ms)

    class FakeTreeOrganizationController:
        def __init__(self):
            self.active_profile = profile
            self.sync_refresh_args = []

        def _sync_active_profile(self, *, refresh=True):
            self.sync_refresh_args.append(refresh)

    class FakeApp(QObject):
        def __init__(self):
            super().__init__()
            self.model = model
            self.footer = FakeFooter()
            self.view_controller = FakeViewController()
            self.tree_organization_controller = FakeTreeOrganizationController()

    app = FakeApp()
    controller = DraftingController(app)

    changed = controller.stage_tree_reorg_updates(
        [],
        move_profile_node_id="dupes",
        target_fields={"audio_type": "Loops", "category": "Bass"},
    )

    assert changed
    active = app.tree_organization_controller.active_profile
    bass = next(node for node in active.nodes if node.name == "Bass")
    dupes = next(node for node in active.nodes if node.id == "dupes")
    assert bass.parent_id == "loops"
    assert bass.filter_query == 'cat:"Bass"'
    assert dupes.parent_id == bass.id
    assert dupes.filter_query == 'tag:"dupe"'
    assert app.tree_organization_controller.sync_refresh_args == [False]
    assert app.view_controller.refresh_calls == []


def test_drafting_controller_reuses_existing_custom_semantic_parent(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from gui.core.drafting_controller import DraftingController
    from gui.models.library_tree import LibraryTreeModel, RAW_NAME_ROLE
    from gui.models.staging_table import StagingTableModel

    _app = QApplication.instance() or QApplication([])
    rec = _record("dupe.wav", category="Dupes", audio_type="Loops", tags=["dupe"])
    model = StagingTableModel([rec])
    profile = _profile(
        [
            TreeOrganizationNode("loops", "root", "Loops", 'type:"Loops"', "custom", 1),
            TreeOrganizationNode("dupes", "root", "Dupes", 'tag:"dupe"', "custom", 2),
        ]
    )

    class FakeFooter:
        def toggle_footer(self, _visible):
            pass

        def log(self, _message):
            pass

        def set_status(self, _message):
            pass

    class FakeViewController:
        def is_tree_visible(self):
            return False

    class FakeTreeOrganizationController:
        def __init__(self):
            self.active_profile = profile

        def _sync_active_profile(self, *, refresh=True):
            pass

    class FakeApp(QObject):
        def __init__(self):
            super().__init__()
            self.model = model
            self.footer = FakeFooter()
            self.view_controller = FakeViewController()
            self.tree_organization_controller = FakeTreeOrganizationController()

    app = FakeApp()
    controller = DraftingController(app)
    assert controller.stage_tree_reorg_updates(
        [],
        move_profile_node_id="dupes",
        target_fields={"audio_type": "Loops"},
    )

    active = app.tree_organization_controller.active_profile
    dupes = next(node for node in active.nodes if node.id == "dupes")
    assert dupes.parent_id == "loops"
    assert len([node for node in active.nodes if node.name == "Loops"]) == 1

    tree_model = LibraryTreeModel()
    tree_model.set_custom_tree_profile(active)
    tree_model.rebuild([rec])
    assert tree_model.invisibleRootItem().child(0, 0).data(RAW_NAME_ROLE) != "Invalid Custom Tree"
    assert tree_model.index_for_path(("Loops", "Dupes")) is not None


def test_drafting_controller_merges_duplicate_custom_siblings(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from gui.core.drafting_controller import DraftingController
    from gui.models.library_tree import LibraryTreeModel, RAW_NAME_ROLE
    from gui.models.staging_table import StagingTableModel

    _app = QApplication.instance() or QApplication([])
    rec = _record("dupe.wav", category="Dupes", audio_type="Loops", tags=["dupe"])
    model = StagingTableModel([rec])
    profile = _profile(
        [
            TreeOrganizationNode("loops", "root", "Loops", 'type:"Loops"', "custom", 1),
            TreeOrganizationNode("dupes_loops", "loops", "Dupes", 'tag:"dupe"', "custom", 1),
            TreeOrganizationNode("dupes_root", "root", "Dupes", 'tag:"dupe"', "custom", 2),
        ]
    )

    class FakeFooter:
        def toggle_footer(self, _visible):
            pass

        def log(self, _message):
            pass

        def set_status(self, _message):
            pass

        def set_reorg_draft_state(self, *args, **kwargs):
            pass

    class FakeViewController:
        def is_tree_visible(self):
            return False

        def update_library_views(self, delay):
            pass

    class FakeTreeOrganizationController:
        def __init__(self):
            self.active_profile = profile

        def _sync_active_profile(self, refresh=True):
            pass

    class FakeApp(QObject):
        def __init__(self):
            super().__init__()
            self.model = model
            self.footer = FakeFooter()
            self.view_controller = FakeViewController()
            self.tree_organization_controller = FakeTreeOrganizationController()

    app = FakeApp()
    controller = DraftingController(app)
    # Drag "dupes_root" under "loops" (Loops) where "dupes_loops" already exists.
    assert controller.stage_tree_reorg_updates(
        [],
        move_profile_node_id="dupes_root",
        target_fields={"audio_type": "Loops"},
    )

    active = app.tree_organization_controller.active_profile
    # The duplicate "dupes_root" should have been merged/removed, leaving only one Dupes node
    dupes_nodes = [node for node in active.nodes if node.name == "Dupes"]
    assert len(dupes_nodes) == 1
    assert dupes_nodes[0].id == "dupes_loops"

    tree_model = LibraryTreeModel()
    tree_model.set_custom_tree_profile(active)
    tree_model.rebuild([rec])
    assert tree_model.invisibleRootItem().child(0, 0).data(RAW_NAME_ROLE) != "Invalid Custom Tree"


def test_drafting_controller_moves_system_presentation_node_under_target_filter_path(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from gui.core.drafting_controller import DraftingController
    from gui.models.library_tree import LibraryTreeModel
    from gui.models.staging_table import StagingTableModel

    _app = QApplication.instance() or QApplication([])
    rec = _record("dupe.wav", category="Kicks", audio_type="Oneshots", tags=["dupe"])
    model = StagingTableModel([rec])
    profile = _profile(
        [
            TreeOrganizationNode("loops", "root", "Loops", 'type:"Loops"', "system", 1),
            TreeOrganizationNode("dupes", "root", "Dupes", 'tag:"dupe"', "system", 2),
        ]
    )

    class FakeFooter:
        def toggle_footer(self, _visible):
            pass

        def log(self, _message):
            pass

        def set_status(self, _message):
            pass

    class FakeViewController:
        def is_tree_visible(self):
            return False

        def update_library_views(self, tree_delay_ms=0):
            pass

    class FakeTreeOrganizationController:
        def __init__(self):
            self.active_profile = profile
            self.sync_refresh_args = []

        def _sync_active_profile(self, *, refresh=True):
            self.sync_refresh_args.append(refresh)

    class FakeLibraryTab:
        def __init__(self):
            self.tree_model = LibraryTreeModel()

    class FakeApp(QObject):
        def __init__(self):
            super().__init__()
            self.model = model
            self.footer = FakeFooter()
            self.view_controller = FakeViewController()
            self.tree_organization_controller = FakeTreeOrganizationController()
            self.library_tab = FakeLibraryTab()

    app = FakeApp()
    controller = DraftingController(app)

    changed = controller.stage_tree_reorg_updates(
        [],
        move_profile_node_id="dupes",
        target_fields={"audio_type": "Loops", "category": "Bass"},
    )

    assert changed
    active = app.tree_organization_controller.active_profile
    bass = next(node for node in active.nodes if node.name == "Bass")
    dupes = next(node for node in active.nodes if node.id == "dupes")
    assert dupes.parent_id == bass.id
    assert dupes.node_type == "system"
    assert app.tree_organization_controller.sync_refresh_args == [False]


def test_drafting_controller_successive_moves_render_moved_semantic_node(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from gui.core.drafting_controller import DraftingController
    from gui.models.library_tree import LibraryTreeModel
    from gui.models.staging_table import StagingTableModel
    from gui.utils.constants import StagingColumn

    _app = QApplication.instance() or QApplication([])
    rec = _record("dupe.wav", category="Dupes", audio_type="Loops", subcategory="Hard")
    model = StagingTableModel([rec])
    profile = _profile(
        [
            TreeOrganizationNode("oneshots", "root", "Oneshots", 'type:"Oneshots"', "system", 1),
            TreeOrganizationNode("dupes", "root", "Dupes", 'cat:"Dupes"', "system", 2),
        ]
    )

    class FakeFooter:
        def toggle_footer(self, _visible):
            pass

        def log(self, _message):
            pass

        def set_status(self, _message):
            pass

    class FakeViewController:
        def is_tree_visible(self):
            return False

        def update_library_views(self, tree_delay_ms=0):
            pass

    class FakeTreeOrganizationController:
        def __init__(self):
            self.active_profile = profile

        def _sync_active_profile(self, *, refresh=True):
            pass

    class FakeLibraryTab:
        def __init__(self):
            self.tree_model = LibraryTreeModel()

    class FakeApp(QObject):
        def __init__(self):
            super().__init__()
            self.model = model
            self.footer = FakeFooter()
            self.view_controller = FakeViewController()
            self.tree_organization_controller = FakeTreeOrganizationController()
            self.library_tab = FakeLibraryTab()

    app = FakeApp()
    controller = DraftingController(app)
    controller.stage_tree_reorg_updates(
        [(rec, StagingColumn.TYPE, "Oneshots")],
        move_profile_node_id="dupes",
        target_fields={"audio_type": "Oneshots"},
    )
    controller.stage_tree_reorg_updates(
        [(rec, StagingColumn.CATEGORY, "Bass")],
        move_profile_node_id="dupes",
        target_fields={"audio_type": "Oneshots", "category": "Bass"},
    )

    active = app.tree_organization_controller.active_profile
    bass = next(node for node in active.nodes if node.name == "Bass")
    dupes = next(node for node in active.nodes if node.id == "dupes")
    assert dupes.parent_id == bass.id
    assert rec.audio_type == "Oneshots"
    assert rec.category == "Bass"

    tree_model = LibraryTreeModel()
    tree_model.set_custom_tree_profile(active)
    tree_model.rebuild([rec])
    assert tree_model.index_for_path(("Oneshots", "Bass", "Dupes", "Pack")) is not None


def test_repeated_custom_node_drop_keeps_profile_bucket_routing(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from gui.core.drafting_controller import DraftingController
    from gui.models.library_tree import LibraryTreeModel
    from gui.models.library_tree_resolution import SEMANTIC_OVERRIDE_ATTR
    from gui.models.staging_table import StagingTableModel
    from gui.utils.constants import StagingColumn

    _app = QApplication.instance() or QApplication([])
    rec = _record("dupe.wav", category="Dupes", audio_type="Oneshots", subcategory="Generic", tags=["dupe"])
    model = StagingTableModel([rec])
    profile = _profile(
        [
            TreeOrganizationNode("oneshots", "root", "Oneshots", 'type:"Oneshots"', "system", 1),
            TreeOrganizationNode("claps", "oneshots", "Claps", 'cat:"Claps"', "system", 1),
            TreeOrganizationNode("dupes", "claps", "Dupes", 'tag:"dupe"', "custom", 1),
        ]
    )

    class FakeFooter:
        def toggle_footer(self, _visible):
            pass

        def log(self, _message):
            pass

        def set_status(self, _message):
            pass

    class FakeViewController:
        def is_tree_visible(self):
            return False

        def update_library_views(self, tree_delay_ms=0):
            pass

    class FakeTreeOrganizationController:
        def __init__(self):
            self.active_profile = profile

        def _sync_active_profile(self, *, refresh=True):
            pass

    class FakeLibraryTab:
        def __init__(self):
            self.tree_model = LibraryTreeModel()

    class FakeApp(QObject):
        def __init__(self):
            super().__init__()
            self.model = model
            self.footer = FakeFooter()
            self.view_controller = FakeViewController()
            self.tree_organization_controller = FakeTreeOrganizationController()
            self.library_tab = FakeLibraryTab()

    app = FakeApp()
    controller = DraftingController(app)
    assert controller.stage_tree_reorg_updates(
        [(rec, StagingColumn.CATEGORY, "Claps")],
        move_profile_node_id="dupes",
        target_fields={"audio_type": "Oneshots", "category": "Claps"},
    )

    assert not hasattr(rec, SEMANTIC_OVERRIDE_ATTR)
    tree_model = LibraryTreeModel()
    tree_model.set_custom_tree_profile(app.tree_organization_controller.active_profile)
    tree_model.rebuild([rec])
    assert tree_model.index_for_path(("Oneshots", "Claps", "Dupes")) is not None


def test_drafting_controller_saves_draft_profile_and_remembers_active_id(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from gui.core.drafting_controller import DraftingController
    from gui.models.staging_table import StagingTableModel

    _app = QApplication.instance() or QApplication([])
    rec = _record("dupe.wav", tags=["dupe"])
    model = StagingTableModel([rec])
    repository = TreeOrganizationRepository(tmp_path / "profiles.json")
    profile = repository.save_profile(
        _profile(
            [
                TreeOrganizationNode("loops", "root", "Loops", 'type:"Loops"', "system", 1),
                TreeOrganizationNode("dupes", "root", "Dupes", 'tag:"dupe"', "custom", 2),
            ]
        )
    )

    class FakeFooter:
        def toggle_footer(self, _visible):
            pass

        def log(self, _message):
            pass

        def set_status(self, _message):
            pass

    class FakeViewController:
        def is_tree_visible(self):
            return False

        def update_library_views(self, tree_delay_ms=0):
            pass

    class FakeTreeOrganizationController:
        def __init__(self):
            self.active_profile = profile
            self.repository = repository
            self.persisted = []

        def _sync_active_profile(self, *, refresh=True):
            pass

        def _persist_active_profile_id(self, profile_id):
            self.persisted.append(profile_id)

    class FakeLibraryTab:
        def __init__(self):
            from gui.models.library_tree import LibraryTreeModel

            self.tree_model = LibraryTreeModel()

    class FakeApp(QObject):
        def __init__(self):
            super().__init__()
            self.model = model
            self.footer = FakeFooter()
            self.view_controller = FakeViewController()
            self.tree_organization_controller = FakeTreeOrganizationController()
            self.library_tab = FakeLibraryTab()

    app = FakeApp()
    controller = DraftingController(app)
    assert controller.stage_tree_reorg_updates(
        [],
        move_profile_node_id="dupes",
        target_fields={"audio_type": "Loops", "category": "Bass"},
    )

    controller._save_draft_profile_if_needed()

    saved = repository.get_profile(profile.id)
    assert saved is not None
    bass = next(node for node in saved.nodes if node.name == "Bass")
    dupes = next(node for node in saved.nodes if node.id == "dupes")
    assert dupes.parent_id == bass.id
    assert app.tree_organization_controller.persisted == [profile.id]


def test_drafting_controller_save_defers_refresh_until_after_busy(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from gui.core.drafting_controller import DraftingController
    from gui.models.staging_table import StagingTableModel
    from gui.utils.constants import StagingColumn

    _app = QApplication.instance() or QApplication([])
    rec = _record("outlier.wav", category="Bass")
    model = StagingTableModel([rec])
    events = []

    class FakeFooter:
        def toggle_footer(self, _visible):
            pass

        def log(self, message):
            events.append(("log", message))

        def set_status(self, _message):
            pass

        def set_reorg_draft_state(self, text, visible, can_save=False):
            events.append(("draft_state", text, visible, can_save))

    class FakeViewController:
        def update_library_views(self, tree_delay_ms=0):
            events.append(("views", tree_delay_ms))

    class FakeSearchController:
        current_query = 'tag:"possibleduplicate"'

        def execute_search(self):
            events.append(("search",))

    class FakeApp(QObject):
        def __init__(self):
            super().__init__()
            self.model = model
            self.footer = FakeFooter()
            self.view_controller = FakeViewController()
            self.search_controller = FakeSearchController()
            self.data_manager = None
            self.system_controller = None

    app = FakeApp()
    controller = DraftingController(app)
    controller._show_save_confirm_dialog = lambda *_args, **_kwargs: True
    controller._impact_worker = object()
    controller._impact_request_id = 7
    rec.category = "FX"
    controller.reorg_manager.stage_updates([(rec, StagingColumn.CATEGORY, "Bass")], learn=False)

    monkeypatch.setattr(
        "gui.utils.state.rewrite_staging_from_model",
        lambda _app: events.append(("rewrite",)),
    )
    monkeypatch.setattr(
        "gui.utils.ui_helpers.set_ui_busy",
        lambda _app, busy: events.append(("busy", busy)),
    )
    monkeypatch.setattr(
        "gui.core.drafting_controller.QTimer.singleShot",
        lambda _delay, callback: events.append(("defer", _delay)) or callback(),
    )

    controller.save_reorg_draft()

    assert controller._impact_worker is None
    assert controller._impact_request_id == 8
    assert not controller.has_changes()
    assert events.index(("busy", False)) < events.index(("defer", 0)) < events.index(("search",))


def test_library_tree_drop_confirmation_reports_changed_fields(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.views.library_tree import LibraryTreeView

    _app = QApplication.instance() or QApplication([])
    view = LibraryTreeView()
    rec = _record("dupe.wav", category="Dupes", audio_type="Loops")

    assert view._drop_mutation_lines([rec], {"audio_type": "Oneshots", "category": "Bass"}) == [
        "type to Oneshots",
        "category to Bass",
    ]
    assert view._drop_mutation_lines([rec], {"audio_type": "Loops", "category": "Dupes"}) == []

    calls = []
    view._confirm_record_reclassification = lambda source_name, target_name, mutation_lines, record_count: calls.append((source_name, target_name, mutation_lines, record_count)) or False  # type: ignore

    assert not view._confirm_folder_move_if_needed(
        "Dupes",
        "Bass",
        [rec],
        {"audio_type": "Oneshots", "category": "Bass"},
    )
    assert calls == [("Dupes", "Bass", ["type to Oneshots", "category to Bass"], 1)]

    already_loops = [
        _record(f"already-{index}.wav", category="Dupes", audio_type="Loops")
        for index in range(12)
    ]
    changing = [
        _record(f"changing-{index}.wav", category="Dupes", audio_type="Oneshots")
        for index in range(2)
    ]
    calls.clear()
    assert not view._confirm_folder_move_if_needed(
        "Dupes",
        "Loops",
        already_loops + changing,
        {"audio_type": "Loops"},
    )
    assert calls == [("Dupes", "Loops", ["type to Loops"], 2)]


def test_library_tree_drop_confirmation_uses_applied_target_label(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.views.library_tree import LibraryTreeView

    _app = QApplication.instance() or QApplication([])
    view = LibraryTreeView()
    rec = _record("full.wav", category="Full Drums", audio_type="Loops")
    calls = []
    view._confirm_record_reclassification = lambda source_name, target_name, mutation_lines, record_count: calls.append((source_name, target_name, mutation_lines, record_count)) or False  # type: ignore

    applied = view._normalize_drop_fields({"audio_type": "Oneshots", "category": "Full Drums"})
    assert not view._confirm_folder_move_if_needed(
        "Full Drums",
        view._drop_target_label(applied),
        [rec],
        applied,
    )

    assert applied == {"audio_type": "Oneshots", "category": "Uncategorized"}
    assert calls == [("Full Drums", "Uncategorized", ["type to Oneshots", "category to Uncategorized"], 1)]


def test_library_tree_drop_dialog_explains_file_reclassification(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox
    from gui.views import library_tree
    from gui.views.library_tree import LibraryTreeView

    _app = QApplication.instance() or QApplication([])
    captured = {}

    class FakeMessageBox:
        Question = QMessageBox.Question
        Yes = QMessageBox.Yes
        No = QMessageBox.No

        def __init__(self, _parent=None):
            pass

        def setIcon(self, icon):
            captured["icon"] = icon

        def setWindowTitle(self, title):
            captured["title"] = title

        def setText(self, text):
            captured["text"] = text

        def setInformativeText(self, text):
            captured["informative"] = text

        def setMinimumWidth(self, width):
            captured["minimum_width"] = width

        def setStandardButtons(self, buttons):
            captured["buttons"] = buttons

        def setDefaultButton(self, button):
            captured["default"] = button

        def exec(self):
            return QMessageBox.No

    monkeypatch.setattr(library_tree, "QMessageBox", FakeMessageBox, raising=False)
    view = LibraryTreeView()

    assert not view._confirm_record_reclassification("Dupes", "Loops", ["type to Loops"], 2)
    assert captured["title"] == "Confirm File Reclassification"
    assert captured["text"] == "Reclassify 2 records?"
    assert 'Source: "Dupes"' in captured["informative"]
    assert "File changes: type to Loops." in captured["informative"]
    assert "Custom tree folders may still contain these files" in captured["informative"]
    assert "Edit tree organization" in captured["informative"]
    assert captured["minimum_width"] >= 420


def test_library_tree_drop_index_requires_row_center(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication
    from gui.models.library_tree import LibraryTreeModel
    from gui.views.library_tree import LibraryTreeView

    _app = QApplication.instance() or QApplication([])
    model = LibraryTreeModel()
    model.rebuild([
        _record("loop.wav", audio_type="Loops", category="Full Drums", row_id=1),
        _record("kick.wav", audio_type="Oneshots", category="Kicks", row_id=2),
    ])
    view = LibraryTreeView()
    view.setModel(model)
    view.resize(420, 240)
    view.show()
    _app.processEvents()

    oneshots = model.index_for_path(("Oneshots",))
    assert oneshots is not None
    rect = view.visualRect(oneshots)
    assert rect.isValid()

    assert view._drop_index_at(rect.center()).isValid()
    edge = QPoint(rect.center().x(), rect.top() + 1)
    assert not view._drop_index_at(edge).isValid()


def test_preserve_pack_is_staged_as_discardable_draft(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from gui.core.drafting_controller import DraftingController
    from gui.models.staging_table import StagingTableModel

    _app = QApplication.instance() or QApplication([])
    rec = _record("kick.wav", category="Kicks")
    model = StagingTableModel([rec])

    class FakeFooter:
        def toggle_footer(self, _visible):
            pass

        def log(self, _message):
            pass

        def set_status(self, _message):
            pass

    class FakeViewController:
        def update_library_views(self, tree_delay_ms=0):
            pass

    class FakeApp(QObject):
        def __init__(self):
            super().__init__()
            self.model = model
            self.footer = FakeFooter()
            self.view_controller = FakeViewController()

    controller = DraftingController(FakeApp())
    preserved_root = Path("D:/Samples/Pack")
    changed = controller.apply_preserve_pack([rec], preserved_root)

    assert changed
    assert rec.is_preserved is True
    assert rec.preserved_root == preserved_root

    model._apply_bulk_values(controller.reorg_manager.get_revert_list())
    assert not rec.is_preserved
    assert rec.preserved_root is None


def test_unpreserve_pack_is_staged_as_discardable_draft(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from gui.core.drafting_controller import DraftingController
    from gui.models.staging_table import StagingTableModel

    _app = QApplication.instance() or QApplication([])
    rec = _record("kick.wav", category="Kicks")
    rec.is_preserved = True
    rec.preserved_root = Path("D:/Samples/Pack")
    model = StagingTableModel([rec])

    class FakeFooter:
        def toggle_footer(self, _visible):
            pass

        def log(self, _message):
            pass

        def set_status(self, _message):
            pass

    class FakeViewController:
        def update_library_views(self, tree_delay_ms=0):
            pass

    class FakeApp(QObject):
        def __init__(self):
            super().__init__()
            self.model = model
            self.footer = FakeFooter()
            self.view_controller = FakeViewController()

    controller = DraftingController(FakeApp())
    changed = controller.apply_unpreserve_pack([rec], rec.preserved_root)

    assert changed
    assert not rec.is_preserved
    assert rec.preserved_root is None

    model._apply_bulk_values(controller.reorg_manager.get_revert_list())
    assert rec.is_preserved is True
    assert rec.preserved_root == Path("D:/Samples/Pack")


def _tree_editor(monkeypatch, profile, records=None):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.tree_organization import TreeOrganizationEditor

    _app = QApplication.instance() or QApplication([])
    editor = TreeOrganizationEditor([], profile, list(records or [_record("kick.wav", category="Kicks")]))
    editor._ensure_editor_built()
    editor.show()
    _app.processEvents()
    return editor


def _tree_item_count(item):
    from gui.widgets.tree_organization import NODE_ID_ROLE

    count = 1 if item.data(NODE_ID_ROLE) else 0
    return count + sum(_tree_item_count(item.child(row, 0)) for row in range(item.rowCount()))


def test_tree_editor_model_builds_one_row_per_profile_node(monkeypatch):
    profile = _profile(
        [
            TreeOrganizationNode("loops", "root", "Loops", 'type:"Loops"', "system", 1),
            TreeOrganizationNode("oneshots", "root", "Oneshots", 'type:"Oneshots"', "system", 2),
            TreeOrganizationNode("kicks", "oneshots", "Kicks", 'cat:"Kicks"', "system", 1),
            TreeOrganizationNode("bass", "oneshots", "Bass", 'cat:"Bass"', "system", 2),
        ]
    )
    editor = _tree_editor(monkeypatch, profile)
    editor.tree.setExpanded(editor.tree_model.indexFromItem(editor._tree_items["loops"]), True)

    root_item = editor._tree_items["root"]
    assert _tree_item_count(root_item) == len(profile.nodes)
    assert editor._tree_items["loops"].text() == "Loops"
    assert editor._tree_items["loops"].parent().child(editor._tree_items["loops"].row(), 1).text() == 'type:"Loops"'


def test_tree_editor_action_column_adds_child_and_removes_node(monkeypatch):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    profile = _profile(
        [
            TreeOrganizationNode("loops", "root", "Loops", 'type:"Loops"', "system", 1),
            TreeOrganizationNode("bass", "loops", "Bass", 'cat:"Bass"', "system", 1),
        ]
    )
    editor = _tree_editor(monkeypatch, profile)
    editor.tree.setExpanded(editor.tree_model.indexFromItem(editor._tree_items["loops"]), True)
    editor.tree.setExpanded(editor.tree_model.indexFromItem(editor._tree_items["bass"]), True)

    def click_remove(node_id: str) -> None:
        index = editor.tree_model.indexFromItem(editor._tree_items[node_id]).siblingAtColumn(2)
        rect = editor.tree.visualRect(index)
        x = rect.center().x()
        y = rect.center().y()
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(x, y),
            QPointF(x, y),
            QPointF(x, y),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        editor.tree.mousePressEvent(event)
        QApplication.processEvents()

    def click_add_row(node_id: str) -> None:
        parent_item = editor._tree_items[node_id]
        add_item = parent_item.child(parent_item.rowCount() - 1, 0)
        index = editor.tree_model.indexFromItem(add_item)
        rect = editor.tree.visualRect(index)
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(rect.center()),
            QPointF(rect.center()),
            QPointF(rect.center()),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        editor.tree.mousePressEvent(event)
        QApplication.processEvents()

    action_index = editor.tree_model.indexFromItem(editor._tree_items["bass"]).siblingAtColumn(2)
    assert action_index.data() == ""
    assert not action_index.data(Qt.DecorationRole).isNull()
    click_add_row("bass")
    child = editor._selected_node()
    assert child is not None
    assert child.parent_id == "bass"
    click_remove(child.id)
    assert child.id not in editor._node_by_id()


def test_tree_editor_undo_redo_restores_tree_edits(monkeypatch):
    profile = _profile([TreeOrganizationNode("kicks", "root", "Kicks", 'cat:"Kicks"', "system", 1)])
    editor = _tree_editor(monkeypatch, profile)

    editor._select_node("kicks")
    editor._add_child_node()
    child = editor._selected_node()
    assert child is not None
    assert child.parent_id == "kicks"
    child_id = child.id

    editor._undo_tree_edit()
    assert child_id not in editor._node_by_id()
    assert editor.btn_redo.isEnabled()

    editor._redo_tree_edit()
    assert child_id in editor._node_by_id()
    assert editor._node_by_id()[child_id].parent_id == "kicks"



def test_tree_editor_defaults_to_tree_list_and_returns_after_save(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.tree_organization import TreeOrganizationEditor

    _app = QApplication.instance() or QApplication([])
    profile = _profile([TreeOrganizationNode("kicks", "root", "Kicks", 'cat:"Kicks"', "system", 1)])
    editor = TreeOrganizationEditor([profile], profile, [_record("kick.wav", category="Kicks")], embedded=True)
    editor.show()
    _app.processEvents()
    editor._profiles = [profile]
    editor._active_profile_id = profile.id
    editor._show_profile_list()

    assert editor.page_stack.currentWidget() is editor.profile_list_page

    editor._show_editor_page(profile)
    assert editor.page_stack.currentWidget() is editor.editor_page
    editor.btn_back_to_list.click()
    assert editor.page_stack.currentWidget() is editor.profile_list_page
    editor._show_editor_page(profile)
    saved = []
    editor.profileSaved.connect(saved.append)
    editor._save()

    assert saved
    assert editor.page_stack.currentWidget() is editor.profile_list_page


def test_tree_editor_rejects_duplicate_name_on_save(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.tree_organization import TreeOrganizationEditor

    _app = QApplication.instance() or QApplication([])
    first = _profile([TreeOrganizationNode("kicks", "root", "Kicks", 'cat:"Kicks"', "system", 1)])
    first = TreeOrganizationProfile(first.id, "My Tree", first.root_node_id, first.nodes, "now", "now")
    second = TreeOrganizationProfile("other", "Other Tree", first.root_node_id, first.nodes, "now", "now")
    editor = TreeOrganizationEditor([first, second], second, [_record("kick.wav", category="Kicks")], embedded=True)
    editor._ensure_editor_built()
    saved = []
    editor.profileSaved.connect(saved.append)

    editor.profile_name.setText("  my   tree ")
    editor._save()

    assert saved == []
    assert 'already named "my   tree"' in editor.validation_label.text()


def test_tree_editor_list_selection_controls_unified_set_active(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.tree_organization import TreeOrganizationEditor

    _app = QApplication.instance() or QApplication([])
    active = _profile([TreeOrganizationNode("kicks", "root", "Kicks", 'cat:"Kicks"', "system", 1)])
    other = TreeOrganizationProfile("other", "Other Tree", "root", active.nodes, "now", "now")
    editor = TreeOrganizationEditor([active, other], active, [_record("kick.wav", category="Kicks")], embedded=True)

    assert editor.btn_set_active_from_list.isEnabled() is False
    editor._select_profile_row(other.id)
    assert editor.btn_set_active_from_list.isEnabled() is True

    applied = []
    editor.profileApplied.connect(applied.append)
    editor._set_selected_profile_active()

    assert applied == [other]
    assert editor.btn_set_active_from_list.isEnabled() is False


def test_tree_editor_save_selects_saved_profile_in_embedded_list(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.tree_organization import TreeOrganizationEditor

    _app = QApplication.instance() or QApplication([])
    first = _profile([TreeOrganizationNode("kicks", "root", "Kicks", 'cat:"Kicks"', "system", 1)])
    second = TreeOrganizationProfile("other", "Other Tree", first.root_node_id, first.nodes, "now", "now")
    editor = TreeOrganizationEditor([first, second], first, [_record("kick.wav", category="Kicks")], embedded=True)

    editor._show_editor_page(second)
    editor._save()

    assert editor._selected_profile_id == second.id
    assert editor.page_stack.currentWidget() is editor.profile_list_page


def test_tree_editor_default_active_shows_active_label(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel
    from gui.widgets.tree_organization import TreeOrganizationEditor

    _app = QApplication.instance() or QApplication([])
    profile = _profile([TreeOrganizationNode("kicks", "root", "Kicks", 'cat:"Kicks"', "system", 1)])
    editor = TreeOrganizationEditor([profile], None, [_record("kick.wav", category="Kicks")], embedded=True)

    labels = [
        label.text()
        for label in editor.profile_list_page.findChildren(QLabel)
        if label.objectName() == "TreeProfileActive"
    ]
    assert "Active" in labels


def test_tree_editor_new_tree_prompts_for_name(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.tree_organization import TreeOrganizationEditor

    _app = QApplication.instance() or QApplication([])
    profile = _profile([TreeOrganizationNode("kicks", "root", "Kicks", 'cat:"Kicks"', "system", 1)])
    editor = TreeOrganizationEditor([profile], profile, [_record("kick.wav", category="Kicks")], embedded=True)

    editor._new_profile_from_list()
    editor.new_tree_name.setText("Studio Layout")
    editor._confirm_new_profile_name()

    assert editor.profile_name.text() == "Studio Layout"
    assert editor.page_stack.currentWidget() is editor.editor_page


def test_tree_editor_new_tree_rejects_duplicate_name(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.tree_organization import TreeOrganizationEditor

    _app = QApplication.instance() or QApplication([])
    profile = _profile([TreeOrganizationNode("kicks", "root", "Kicks", 'cat:"Kicks"', "system", 1)])
    profile = TreeOrganizationProfile(profile.id, "My Tree", profile.root_node_id, profile.nodes, "now", "now")
    editor = TreeOrganizationEditor([profile], profile, [_record("kick.wav", category="Kicks")], embedded=True)

    editor._new_profile_from_list()
    editor.new_tree_name.setText("My Tree")

    assert editor.new_tree_error.text() == "Not available"
    assert not editor.new_tree_error.isHidden()
    assert editor.btn_create_tree.isEnabled() is False
    assert editor.page_stack.currentWidget() is editor.profile_list_page

    editor.new_tree_name.setText("Other Tree")
    assert editor.new_tree_error.text() == "Available"
    assert editor.btn_create_tree.isEnabled() is True
    editor._confirm_new_profile_name()
    assert editor.profile_name.text() == "Other Tree"


def test_tree_editor_filter_suggestions_use_saved_filters_and_record_values(monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QWidget
    from gui.widgets.tree_organization import TreeOrganizationEditor

    _app = QApplication.instance() or QApplication([])
    parent = QWidget()
    parent.settings_controller = SimpleNamespace(
        get_saved_filters=lambda: [{"name": "Warm kicks", "query": 'category:"Kicks" tag:"warm"'}]
    )
    profile = _profile([TreeOrganizationNode("kicks", "root", "Kicks", 'cat:"Kicks"', "system", 1)])
    record = _record("kick.wav", category="Kicks")
    record.pack = "ADEN Massamolla"
    record.audio_type = "Oneshots"
    record.tags = ["warm"]
    record.source_path = Path("D:/Samples/ADEN/kick.wav")

    editor = TreeOrganizationEditor([profile], profile, [record], parent, embedded=True)
    editor._ensure_editor_built()

    assert 'category:"Kicks" tag:"warm"' in editor.node_filter._saved_filter_suggestions
    assert 'packname:"ADEN Massamolla"' in editor.node_filter._suggestions
    assert 'type:"Oneshots"' in editor.node_filter._matching_suggestions("on")


@pytest.mark.parametrize("query", [
    'tag:"warm" AND ca',
    'tag:"warm" OR ca',
    'tag:"warm", ca',
    'tag:"warm" & ca',
    'tag:"warm" | ca',
])
def test_filter_suggestion_line_edit_replaces_fragment_after_query_operators(monkeypatch, query):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.filter_suggestion_line_edit import FilterSuggestionLineEdit

    _app = QApplication.instance() or QApplication([])
    edit = FilterSuggestionLineEdit()
    edit.set_suggestions(['category:"Kicks"'], [])
    edit.setText(query)
    edit.setCursorPosition(len(query))

    edit._accept_completion('category:"Kicks"')

    assert edit.text() == query[:-2] + 'category:"Kicks"'


def test_filter_suggestion_line_edit_preserves_query_after_current_fragment(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.filter_suggestion_line_edit import FilterSuggestionLineEdit

    _app = QApplication.instance() or QApplication([])
    edit = FilterSuggestionLineEdit()
    edit.set_suggestions(['category:"Kicks"'], [])
    edit.setText('ca OR tag:"warm"')
    edit.setCursorPosition(2)

    edit._accept_completion('category:"Kicks"')

    assert edit.text() == 'category:"Kicks" OR tag:"warm"'


def test_filter_suggestion_line_edit_displays_full_query_completion_context(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.filter_suggestion_line_edit import FilterSuggestionLineEdit

    _app = QApplication.instance() or QApplication([])
    edit = FilterSuggestionLineEdit()
    edit.set_suggestions(['category:"Kicks"'], [])
    edit.setText('tag:"possibleduplicate" OR cate')
    edit.setCursorPosition(len(edit.text()))

    labels = edit._display_candidates(['category:"Kicks"'])

    assert labels == ['tag:"possibleduplicate" OR category:"Kicks"']
    edit._accept_completion(labels[0])
    assert edit.text() == 'tag:"possibleduplicate" OR category:"Kicks"'


@pytest.mark.parametrize("query", [
    'tag:"possibleduplicate" AND tag:"',
    'tag:"possibleduplicate" AND tags:"',
    'tag:"possibleduplicate" OR tag:"',
    "tag:'possibleduplicate' AND tag:\"",
])
def test_filter_suggestion_line_edit_suggests_scoped_field_values_after_operators(monkeypatch, query):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.filter_suggestion_line_edit import FilterSuggestionLineEdit

    _app = QApplication.instance() or QApplication([])
    edit = FilterSuggestionLineEdit()
    edit.set_suggestions(['tag:"Silent"', 'category:"Kicks"'], [])
    edit.setText(query)
    edit.setCursorPosition(len(query))

    assert edit._current_fragment() in {'tag:"', 'tags:"'}
    assert edit._matching_suggestions(edit._current_fragment()) == ['tag:"Silent"']


def test_filter_suggestion_line_edit_does_not_treat_single_quotes_as_search_quotes(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.filter_suggestion_line_edit import FilterSuggestionLineEdit

    _app = QApplication.instance() or QApplication([])
    edit = FilterSuggestionLineEdit()
    edit.set_suggestions(['tag:"Silent"'], [])
    edit.setText("tag:'Sil")
    edit.setCursorPosition(len(edit.text()))

    assert edit._matching_suggestions(edit._current_fragment()) == []


def test_plain_custom_tree_filter_gets_internal_semantic_equivalent_for_routing():
    from unshuffle.logic.tree_organization.routing import semantic_equivalent_query

    records = [
        _record("a.wav", category="Kicks", tags=["possibleduplicate"], row_id=1),
        _record("b.wav", category="Snares", tags=["possibleduplicate"], row_id=2),
    ]

    assert semantic_equivalent_query("possibleduplicate", records) == 'tag:"possibleduplicate"'


def test_hide_subbranches_routes_files_directly_under_custom_node():
    profile = _profile(
        [
            TreeOrganizationNode(
                "dupes",
                "root",
                "Dupes",
                'tag:"dupe"',
                "custom",
                1,
                True,
                True,
            )
        ]
    )
    record = _record("dupe.wav", category="Kicks", subcategory="Hard", tags=["dupe"])

    route = TreeRouteBuilder().routes_for([record], profile)[0]

    assert [part.label for part in route.parts] == ["Dupes"]


def test_library_tree_hide_subbranches_shows_files_directly_under_custom_node(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.models.library_tree import LibraryTreeModel, NODE_TYPE_ROLE

    _app = QApplication.instance() or QApplication([])
    profile = _profile(
        [
            TreeOrganizationNode(
                "dupes",
                "root",
                "Dupes",
                'tag:"dupe"',
                "custom",
                1,
                True,
                True,
            )
        ]
    )
    model = LibraryTreeModel()
    model.set_custom_tree_profile(profile)
    model.rebuild([
        _record("loop.wav", audio_type="Loops", category="Kicks", tags=["dupe"]),
        _record("shot.wav", audio_type="Oneshots", category="Snares", tags=["dupe"]),
    ])

    dupes_index = model.index_for_path(("Dupes",))

    assert dupes_index is not None
    item = model.itemFromIndex(dupes_index)
    assert item.rowCount() == 2
    assert {item.child(row).data(NODE_TYPE_ROLE) for row in range(item.rowCount())} == {"file"}


def test_tree_editor_save_apply_includes_unupdated_hide_subbranches(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.tree_organization import TreeOrganizationEditor

    _app = QApplication.instance() or QApplication([])
    profile = _profile([TreeOrganizationNode("dupes", "root", "Dupes", 'tag:"dupe"', "custom", 1)])
    editor = TreeOrganizationEditor([], profile, [_record("dupe.wav", tags=["dupe"])], embedded=True)
    editor._ensure_editor_built()
    editor._select_node("dupes")
    editor.node_hide_subbranches.setChecked(True)

    profile_from_ui = editor._profile_from_ui()

    dupes = next(node for node in profile_from_ui.nodes if node.id == "dupes")
    assert dupes.hide_subbranches is True


def test_taxonomy_tree_organization_sidebar_requests_panel_when_missing(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QWidget
    from gui.widgets.system_page import SystemPage

    _app = QApplication.instance() or QApplication([])
    page = SystemPage()
    requested = []
    page.treeOrganizationRequested.connect(lambda: requested.append(True))

    page._open_tree_organization()
    assert requested == [True]

    panel = QWidget()
    page.set_tree_organization_panel(panel)
    assert page.stack.currentWidget() is panel


def test_system_page_refreshes_section_header_theme(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.utils import styles
    from gui.widgets.system_page import SystemPage

    _app = QApplication.instance() or QApplication([])
    page = SystemPage()
    header, _label = page._section_headers[0]

    old_hover = styles.ColorPalette.BG_HOVER
    try:
        styles.ColorPalette.BG_HOVER = "#123456"
        page.refresh_theme()
        assert "#123456" in header.styleSheet()
    finally:
        styles.ColorPalette.BG_HOVER = old_hover


def test_tree_editor_selecting_row_opens_details_and_edits_node(monkeypatch):
    profile = _profile(
        [
            TreeOrganizationNode("kicks", "root", "Kicks", 'cat:"Kicks"', "system", 1),
        ]
    )
    editor = _tree_editor(monkeypatch, profile)

    editor._select_node("kicks")
    assert editor.detail_panel.isVisible()
    assert editor.node_name.text() == "Kicks"
    assert editor.node_filter.text() == 'cat:"Kicks"'
    editor.node_name.setText("Hard Kicks")
    editor.node_filter.setText('tag:"hard"')
    editor.node_type.setCurrentText("custom")
    editor._update_selected()

    node = editor._node_by_id()["kicks"]
    assert node.name == "Hard Kicks"
    assert node.filter_query == 'tag:"hard"'
    assert node.node_type == "custom"
    assert editor._tree_items["kicks"].text() == "Hard Kicks"


def test_tree_editor_add_child_assigns_parent_and_order(monkeypatch):
    profile = _profile([TreeOrganizationNode("kicks", "root", "Kicks", 'cat:"Kicks"', "system", 1)])
    editor = _tree_editor(monkeypatch, profile)

    editor._select_node("kicks")
    editor._add_child_node()
    child = editor._selected_node()
    assert child is not None
    assert child.parent_id == "kicks"
    child_id = child.id
    editor._add_child_node()
    second_child = editor._selected_node()
    assert second_child is not None
    assert second_child.parent_id == child_id
    editor._select_node("kicks")
    editor._add_child_node()
    sibling_child = editor._selected_node()
    assert sibling_child is not None
    assert sibling_child.parent_id == "kicks"
    assert sibling_child.sort_order == 2
    assert editor._node_by_id()[child_id].sort_order == 1


def test_tree_editor_add_child_waits_for_unsaved_filter_update(monkeypatch):
    profile = _profile([TreeOrganizationNode("kicks", "root", "Kicks", 'cat:"Kicks"', "system", 1)])
    editor = _tree_editor(monkeypatch, profile)

    editor._select_node("kicks")
    assert editor.btn_child.isEnabled()

    editor.node_filter.setText('tag:"hard"')

    assert not editor.btn_child.isEnabled()
    editor._add_child_node()
    assert len(editor._children_by_parent().get("kicks", [])) == 0

    editor._update_selected()

    assert editor.btn_child.isEnabled()
    editor._add_child_node()
    child = editor._selected_node()
    assert child is not None
    assert child.parent_id == "kicks"


def test_tree_editor_move_reparents_reorders_and_rejects_invalid_moves(monkeypatch):
    profile = _profile(
        [
            TreeOrganizationNode("parent", "root", "Parent", 'tag:"parent"', "custom", 1),
            TreeOrganizationNode("child", "parent", "Child", 'cat:"Kicks"', "system", 1),
            TreeOrganizationNode("sibling", "root", "Sibling", 'tag:"sibling"', "custom", 2),
        ]
    )
    editor = _tree_editor(monkeypatch, profile)

    editor._move_node("parent", "child", -1)
    assert editor._node_by_id()["parent"].parent_id == "root"

    editor._move_node("child", "root", 0)
    assert editor._node_by_id()["child"].parent_id == "root"
    assert editor._node_by_id()["child"].sort_order == 1
    assert editor._node_by_id()["parent"].sort_order == 2
    assert editor._node_by_id()["sibling"].sort_order == 3

    editor._move_node("root", "child", -1)
    assert editor._node_by_id()["root"].parent_id is None


def test_tree_editor_move_rejects_stale_node_or_parent_ids(monkeypatch):
    profile = _profile([TreeOrganizationNode("parent", "root", "Parent", 'tag:"parent"', "custom", 1)])
    editor = _tree_editor(monkeypatch, profile)

    editor._move_node("missing", "root", 0)
    editor._move_node("parent", "missing", 0)

    assert editor._node_by_id()["parent"].parent_id == "root"
    assert editor._undo_states == []


def test_tree_editor_indexes_and_rendering_are_cycle_safe(monkeypatch):
    profile = TreeOrganizationProfile(
        "p1",
        "Custom",
        "root",
        [
            TreeOrganizationNode("root", None, "Root", None, "system", 0, True),
            TreeOrganizationNode("a", "b", "A", 'tag:"a"', "custom", 1),
            TreeOrganizationNode("b", "a", "B", 'tag:"b"', "custom", 1),
        ],
        "now",
        "now",
    )

    editor = _tree_editor(monkeypatch, profile)
    result = TreeOrganizationResolver().validate_profile(profile, [])

    assert not result.valid
    assert any("cycle" in message.lower() for message in result.blocking_messages)
    assert editor._descendant_ids("a") == {"a", "b"}
    assert "root" in editor._tree_items


def test_tree_editor_move_defers_routed_count_rebuild(monkeypatch):
    profile = _profile(
        [
            TreeOrganizationNode("parent", "root", "Parent", 'tag:"parent"', "custom", 1),
            TreeOrganizationNode("child", "parent", "Child", 'cat:"Kicks"', "system", 1),
            TreeOrganizationNode("sibling", "root", "Sibling", 'tag:"sibling"', "custom", 2),
        ]
    )
    editor = _tree_editor(monkeypatch, profile)
    calls = []

    def fake_routed_records(self, profile, records):
        calls.append((profile, records))
        return {}

    monkeypatch.setattr(TreeOrganizationResolver, "routed_records", fake_routed_records)
    editor._move_node("child", "root", 0)

    assert calls == []
    assert editor._counts_dirty
    editor._refresh_counts_after_idle()
    assert len(calls) == 1


def test_tree_editor_drop_plan_uses_row_zones_and_rejects_invalid_descendant_drop(monkeypatch):
    from PySide6.QtCore import QPoint

    profile = _profile(
        [
            TreeOrganizationNode("parent", "root", "Parent", 'tag:"parent"', "custom", 1),
            TreeOrganizationNode("child", "parent", "Child", 'cat:"Kicks"', "system", 1),
            TreeOrganizationNode("sibling", "root", "Sibling", 'tag:"sibling"', "custom", 2),
        ]
    )
    editor = _tree_editor(monkeypatch, profile)
    editor.tree.setExpanded(editor.tree_model.indexFromItem(editor._tree_items["parent"]), True)
    parent_index = editor.tree_model.indexFromItem(editor._tree_items["parent"])
    sibling_index = editor.tree_model.indexFromItem(editor._tree_items["sibling"])
    sibling_rect = editor.tree.visualRect(sibling_index)

    editor.tree._drag_node_id = "child"
    assert editor.tree._drop_plan(QPoint(sibling_rect.center().x(), sibling_rect.top() + 1)) == ("root", 1)
    assert editor.tree._drop_plan(sibling_rect.center()) == ("sibling", -1)
    assert editor.tree._drop_plan(QPoint(sibling_rect.center().x(), sibling_rect.bottom() - 1)) == ("root", 2)

    parent_rect = editor.tree.visualRect(parent_index)
    editor.tree._drag_node_id = "parent"
    child_index = editor.tree_model.indexFromItem(editor._tree_items["child"])
    assert editor.tree._drop_plan(editor.tree.visualRect(child_index).center()) is None
    assert editor.tree._drop_plan(parent_rect.center()) is None


def test_tree_editor_empty_space_root_drop_appends_to_root_children(monkeypatch):
    from PySide6.QtCore import QPoint

    profile = _profile(
        [
            TreeOrganizationNode("parent", "root", "Parent", 'tag:"parent"', "custom", 1),
            TreeOrganizationNode("sibling", "root", "Sibling", 'tag:"sibling"', "custom", 2),
            TreeOrganizationNode("child", "parent", "Child", 'cat:"Kicks"', "system", 1),
        ]
    )
    editor = _tree_editor(monkeypatch, profile)

    editor.tree._drag_node_id = "child"

    assert editor.tree._drop_plan(QPoint(1, editor.tree.viewport().height() + 50)) == ("root", 2)


def test_tree_editor_internal_drag_commits_on_mouse_release(monkeypatch):
    from PySide6.QtCore import Qt

    profile = _profile(
        [
            TreeOrganizationNode("parent", "root", "Parent", 'tag:"parent"', "custom", 1),
            TreeOrganizationNode("child", "parent", "Child", 'cat:"Kicks"', "system", 1),
            TreeOrganizationNode("sibling", "root", "Sibling", 'tag:"sibling"', "custom", 2),
        ]
    )
    editor = _tree_editor(monkeypatch, profile)

    editor.tree._drag_node_id = "child"
    editor.tree._dragging_internal = True
    editor.tree._drop_target = ("root", 0)
    editor.tree.mouseReleaseEvent(type("Evt", (), {"button": lambda self: Qt.LeftButton, "accept": lambda self: None})())
    assert editor._node_by_id()["child"].parent_id == "root"
    assert editor._node_by_id()["child"].sort_order == 1
    assert not editor.tree._dragging_internal


def test_tree_editor_root_controls_are_locked(monkeypatch):
    profile = _profile([TreeOrganizationNode("kicks", "root", "Kicks", 'cat:"Kicks"', "system", 1)])
    editor = _tree_editor(monkeypatch, profile)

    editor._select_node("root")
    assert not editor.node_name.isEnabled()
    assert not editor.node_filter.isEnabled()
    assert not editor.node_type.isEnabled()
    assert not editor.btn_remove.isEnabled()
    assert editor.node_actions.isHidden()
    root_action = editor.tree_model.indexFromItem(editor._tree_items["root"]).siblingAtColumn(2)
    assert root_action.data() == ""


def test_tree_editor_read_only_action_cell_does_not_request_delete(monkeypatch):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    profile = _profile(
        [
            TreeOrganizationNode("utility", "root", "Utility", 'type:"Non-Audio Assets"', "system", 1),
        ]
    )
    editor = _tree_editor(monkeypatch, profile)
    deleted = []
    editor.tree.deleteNodeRequested.connect(deleted.append)
    index = editor.tree_model.indexFromItem(editor._tree_items["utility"]).siblingAtColumn(2)
    rect = editor.tree.visualRect(index)
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(rect.center()),
        QPointF(rect.center()),
        QPointF(rect.center()),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    editor.tree.mousePressEvent(event)
    QApplication.processEvents()

    assert deleted == []


def test_tree_editor_uses_icons_for_edit_and_delete_actions(monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QPushButton

    profile = _profile([TreeOrganizationNode("kicks", "root", "Kicks", 'cat:"Kicks"', "system", 1)])
    editor = _tree_editor(monkeypatch, profile)

    editor._select_node("kicks")
    assert editor.btn_update.text() == ""
    assert not editor.btn_update.icon().isNull()
    assert editor.btn_remove.text() == ""
    assert not editor.btn_remove.icon().isNull()

    action = editor.tree_model.indexFromItem(editor._tree_items["kicks"]).siblingAtColumn(2)
    assert action.data() == ""
    assert not action.data(Qt.DecorationRole).isNull()

    editor._profiles = [profile]
    editor._show_profile_list()
    buttons = editor.profile_scroll_content.findChildren(QPushButton)
    icon_buttons = [button for button in buttons if button.toolTip() in {"Edit this tree", "Delete tree"}]
    assert icon_buttons
    assert all(button.text() == "" and not button.icon().isNull() for button in icon_buttons)


def test_tree_editor_utility_system_folder_is_locked_but_children_are_editable(monkeypatch):
    profile = _profile(
        [
            TreeOrganizationNode("utility", "root", "Utility", 'type:"Non-Audio Assets"', "system", 1),
            TreeOrganizationNode("docs", "utility", "Docs", 'cat:"Docs"', "system", 1),
        ]
    )
    editor = _tree_editor(monkeypatch, profile)

    editor._select_node("utility")
    assert not editor.node_name.isEnabled()
    assert not editor.node_filter.isEnabled()
    assert not editor.btn_update.isEnabled()
    assert not editor.btn_remove.isEnabled()
    assert editor.node_actions.isHidden()
    utility_item = editor._tree_items["utility"]
    utility_action = editor.tree_model.indexFromItem(utility_item).siblingAtColumn(2)
    assert utility_action.data() == ""
    assert utility_item.rowCount() == 1
    editor._insert_node("utility")
    assert len(editor._children_by_parent().get("utility", [])) == 1

    editor._select_node("docs")
    assert editor.node_name.isEnabled()
    assert editor.node_filter.isEnabled()
    assert editor.btn_update.isEnabled()
    assert editor.btn_remove.isEnabled()
    assert not editor.node_actions.isHidden()


def test_tree_editor_profile_name_field_drives_saved_profile_name(monkeypatch):
    profile = _profile([TreeOrganizationNode("kicks", "root", "Kicks", 'cat:"Kicks"', "system", 1)])
    editor = _tree_editor(monkeypatch, profile)

    editor.profile_name.setText("My Routing")
    assert editor.profile().name == "My Routing"


def test_tree_editor_preview_counts_follow_routed_tree_not_raw_filters(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.widgets.tree_organization import TreeOrganizationEditor

    _app = QApplication.instance() or QApplication([])
    records = [
        _record("aden_kick.wav", category="Kicks", tags=["aden"], row_id=1),
        _record("plain_kick.wav", category="Kicks", tags=[], row_id=2),
        _record("bass.wav", category="Bass", tags=[], row_id=3),
    ]
    profile = _profile(
        [
            TreeOrganizationNode("aden", "root", "Aden", 'tag:"aden"', "custom", 1),
            TreeOrganizationNode("aden_kicks", "aden", "Aden Kicks", 'cat:"Kicks"', "system", 1),
            TreeOrganizationNode("kicks", "root", "Kicks", 'cat:"Kicks"', "system", 2),
            TreeOrganizationNode("fallback", "root", "Other", None, "fallback", 99),
        ]
    )
    editor = TreeOrganizationEditor([], profile, records)
    editor._ensure_editor_built()
    editor._refresh_counts_after_idle()
    nodes = {node.id: node for node in editor._nodes}

    assert editor._preview_count(nodes["root"]) == 3
    assert editor._preview_count(nodes["aden"]) == 1
    assert editor._preview_count(nodes["aden_kicks"]) == 1
    assert editor._preview_count(nodes["kicks"]) == 1
    assert editor._preview_count(nodes["fallback"]) == 1


def test_library_tree_model_folder_counts_show_sample_totals(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.models.library_tree import LibraryTreeModel, COUNT_ROLE

    _app = QApplication.instance() or QApplication([])
    model = LibraryTreeModel()
    
    # We have 3 bass oneshot records in pack A, and 2 bass oneshot records in pack B.
    # The structure is:
    # Oneshots (5 samples) -> Bass (5 samples) -> Sub (5 samples) -> Pack A (3 files), Pack B (2 files)
    records = [
        _record("bass1.wav", pack="Pack A", category="Bass", audio_type="Oneshots", row_id=1),
        _record("bass2.wav", pack="Pack A", category="Bass", audio_type="Oneshots", row_id=2),
        _record("bass3.wav", pack="Pack A", category="Bass", audio_type="Oneshots", row_id=3),
        _record("bass4.wav", pack="Pack B", category="Bass", audio_type="Oneshots", row_id=4),
        _record("bass5.wav", pack="Pack B", category="Bass", audio_type="Oneshots", row_id=5),
    ]
    
    model.rebuild(records)
    root = model.invisibleRootItem()
    
    # Root level has "Oneshots"
    assert root.rowCount() == 1
    oneshots_item = root.child(0)
    assert oneshots_item.text() == "Oneshots (5)"
    assert oneshots_item.data(COUNT_ROLE) == 5
    
    # Next level has "Bass"
    assert oneshots_item.rowCount() == 1
    bass_item = oneshots_item.child(0)
    assert bass_item.text() == "Bass (5)"
    assert bass_item.data(COUNT_ROLE) == 5
    
    # Next level has "Sub"
    assert bass_item.rowCount() == 1
    sub_item = bass_item.child(0)
    assert sub_item.text() == "Sub (5)"
    assert sub_item.data(COUNT_ROLE) == 5
    
    # Next level has Pack A and Pack B
    assert sub_item.rowCount() == 2
    pack_a_item = sub_item.child(0) if "Pack A" in sub_item.child(0).text() else sub_item.child(1)
    pack_b_item = sub_item.child(1) if "Pack A" in sub_item.child(0).text() else sub_item.child(0)
    
    assert "Pack A (3)" in pack_a_item.text()  # Pack A is a leaf folder, so it has 3 files
    assert pack_a_item.data(COUNT_ROLE) == 3
    assert "Pack B (2)" in pack_b_item.text()  # Pack B has 2 files
    assert pack_b_item.data(COUNT_ROLE) == 2


def test_library_tree_model_custom_sample_icon(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui.models.library_tree import LibraryTreeModel

    _app = QApplication.instance() or QApplication([])
    model = LibraryTreeModel()
    
    # We have 1 sample and 1 utility asset
    records = [
        _record("bass.wav", pack="Pack A", category="Bass", audio_type="Oneshots", row_id=1),
        _record("cover.jpg", pack="Pack A", category="", subcategory="", audio_type="Non-Audio Assets", row_id=2),
    ]
    
    model.rebuild(records)
    
    # Find root index for Oneshots and Utility
    oneshots_idx = model.index(0, 0) if "Oneshots" in model.index(0, 0).data() else model.index(1, 0)
    utility_idx = model.index(1, 0) if "Oneshots" in model.index(0, 0).data() else model.index(0, 0)
    
    # Populate Oneshots -> Bass -> Sub -> Pack A
    model.populate_index(oneshots_idx)
    bass_idx = model.index(0, 0, oneshots_idx)
    model.populate_index(bass_idx)
    sub_idx = model.index(0, 0, bass_idx)
    model.populate_index(sub_idx)
    pack_idx = model.index(0, 0, sub_idx)
    model.populate_index(pack_idx)
    
    # bass.wav item is a child of Pack A
    bass_file_idx = model.index(0, 0, pack_idx)
    assert bass_file_idx.data() == "bass.wav"
    bass_file_item = model.itemFromIndex(bass_file_idx)
    assert not bass_file_item.icon().isNull()
    assert bass_file_item.icon().availableSizes() == model._sample_icon.availableSizes()
    
    # Populate Utility -> Pack A
    # Populate Utility -> "" -> Pack A (Other subcategory is collapsed/bypassed)
    model.populate_index(utility_idx)
    cat_utility_idx = model.index(0, 0, utility_idx)  # category is ""
    model.populate_index(cat_utility_idx)
    pack_utility_idx = model.index(0, 0, cat_utility_idx)  # pack is "Pack A" (Other is bypassed)
    model.populate_index(pack_utility_idx)
    
    # cover.jpg is a child of Pack A under Utility
    cover_file_idx = model.index(0, 0, pack_utility_idx)
    assert cover_file_idx.data() == "cover.jpg"
    cover_file_item = model.itemFromIndex(cover_file_idx)
    assert cover_file_item.icon() != model._sample_icon

    # Verify the custom file foreground colors
    from PySide6.QtCore import Qt
    from gui.models.library_tree import tree_file_sequence_color
    assert model.data(bass_file_idx, Qt.ForegroundRole) == tree_file_sequence_color(0)
    assert model.data(cover_file_idx, Qt.ForegroundRole) == tree_file_sequence_color(0)

