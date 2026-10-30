"""Resource declarations install shared dashboard baselines."""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from django.core.exceptions import ValidationError
from django.db import models

from angee.resources.loader import AngeeResource

if TYPE_CHECKING:
    from angee.dashboards.models import Dashboard


class DashboardResource(AngeeResource):
    """Keep baseline identity explicit and separate from transferable ownership."""

    def before_import_row(self, row: dict[str, Any], **kwargs: Any) -> None:
        if "is_baseline" in row:
            raise ValidationError({"is_baseline": "Dashboard resources always install baselines."})
        super().before_import_row(row, **kwargs)

    def init_instance(self, row: Mapping[str, Any] | None = None) -> models.Model:
        instance = cast("Dashboard", super().init_instance(row))
        instance.is_baseline = True
        return instance
