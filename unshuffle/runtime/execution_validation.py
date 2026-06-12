"""Execution-time validation helpers."""

from typing import Any, Sequence


def tree_profile_error(active_tree_profile: Any, execution_records: Sequence[Any]) -> str | None:
    if active_tree_profile is None:
        return None

    from ..logic.tree_organization import TreeOrganizationResolver

    validation = TreeOrganizationResolver().validate_profile(active_tree_profile, list(execution_records))
    if validation.valid:
        return None
    return "\n".join(validation.blocking_messages[:5])
