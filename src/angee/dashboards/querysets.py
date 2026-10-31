"""Dashboard catalogue, target and widget selection."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self

from django.db import models

from angee.base.models.trash import ArchiveQuerySet, TrashQuerySet
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet

if TYPE_CHECKING:
    from angee.dashboards.models import Dashboard, DashboardWidget


class DashboardQuerySet[DashboardT: Dashboard](
    TrashQuerySet[DashboardT],
    AngeeHistoryQuerySet[DashboardT],
):
    """Actor-scoped dashboards with removal reserved for personal snapshots."""

    def catalogue(self, owner: Any) -> Self:
        """Readable personal, owned and installed entries, including removed personal rows."""

        return self.filter(
            models.Q(scope="personal") | models.Q(owner=owner) | models.Q(is_baseline=True),
        ).order_by("sqid")

    def target_candidates(self, owner: Any, scope: str, scope_key: str | None) -> Self:
        """Readable authored snapshots precede an installed baseline for this target."""

        if scope == "personal":
            raise ValueError("Personal dashboards resolve by public id.")
        return self.filter(
            models.Q(owner=owner) | models.Q(is_baseline=True), scope=scope, scope_key=scope_key,
        ).order_by(models.F("owner").desc(nulls_last=True))

    def trash_targets(self) -> Self:
        """Installed and scoped snapshots reset through their owning verb."""

        return self.filter(scope="personal")



class DashboardWidgetQuerySet[WidgetT: DashboardWidget](ArchiveQuerySet[WidgetT], AngeeHistoryQuerySet[WidgetT]):
    """Archive scopes layered over dashboard widget reads."""
