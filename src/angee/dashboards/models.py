"""Persistent dashboard snapshots and their widget rows."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, ClassVar, Self

from django.core.exceptions import ValidationError
from django.db import models, router, transaction
from rebac import PermissionDenied, current_actor, database_context, system_context, to_subject_ref

from angee.base.fields.state import StateField
from angee.base.models.audit import AuditMixin
from angee.base.models.locks import ImmutableFieldsMixin, OptimisticLockMixin
from angee.base.models.trash import ArchiveMixin
from angee.dashboards.managers import DashboardManager, DashboardWidgetManager
from angee.dashboards.resources import DashboardResource
from angee.dashboards.validators import (
    DASHBOARD_SCHEMA_VERSION,
    MAX_COLUMNS,
    MAX_LAYOUT_ROWS,
    MAX_WIDGET_HEIGHT,
    canonical_dashboard_snapshot,
    validate_dashboard_queries,
)
from angee.iam.models.base import ProtectedDataModel
from angee.iam.models.ownership import OwnerMixin
from angee.iam.models.trash import ProtectedTrashMixin
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.resources.mixins import ResourceLoadMixin


class Dashboard(
    ImmutableFieldsMixin,
    OptimisticLockMixin,
    ResourceLoadMixin,
    ProtectedTrashMixin,
    OwnerMixin,
    HistoryMixin,
    ProtectedDataModel,
    concrete=True,
):
    """One installed baseline or actor-owned complete dashboard snapshot."""

    record_display_paths: ClassVar[tuple[str, ...]] = ("name",)

    resource_class = DashboardResource
    immutable_fields = ("is_baseline",)
    sqid_prefix = "dsh_"
    rebac_grantable = {"viewer": "share", "editor": "share"}
    history_relations = ("widgets",)

    class DashboardScope(models.TextChoices):
        PERSONAL = "personal", "Personal"
        ADDON = "addon", "Addon"
        RESOURCE = "resource", "Resource"

    is_baseline = models.BooleanField(default=False, editable=False)
    """An installed shared definition, independent of an authored snapshot's owner."""

    scope = StateField(choices_enum=DashboardScope, db_index=True)
    scope_key = models.CharField(max_length=255, null=True, blank=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    columns = models.PositiveSmallIntegerField(default=12)
    spec_version = models.PositiveSmallIntegerField(default=DASHBOARD_SCHEMA_VERSION)
    declaration_revision = models.CharField(max_length=128, blank=True, default="")

    objects = DashboardManager()

    class Meta:
        abstract = True
        ordering = ("name", "sqid")
        rebac_resource_type = "dashboards/dashboard"
        constraints = (
            models.CheckConstraint(
                condition=(
                    models.Q(scope="personal", scope_key__isnull=True)
                    | (
                        models.Q(scope__in=("addon", "resource"))
                        & models.Q(scope_key__isnull=False)
                        & ~models.Q(scope_key="")
                    )
                ),
                name="dashboard_scope_key_shape",
            ),
            models.CheckConstraint(
                condition=models.Q(columns__gte=1, columns__lte=MAX_COLUMNS),
                name="dashboard_columns_range",
            ),
            models.CheckConstraint(condition=models.Q(revision__gte=1), name="dashboard_revision_positive"),
            models.CheckConstraint(
                condition=(models.Q(scope="personal") | models.Q(is_trashed=False)),
                name="dashboard_scoped_not_trashed",
            ),
            models.UniqueConstraint(
                fields=("owner", "scope", "scope_key"),
                condition=models.Q(owner__isnull=False) & ~models.Q(scope="personal"),
                name="dashboard_owner_scoped_target",
            ),
            models.UniqueConstraint(
                fields=("scope", "scope_key"),
                condition=models.Q(is_baseline=True),
                name="dashboard_installed_scoped_target",
            ),
            models.CheckConstraint(
                condition=models.Q(is_baseline=False) | models.Q(owner__isnull=True, scope__in=("addon", "resource")),
                name="dashboard_baseline_shape",
            ),
        )

    def __str__(self) -> str:
        return self.name

    def insert_owner_default(self) -> Any:
        """Keep installed scoped baselines ownerless; authored snapshots name their owner."""

        if self.is_baseline:
            return None
        return super().insert_owner_default()

    def reset_snapshot(self, *, expected_revision: int) -> None:
        """Remove an owner's scoped override under its revision and database lock."""

        actor = current_actor()
        using = self._state.db or router.db_for_write(type(self), instance=self)
        with (
            database_context(using),
            transaction.atomic(using=using),
            system_context(reason="dashboards.reset_snapshot"),
        ):
            locked = type(self).system_queryset(lock=("self",)).using(using).get(pk=self.pk)
            locked.require_revision(expected_revision)
            if locked.scope == self.DashboardScope.PERSONAL or actor is None or to_subject_ref(locked.owner) != actor:
                raise PermissionDenied("Only the owner can reset a scoped dashboard.")
            locked.sudo(reason="dashboards.reset_snapshot").delete(using=using)

    def snapshot(self) -> dict[str, Any]:
        """Return the complete persisted snapshot in the public wire shape."""

        widgets = [
            {
                "schemaVersion": widget.spec_version,
                "id": widget.widget_key,
                **({"definitionRef": widget.definition_ref} if widget.definition_ref else {}),
                "kind": widget.kind,
                "kindVersion": widget.kind_version,
                "title": widget.title,
                "data": widget.data,
                "options": widget.options,
                **({"visibility": widget.visibility} if widget.visibility is not None else {}),
                "x": widget.x,
                "y": widget.y,
                "w": widget.w,
                "h": widget.h,
                "isArchived": widget.is_archived,
            }
            for widget in self.widgets.order_by("sequence", "sqid")
        ]
        return {"schemaVersion": self.spec_version, "columns": self.columns, "widgets": widgets}

    def validate_installed_snapshot(self) -> None:
        """Validate one resource-owned baseline after all declared rows load."""

        if not self.is_baseline:
            raise ValidationError({"is_baseline": "Dashboard resources must name installed baselines."})
        if self.owner_id is not None:
            raise ValidationError({"owner": "Resource-installed dashboards cannot name an actor owner."})
        if self.scope not in {self.DashboardScope.ADDON, self.DashboardScope.RESOURCE}:
            raise ValidationError({"scope": "Resource-installed dashboards require addon or resource scope."})
        snapshot = self.snapshot()
        canonical = canonical_dashboard_snapshot(snapshot)
        if canonical != snapshot:
            raise ValidationError(
                {"widgets": "Installed dashboard widgets must use a canonical, non-overlapping layout."}
            )
        validate_dashboard_queries(snapshot)

    @classmethod
    def after_resource_load(
        cls,
        instances: Iterable[Self],
        *,
        tier: str,
        source: str,
    ) -> None:
        """Validate snapshots, including unchanged resource rows."""

        loaded = tuple(instances)
        for instance in loaded:
            instance.validate_installed_snapshot()
        super().after_resource_load(loaded, tier=tier, source=source)


class DashboardWidget(ResourceLoadMixin, ArchiveMixin, AuditMixin, HistoryMixin, ProtectedDataModel, concrete=True):
    """One versioned widget specification inside a snapshot."""

    sqid_prefix = "dsw_"

    dashboard = models.ForeignKey("dashboards.Dashboard", on_delete=models.CASCADE, related_name="widgets")
    widget_key = models.CharField(max_length=128)
    definition_ref = models.CharField(max_length=128, null=True, blank=True)
    spec_version = models.PositiveSmallIntegerField(default=DASHBOARD_SCHEMA_VERSION)
    kind = models.CharField(max_length=128)
    kind_version = models.PositiveSmallIntegerField(default=1)
    title = models.CharField(max_length=240)
    data = models.JSONField()
    options = models.JSONField(default=dict, blank=True)
    visibility = models.JSONField(null=True, blank=True)
    x = models.PositiveSmallIntegerField(default=0)
    y = models.PositiveSmallIntegerField(default=0)
    w = models.PositiveSmallIntegerField(default=1)
    h = models.PositiveSmallIntegerField(default=1)
    sequence = models.PositiveSmallIntegerField(default=0)

    objects = DashboardWidgetManager()

    class Meta:
        abstract = True
        ordering = ("sequence", "sqid")
        rebac_resource_type = "dashboards/widget"
        constraints = (
            models.UniqueConstraint(fields=("dashboard", "widget_key"), name="dashboard_widget_key"),
            models.UniqueConstraint(
                fields=("dashboard", "definition_ref"),
                condition=models.Q(definition_ref__isnull=False),
                name="dashboard_widget_definition_ref",
            ),
            models.CheckConstraint(
                condition=models.Q(w__gte=1, h__gte=1, h__lte=MAX_WIDGET_HEIGHT),
                name="dashboard_widget_size",
            ),
            models.CheckConstraint(
                condition=models.Q(y__lte=MAX_LAYOUT_ROWS - models.F("h")), name="dashboard_widget_row",
            ),
        )

    def __str__(self) -> str:
        return self.title

    @classmethod
    def after_resource_load(
        cls,
        instances: Iterable[Self],
        *,
        tier: str,
        source: str,
    ) -> None:
        """Revalidate installed parents when a widget resource is loaded alone."""

        loaded = tuple(instances)
        dashboard_model = cls.dashboard.field.related_model
        for using in dict.fromkeys(widget._state.db for widget in loaded):
            dashboard_ids = sorted({widget.dashboard_id for widget in loaded if widget._state.db == using})
            for dashboard in dashboard_model.objects.using(using).filter(pk__in=dashboard_ids, is_baseline=True):
                dashboard.validate_installed_snapshot()
        super().after_resource_load(loaded, tier=tier, source=source)
