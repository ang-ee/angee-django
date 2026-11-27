"""Brand kits load under the lock a change of the default kit takes."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from angee.resources.loader import AngeeResource


class BrandKitResource(AngeeResource):
    """Load kit rows natively, after taking every kit once, before the load writes any row."""

    @classmethod
    def lock_imports(cls, loaded_groups: Sequence[tuple[Any, AngeeResource]]) -> None:
        """Take :meth:`~angee.brandkit.models.BrandKitManager.lock_default` for each kit model the load writes.

        A load writes its rows in file order, in one transaction. A row that changes
        a kit holds that kit, and a later row that moves the default would then ask
        for every kit while holding it, the reverse of every change of default.
        """

        super().lock_imports(loaded_groups)
        kits = {group.model for group, resource in loaded_groups if isinstance(resource, cls)}
        for model in sorted(kits, key=lambda model: model._meta.label):
            model.objects.lock_default()
