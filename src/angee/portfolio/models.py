"""Products, initiatives, reports, releases, and same-row planning donors.

Products are phasal subjects: their lifecycle carries no target date, health,
or progress. Projects and initiatives are teleological and therefore own health
reports. The project/task donors fold optional portfolio placement onto the
upstream rows only when this addon is composed.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, ClassVar

from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, router, transaction
from django_compose.models import Composable
from rebac import (
    current_actor,
    database_context,
    generic_target,
    system_context,
)

from angee.base.fields.ordering import FractionalRankField
from angee.base.fields.state import StateField
from angee.base.models.audit import AuditMixin
from angee.base.models.hierarchy import HierarchyMixin, HierarchyQuerySet
from angee.base.models.locks import ImmutableFieldsMixin
from angee.base.models.merge_references import MergePolicy
from angee.iam.models.base import ProtectedDataModel
from angee.iam.models.ownership import OwnerMixin
from angee.iam.models.refs import ProtectedRecordRefMixin
from angee.iam.models.roles import role_anchor
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.contrib.angee.managers import AngeeHistoryManager
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet
from angee.resources.mixins import ResourceLoadMixin

if TYPE_CHECKING:
    from angee.projects.models import Project

PortfolioRole = role_anchor("portfolio/role")
"""Table-less REBAC anchor for the const-backed portfolio-admin namespace."""


class ProductLifecycle(models.TextChoices):
    """Phases in a Product's existence, never progress toward completion."""

    IDEA = "idea", "Idea"
    INCUBATING = "incubating", "Incubating"
    ACTIVE = "active", "Active"
    MAINTENANCE = "maintenance", "Maintenance"
    SUNSET = "sunset", "Sunset"
    RETIRED = "retired", "Retired"


class InitiativeStatus(models.TextChoices):
    """Coarse fulfillment lifecycle for strategic initiatives."""

    PROPOSED = "proposed", "Proposed"
    PLANNED = "planned", "Planned"
    ACTIVE = "active", "Active"
    DONE = "done", "Done"
    DROPPED = "dropped", "Dropped"


class Health(models.TextChoices):
    """Health assertion shared by reports and their target projection."""

    ON_TRACK = "on_track", "On track"
    AT_RISK = "at_risk", "At risk"
    OFF_TRACK = "off_track", "Off track"


InitiativeHealth = Health

class InitiativeDateResolution(models.TextChoices):
    """Precision carried by an Initiative target date."""

    DAY = "day", "Day"
    MONTH = "month", "Month"
    QUARTER = "quarter", "Quarter"
    HALF_YEAR = "half_year", "Half year"
    YEAR = "year", "Year"


UpdateHealth = Health

class ReleaseStatus(models.TextChoices):
    """Lifecycle of a Product release artifact."""

    PLANNED = "planned", "Planned"
    IN_PROGRESS = "in_progress", "In progress"
    SHIPPED = "shipped", "Shipped"
    DROPPED = "dropped", "Dropped"


class ProductManager[ProductT: Product](AngeeHistoryManager[ProductT]):
    """Own the idempotent Project-to-Product maturation write."""

    def from_project(self, project: Project) -> ProductT:
        """Return the one Product promoted from ``project`` with provenance.

        Promotion deliberately requires both Project write and ``portfolio_admin``:
        R11 strategy curation wins over owner prerogative at the Product rung.
        """

        if project.pk is None:
            raise ValidationError("A project must be saved before it can be promoted.")
        actor = current_actor()
        with transaction.atomic():
            with system_context(reason="portfolio.product.promote_from_project.lookup"):
                locked_project = type(project).objects.lock_if_supported().get(pk=project.pk)
                existing = self.filter(originated_from_id=project.pk).first()
            if existing is not None:
                if actor is not None:
                    existing.with_actor(actor)
                return existing

            verified_actor, _ = self.effective_actor(strict=True)
            product = self.model(
                name=locked_project.title,
                body=locked_project.body,
                owner_id=locked_project.lead_id,
                originated_from_id=locked_project.pk,
            )
            product.full_clean(validate_unique=False, validate_constraints=False)
            try:
                with transaction.atomic():
                    product = self.insert(product)
            except IntegrityError:
                with system_context(reason="portfolio.product.promote_from_project.concurrent_lookup"):
                    product = self.get(originated_from_id=project.pk)
            if verified_actor is not None:
                product.with_actor(verified_actor)
            return product


class Product(OwnerMixin, HistoryMixin, ProtectedDataModel, concrete=True):
    """A perpetual subject passing through phases rather than toward fulfillment."""

    sqid_prefix = "pdt_"

    name = models.CharField(max_length=240)
    body = models.TextField(blank=True, default="")
    lifecycle = StateField(choices_enum=ProductLifecycle, default=ProductLifecycle.IDEA)
    originated_from = models.ForeignKey(
        "projects.Project",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="promoted_products",
    )

    objects = ProductManager()

    class Meta:
        """Django options for phasal products."""

        abstract = True
        ordering = ("lifecycle", "name", "sqid")
        rebac_resource_type = "portfolio/product"
        constraints = (
            models.UniqueConstraint(
                fields=("originated_from",),
                condition=models.Q(originated_from__isnull=False),
                name="uq_portfolio_product_originated_from",
            ),
        )

    # Promotion lineage stays on the original retained identity.
    merge_policies = {"originated_from": MergePolicy.KEEP}

    def __str__(self) -> str:
        """Return the Product name."""

        return self.name


class InitiativeQuerySet[InitiativeT: Initiative](HierarchyQuerySet[InitiativeT], AngeeHistoryQuerySet[InitiativeT]):
    """Initiative hierarchy writes use the shared saved-row contract."""


_InitiativeManager = AngeeHistoryManager.from_queryset(InitiativeQuerySet)


class Initiative(HierarchyMixin, OwnerMixin, HistoryMixin, ProtectedDataModel, concrete=True):
    """A tree-shaped long-horizon intent whose pushes can be fulfilled."""

    sqid_prefix = "ini_"
    objects = _InitiativeManager()

    name = models.CharField(max_length=240)
    body = models.TextField(blank=True, default="")
    status = StateField(choices_enum=InitiativeStatus, default=InitiativeStatus.PROPOSED)
    health = StateField(
        choices_enum=InitiativeHealth,
        null=True,
        blank=True,
        editable=False,
    )
    health_updated_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        db_index=True,
    )
    target_date = models.DateField(null=True, blank=True, db_index=True)
    target_date_resolution = StateField(
        choices_enum=InitiativeDateResolution,
        default=InitiativeDateResolution.DAY,
    )
    priority = models.IntegerField(default=0, db_index=True)
    sort_order = FractionalRankField()

    class Meta(HierarchyMixin.Meta):
        """Django options carrying the hierarchy index and parent-rank invariant."""

        abstract = True
        ordering = ("parent_id", "sort_order", "name", "sqid")
        rebac_resource_type = "portfolio/initiative"
        constraints = (
            models.UniqueConstraint(
                fields=("parent", "sort_order"),
                name="uq_portfolio_initiative_parent_rank",
                nulls_distinct=False,
            ),
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the Initiative, rejecting ancestry-unsafe subtree moves."""

        parent_changed = False
        saved_parent_id = None
        saved_path = self.path
        if not self._state.adding:
            if hasattr(self, "_hierarchy_saved_parent_id"):
                saved_parent_id = self._hierarchy_saved_parent_id
            else:
                saved_parent_id = self._hierarchy_committed_parent_id()
            parent_changed = saved_parent_id != self.parent_id
        with transaction.atomic():
            super().save(*args, **kwargs)
            if parent_changed:
                try:
                    self._validate_subtree_project_ancestry()
                except ValidationError:
                    self.path = saved_path
                    self._hierarchy_saved_parent_id = saved_parent_id
                    raise

    def _validate_subtree_project_ancestry(self) -> None:
        """Reject a move that makes a subtree Project placement conflict."""

        link_model = apps.get_model("portfolio", "InitiativeProject")
        offenders: dict[Any, str] = {}
        with system_context(reason="portfolio.initiative.validate_subtree_project_ancestry"):
            links = (
                link_model._base_manager.filter(initiative__path__startswith=self.path)
                .select_related("initiative", "project")
                .order_by("project_id", "pk")
            )
            for link in links:
                try:
                    link._validate_ancestry(lock=True)
                except ValidationError:
                    offenders[link.project_id] = str(link.project)
        if offenders:
            projects = ", ".join(offenders[project_id] for project_id in sorted(offenders))
            raise ValidationError(
                {
                    "parent": (
                        "Reparenting would place these projects on both an ancestor "
                        f"and a descendant Initiative: {projects}."
                    )
                }
            )

    def __str__(self) -> str:
        """Return the Initiative name."""

        return self.name


class InitiativeProject(ResourceLoadMixin, AuditMixin, ProtectedDataModel, concrete=True):
    """An ordered Project placement on an ancestry-safe Initiative tree."""

    sqid_prefix = "inp_"

    initiative = models.ForeignKey(
        "portfolio.Initiative",
        on_delete=models.CASCADE,
        related_name="project_links",
    )
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="initiative_links",
    )
    sort_order = FractionalRankField()

    class Meta:
        """Django options for unique placement and per-Initiative ranks."""

        abstract = True
        ordering = ("initiative_id", "sort_order", "sqid")
        rebac_resource_type = "portfolio/initiative_project"
        constraints = (
            models.UniqueConstraint(
                fields=("initiative", "project"),
                name="uq_portfolio_initiative_project",
            ),
            models.UniqueConstraint(
                fields=("initiative", "sort_order"),
                name="uq_portfolio_initiative_project_rank",
            ),
        )

    def clean(self) -> None:
        """Reject a second Project placement on the same ancestry path."""

        super().clean()
        self._validate_ancestry(lock=False)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist only after serializing the ancestry-path validation."""

        with transaction.atomic():
            self._validate_ancestry(lock=True)
            super().save(*args, **kwargs)

    def _validate_ancestry(self, *, lock: bool) -> None:
        """Raise when this Project already sits on an ancestor or descendant."""

        if self.initiative_id is None or self.project_id is None:
            return
        with system_context(reason="portfolio.initiative_project.validate_ancestry"):
            # A retained relation may predate a reparenting; validate the current path.
            initiative_model = type(self).initiative.field.related_model
            initiative = initiative_model._base_manager.get(pk=self.initiative_id)
            placements = type(self).system_queryset().filter(project_id=self.project_id).exclude(pk=self.pk)
            if lock:
                project_model = type(self).project.field.related_model
                project_model.objects.lock_if_supported().filter(pk=self.project_id).exists()
                placements = placements.lock_if_supported()
            conflict = placements.filter(
                models.Q(initiative__path__startswith=initiative.path)
                | models.Q(initiative__path__in=initiative.ancestor_paths())
            ).exists()
        if conflict:
            raise ValidationError({"project": ("A project cannot appear on two initiatives within one ancestry path.")})

    @classmethod
    def after_resource_load(
        cls,
        instances: Iterable[Any],
        *,
        tier: str,
        source: str,
    ) -> None:
        """Apply demo provisioning, then continue the resource hook chain."""

        if tier == "demo":
            cls._seed_demo_reports(instances)
        super().after_resource_load(instances, tier=tier, source=source)

    @classmethod
    def _seed_demo_reports(cls, instances: Iterable[Any]) -> None:
        """Seed one idempotent report for each side of demo placements."""

        update_model = apps.get_model("portfolio", "Update")
        for placement in sorted(instances, key=lambda instance: instance.pk or 0):
            targets = (
                (
                    placement.initiative,
                    "at_risk",
                    "Scope is clear; delivery sequencing needs attention.",
                ),
                (
                    placement.project,
                    "on_track",
                    "The current push is progressing as planned.",
                ),
            )
            for target, health, body in targets:
                if update_model._base_manager.using(placement._state.db).filter(
                    **generic_target(target).lookups(update_model, "target"),
                    body=body,
                ).exists():
                    continue
                report = update_model(
                    target=target,
                    health=health,
                    body=body,
                    created_by_id=placement.created_by_id,
                    updated_by_id=placement.updated_by_id,
                )
                report.sudo(reason="portfolio.demo.report").save(using=placement._state.db)


class UpdateQuerySet[UpdateT: Update](AngeeHistoryQuerySet[UpdateT]):
    """Report ordering and mutation admission for the target health projection."""

    def for_target(self, target: models.Model) -> UpdateQuerySet[UpdateT]:
        """Select this target's reports in deterministic projection order."""

        return self.filter(**generic_target(target).lookups(self.model, "target")).order_by(
            "-updated_at", "-created_at", "-pk",
        )

    def update(self, **kwargs: Any) -> int:
        """Require instance saves for writes that change projected health or order."""

        if {"health", "created_at", "updated_at", "content_type", "content_type_id", "object_id"} & kwargs.keys():
            raise ValidationError("Report health and target changes require an instance save.")
        return super().update(**kwargs)

    def bulk_create(self, objs: Iterable[UpdateT], *args: Any, **kwargs: Any) -> list[UpdateT]:
        """Reports require row validation and serialized target projection."""

        if rows := tuple(objs):
            raise ValidationError("Report creation requires an instance save.")
        return super().bulk_create(rows, *args, **kwargs)


_UpdateManagerBase = AngeeHistoryManager.from_queryset(UpdateQuerySet)


class UpdateManager[UpdateT: Update](_UpdateManagerBase[UpdateT]):
    """Own target validation and health-report creation.

    ``permissions.zed`` backs the update's ``project`` and ``initiative``
    relations with its ``GenericForeignKey``, so a report is created with write
    on its target; reports are stored at :func:`rebac.generic_target` and
    written under the actor.
    """

    def report(
        self,
        *,
        target: Project | Initiative,
        health: str | UpdateHealth,
        body: str = "",
    ) -> UpdateT:
        """Create one report on a Project or Initiative; its ``create`` requires write on the target."""

        if target.pk is None:
            raise ValidationError({"target": "A saved project or initiative is required."})
        self.model.validate_target(target)
        using = self._db or target._state.db or router.db_for_write(self.model, instance=target)
        self = self.db_manager(using)
        with database_context(using), transaction.atomic(using=using):
            locked_target = type(target).system_queryset(lock=("self",)).using(using).get(pk=target.pk)
            report = self.model(health=health, body=body, **generic_target(locked_target).lookups(self.model, "target"))
            report.full_clean(validate_unique=False, validate_constraints=False)
            return self.insert(report)


class Update(
    ImmutableFieldsMixin, HistoryMixin, AuditMixin, ProtectedRecordRefMixin, ProtectedDataModel, concrete=True
):
    """A required-health report on a Project or Initiative, never a Product."""

    merge_policy = MergePolicy.MOVE
    sqid_prefix = "upd_"
    immutable_fields = ("content_type_id", "object_id")

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey("content_type", "object_id")
    health = StateField(choices_enum=UpdateHealth)
    body = models.TextField(blank=True, default="")

    objects = UpdateManager()

    class Meta:
        """Django options for target-addressed health reports."""

        abstract = True
        ordering = ("-updated_at", "-created_at", "sqid")
        rebac_resource_type = "portfolio/update"
        indexes = (models.Index(fields=("content_type", "object_id", "updated_at")),)

    def clean(self) -> None:
        """Require health and a teleological target."""

        super().clean()
        if self.health in (None, ""):
            raise ValidationError({"health": "A portfolio update must assert health."})
        if self.target is None:
            raise ValidationError({"target": "A project or initiative target is required."})
        self.validate_target(self.target)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the report and the target's latest-health denorm.

        The target is stored at :func:`rebac.generic_target` and is immutable:
        the denormalized health belongs to the target the report was made on.
        """

        if kwargs.get("update_fields") is not None and not kwargs["update_fields"]:
            return
        if self.health in (None, ""):
            raise ValidationError({"health": "A portfolio update must assert health."})
        target = self.target
        if target is None:
            raise ValidationError({"target": "A project or initiative target is required."})
        self.validate_target(target)
        canonical = generic_target(target)
        self.content_type = canonical.content_type
        self.object_id = canonical.object_id
        using = kwargs.get("using") or self._state.db or router.db_for_write(type(self), instance=self)
        with database_context(using), transaction.atomic(using=using):
            # Serialize reports on the target; read committed report values after
            # partial saves so unsaved instance fields never enter the projection.
            type(target).system_queryset(lock=("self",)).using(using).get(pk=target.pk)
            super().save(*args, **kwargs)
            type(self).refresh_target_health(target, using=using)

    @classmethod
    def refresh_target_health(cls, target: Project | Initiative, *, using: str) -> None:
        """Project the latest persisted report, including after collector deletes."""

        latest = (
            cls.objects.sudo(reason="portfolio.update.project_health").using(using).for_target(target)
            .values("health", "updated_at").first()
        )
        target.health = latest["health"] if latest else None
        target.health_updated_at = latest["updated_at"] if latest else None
        type(target)._base_manager.using(using).filter(pk=target.pk).update(
            health=target.health, health_updated_at=target.health_updated_at,
        )

    def __str__(self) -> str:
        """Return a readable target-qualified health assertion."""

        return f"{self.record_model_label}:{self.record_public_id} {self.health}"


class Release(HistoryMixin, AuditMixin, ProtectedDataModel, concrete=True):
    """The named artifact a ship Project leaves on a Product timeline."""

    sqid_prefix = "rls_"

    product = models.ForeignKey(
        "portfolio.Product",
        on_delete=models.CASCADE,
        related_name="releases",
    )
    name = models.CharField(max_length=120)
    status = StateField(choices_enum=ReleaseStatus, default=ReleaseStatus.PLANNED)
    target_date = models.DateField(null=True, blank=True, db_index=True)
    shipped_on = models.DateField(null=True, blank=True, db_index=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        """Django options for Product-scoped release names."""

        abstract = True
        ordering = ("product_id", "-shipped_on", "target_date", "name", "sqid")
        rebac_resource_type = "portfolio/release"
        constraints = (
            models.UniqueConstraint(
                fields=("product", "name"),
                name="uq_portfolio_release_product_name",
            ),
        )

    def __str__(self) -> str:
        """Return the Product-qualified release name."""

        return f"{self.product_id}:{self.name}"


class ProjectPortfolio(Composable, extends="projects.Project"):
    """Same-row portfolio placement folded into ``projects.Project``.

    Placement (``product``, ``priority``, and ``sort_order``) is deliberately
    project-side.
    """


    hasura_readable_fields: ClassVar[tuple[str, ...]] = (
        "product",
        "health",
        "health_updated_at",
        "priority",
        "sort_order",
    )
    hasura_filterable_fields: ClassVar[tuple[str, ...]] = hasura_readable_fields
    hasura_sortable_fields: ClassVar[tuple[str, ...]] = hasura_readable_fields
    hasura_aggregatable_fields: ClassVar[tuple[str, ...]] = ("priority", "sort_order")
    hasura_groupable_fields: ClassVar[tuple[str, ...]] = ("product", "health", "priority")
    hasura_insertable_fields: ClassVar[tuple[str, ...]] = ("product", "priority", "sort_order")
    hasura_updatable_fields: ClassVar[tuple[str, ...]] = hasura_insertable_fields

    product = models.ForeignKey(
        "portfolio.Product",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="projects",
    )
    health = StateField(
        choices_enum=InitiativeHealth,
        null=True,
        blank=True,
        editable=False,
    )
    health_updated_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        db_index=True,
    )
    priority = models.IntegerField(default=0, db_index=True)
    sort_order = FractionalRankField()

    class Meta:
        """Abstract donor options folded into the concrete Project table."""

        abstract = True
        constraints = (
            models.UniqueConstraint(
                fields=("product", "sort_order"),
                condition=models.Q(product__isnull=False),
                name="uq_projects_project_product_rank",
            ),
        )

    def promote_to_product(self) -> Product:
        """Return the one Product matured from this Project."""

        product_model = apps.get_model("portfolio", "Product")
        return product_model.objects.from_project(self)


class TaskPortfolio(Composable, extends="projects.Task"):
    """Same-row release attribution folded into ``projects.Task``."""


    hasura_readable_fields: ClassVar[tuple[str, ...]] = ("release",)
    hasura_filterable_fields: ClassVar[tuple[str, ...]] = hasura_readable_fields
    hasura_sortable_fields: ClassVar[tuple[str, ...]] = hasura_readable_fields
    hasura_aggregatable_fields: ClassVar[tuple[str, ...]] = ()
    hasura_groupable_fields: ClassVar[tuple[str, ...]] = hasura_readable_fields
    # Projected as an object by the portfolio type extension.
    hasura_insertable_fields: ClassVar[tuple[str, ...]] = hasura_readable_fields
    hasura_updatable_fields: ClassVar[tuple[str, ...]] = hasura_readable_fields

    release = models.ForeignKey(
        "portfolio.Release",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="fixed_tasks",
    )

    class Meta:
        """Abstract donor options folded into the concrete Task table."""

        abstract = True
