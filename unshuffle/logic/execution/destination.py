from __future__ import annotations

from pathlib import Path

from ...core.path_safety import is_path_within_directory
from ...core.prefixes import get_pack_prefix
from ..tree_organization import TreeOrganizationProfile, TreeOrganizationResolver
from ..tree_organization.models import DestinationResolution


class DestinationContainmentError(ValueError):
    """Raised when a resolved build destination escapes the target library."""


def _contained_resolution(
    *,
    target_dir: Path,
    dest_path: Path,
    dest_folder: Path,
    final_name: str,
    used_custom_tree: bool,
) -> DestinationResolution:
    if not is_path_within_directory(dest_folder, target_dir):
        raise DestinationContainmentError(f"Destination folder escapes target library: {dest_folder}")
    if not is_path_within_directory(dest_path, target_dir):
        raise DestinationContainmentError(f"Destination path escapes target library: {dest_path}")

    try:
        relative_path = dest_path.relative_to(target_dir)
    except ValueError as exc:
        raise DestinationContainmentError(f"Destination path escapes target library: {dest_path}") from exc

    return DestinationResolution(
        dest_path=dest_path,
        dest_folder=dest_folder,
        final_name=final_name,
        relative_path=relative_path,
        used_custom_tree=used_custom_tree,
    )


class DefaultDestinationResolver:
    """Default Unshuffle build destination logic extracted from transfer code."""

    def resolve(
        self,
        record,
        target_dir: Path,
        flat: bool,
        no_prefix: bool,
        prefix_map: dict,
    ) -> DestinationResolution:
        at = str(record.audio_type)
        cat = str(record.category)
        sub = str(record.subcategory or "")
        pack = str(record.pack)

        if at in {"Non-Audio Assets", "Utility"}:
            dest_folder = target_dir / "Non-Audio Assets" / pack
            final_name = record.source_path.name
        elif flat:
            base_folder = target_dir / at / cat
            dest_folder = base_folder / sub if sub else base_folder
            prefix = ""
            if not no_prefix:
                prefix = get_pack_prefix(pack, cat, at)
                if prefix:
                    prefix_map[prefix] = pack.replace("_", " / ")
            final_name = f"{prefix}_{record.source_path.name}" if prefix else record.source_path.name
        else:
            base_folder = target_dir / at / cat
            dest_folder = base_folder / sub / pack if sub else base_folder / pack
            final_name = record.source_path.name

        dest_path = dest_folder / final_name
        return _contained_resolution(
            target_dir=target_dir,
            dest_path=dest_path,
            dest_folder=dest_folder,
            final_name=final_name,
            used_custom_tree=False,
        )


class DestinationResolver:
    def __init__(
        self,
        default_resolver: DefaultDestinationResolver | None = None,
        tree_resolver: TreeOrganizationResolver | None = None,
    ):
        self.default_resolver = default_resolver or DefaultDestinationResolver()
        self.tree_resolver = tree_resolver or TreeOrganizationResolver()

    def resolve(
        self,
        record,
        target_dir: Path,
        flat: bool,
        no_prefix: bool,
        prefix_map: dict,
        *,
        active_tree_profile: TreeOrganizationProfile | None = None,
        records: list | None = None,
    ) -> DestinationResolution:
        default = self.default_resolver.resolve(record, target_dir, flat, no_prefix, prefix_map)
        if active_tree_profile is None:
            return default
        if str(getattr(record, "audio_type", "")) in {"Non-Audio Assets", "Utility"}:
            return default
        relative_folder = self.tree_resolver.resolve_record(
            record,
            active_tree_profile,
            records or [record],
            flat=flat,
            append_native=True,
        )
        dest_folder = target_dir / relative_folder
        dest_path = dest_folder / default.final_name
        return _contained_resolution(
            target_dir=target_dir,
            dest_path=dest_path,
            dest_folder=dest_folder,
            final_name=default.final_name,
            used_custom_tree=True,
        )
