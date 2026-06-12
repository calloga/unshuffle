from .indexes import TreeOrganizationIndexCountMixin
from .mutations import TreeOrganizationMutationMixin
from .profiles import TreeOrganizationProfileMixin
from .rendering import TreeOrganizationRenderingMixin


class TreeOrganizationEditorLogicMixin(
    TreeOrganizationProfileMixin,
    TreeOrganizationRenderingMixin,
    TreeOrganizationMutationMixin,
    TreeOrganizationIndexCountMixin,
):
    pass
