"""Composer-time resource entry kinds contributed by installed addons."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, ClassVar

from angee.impl import ImplBase

if TYPE_CHECKING:
    from angee.resources.entries import ResourceEntry, ResourceGroup
    from angee.resources.loader import AngeeResource


class ResourceEntryKind(ImplBase):
    """Parse and validate a non-row declaration inside a resource load.

    ``rows`` and ``grants`` remain the resources addon's native materializers.
    Addons use this seam when the declaration's owner performs the resulting
    write, such as publication of a composed workflow definition.
    """

    registry_setting = "ANGEE_RESOURCE_ENTRY_KIND_CLASSES"
    check_config_form_spec = False
    key: ClassVar[str]

    @classmethod
    def read(cls, entry: ResourceEntry) -> tuple[Any, ...]:
        """Parse one entry without writing application state."""

        raise NotImplementedError

    @classmethod
    def after_load(
        cls,
        declarations: Sequence[Any],
        *,
        loaded_groups: Sequence[tuple[ResourceGroup, AngeeResource]],
        dry_run: bool,
    ) -> None:
        """Validate a complete selected declaration set after model rows load."""

        del declarations, loaded_groups, dry_run


type ResourceKindDeclarations = Mapping[str, tuple[Any, ...]]
"""Parsed custom declarations keyed by their composed resource kind."""
