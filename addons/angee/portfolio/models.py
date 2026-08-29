"""Products, initiatives, reports, releases, and same-row planning donors.

Products are phasal subjects: their lifecycle carries no target date, health,
or progress. Projects and initiatives are teleological and therefore own health
reports. The project/task donors fold optional portfolio placement onto the
upstream rows only when this addon is composed.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from angee.base.fields import FractionalRankField, StateField
from angee.base.mixins import AuditMixin, HierarchyMixin
from angee.base.models import (
    AngeeDataModel,
    AngeeManager,
    AngeeModel,
    role_anchor,
)
from angee.base.refs import RecordRefMixin, canonical_record_target
from angee.base.scoping import bind_actor
from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    SubjectRef,
    current_actor,
    system_context,
    to_object_ref,
    write_relationships,
)

_EVERYONE = SubjectRef.of("auth/user", "*")
"""The wildcard subject used by the workspace-visible portfolio posture."""


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


class InitiativeHealth(models.TextChoices):
    """Latest reported health denormalized onto an Initiative."""

    ON_TRACK = "on_track", "On track"
    AT_RISK = "at_risk", "At risk"
    OFF_TRACK = "off_track", "Off track"


class InitiativeDateResolution(models.TextChoices):
    """Precision carried by an Initiative target date."""

    DAY = "day", "Day"
    MONTH = "month", "Month"
    QUARTER = "quarter", "Quarter"
    HALF_YEAR = "half_year", "Half year"
    YEAR = "year", "Year"


class UpdateHealth(models.TextChoices):
    """Health assertion required from every portfolio Update."""

    ON_TRACK = "on_track", "On track"
    AT_RISK = "at_risk", "At risk"
    OFF_TRACK = "off_track", "Off track"


class ReleaseStatus(models.TextChoices):
    """Lifecycle of a Product release artifact."""

    PLANNED = "planned", "Planned"
    IN_PROGRESS = "in_progress", "In progress"
    SHIPPED = "shipped", "Shipped"
    DROPPED = "dropped", "Dropped"


class WorkspaceVisibleMixin(models.Model):
    """Persist the R11 wildcard-reader tuple for a portfolio row.

    Workspace visibility is posture data, so every one of the addon's five
    persisted resource definitions carries an explicit ``reader@auth/user:*``
    tuple. Deployments may replace that tuple strategy without changing schema.
    """

    class Meta:
        """Django options for the tuple-reconciliation mixin."""

        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the row and idempotently reconcile its wildcard reader."""

        with transaction.atomic():
            super().save(*args, **kwargs)
            write_relationships(
                [
                    RelationshipTuple(
                        resource=to_object_ref(self),
                        relation="reader",
                        subject=_EVERYONE,
                    )
                ]
            )


class ProductManager(AngeeManager):
    """Own the idempotent Project-to-Product maturation write."""

    def from_project(self, project: models.Model) -> models.Model:
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
                bind_actor(existing, actor)
                return existing

            verified_actor = self.check_create()
            product = self.model(
                name=locked_project.title,
                body=locked_project.body,
                owner_id=locked_project.lead_id,
                originated_from_id=locked_project.pk,
            )
            product.full_clean(validate_unique=False, validate_constraints=False)
            product.sudo(reason="portfolio.product.promote_from_project")
            try:
                with transaction.atomic():
                    product.save()
            except IntegrityError:
                with system_context(reason="portfolio.product.promote_from_project.concurrent_lookup"):
                    product = self.get(originated_from_id=project.pk)
            bind_actor(product, verified_actor)
            return product


class Product(WorkspaceVisibleMixin, AuditMixin, AngeeDataModel):
    """A perpetual subject passing through phases rather than toward fulfillment."""

    runtime = True
    sqid_prefix = "pdt_"

    name = models.CharField(max_length=240)
    body = models.TextField(blank=True, default="")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="owned_portfolio_products",
    )
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
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("originated_from",),
                condition=models.Q(originated_from__isnull=False),
                name="uq_portfolio_product_originated_from",
            ),
        )

    def __str__(self) -> str:
        """Return the Product name."""

        return self.name


class Initiative(WorkspaceVisibleMixin, HierarchyMixin, AuditMixin, AngeeDataModel):
    """A tree-shaped long-horizon intent whose pushes can be fulfilled."""

    runtime = True
    sqid_prefix = "ini_"

    name = models.CharField(max_length=240)
    body = models.TextField(blank=True, default="")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="owned_portfolio_initiatives",
    )
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
        rebac_id_attr = "sqid"
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


class InitiativeProject(WorkspaceVisibleMixin, AuditMixin, AngeeDataModel):
    """An ordered Project placement on an ancestry-safe Initiative tree."""

    runtime = True
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
        rebac_id_attr = "sqid"
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
            initiative_model = self._meta.get_field("initiative").related_model
            initiative = initiative_model.objects.filter(pk=self.initiative_id).first()
            if initiative is None:
                return
            placements = type(self).objects.filter(project_id=self.project_id).exclude(pk=self.pk)
            if lock:
                project_model = self._meta.get_field("project").related_model
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
        publish: bool = False,
    ) -> None:
        """Seed one idempotent report for each side of demo placements."""

        del cls, source, publish
        if tier != "demo":
            return
        update_model = apps.get_model("portfolio", "Update")
        for placement in sorted(instances, key=lambda instance: instance.pk or 0):
            targets = (
                (placement.initiative, "at_risk", "Scope is clear; delivery sequencing needs attention."),
                (placement.project, "on_track", "The current push is progressing as planned."),
            )
            for target, health, body in targets:
                canonical = canonical_record_target(target)
                if update_model._base_manager.filter(
                    content_type=canonical.content_type,
                    object_id=canonical.object_id,
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
                report.sudo(reason="portfolio.demo.report").save()


class UpdateManager(AngeeManager):
    """Own target validation, authorization, and health-report creation."""

    TARGET_RELATIONS = {
        "projects.project": "project",
        "portfolio.initiative": "initiative",
    }

    @classmethod
    def target_relation(cls, target: models.Model) -> str:
        """Return the Update relation for one allowed teleological target."""

        try:
            return cls.TARGET_RELATIONS[target._meta.label_lower]
        except KeyError as error:
            raise ValidationError({"target": "Portfolio updates may target only projects or initiatives."}) from error

    @classmethod
    def target_model(cls, model_label: str) -> type[models.Model]:
        """Return the installed model for one allowed target label."""

        normalized = str(model_label).strip().lower()
        if normalized not in cls.TARGET_RELATIONS:
            raise ValidationError({"target": "Portfolio updates may target only projects or initiatives."})
        return apps.get_model(normalized)

    def report(
        self,
        *,
        target: models.Model,
        health: str | UpdateHealth,
        body: str = "",
    ) -> models.Model:
        """Create one report on a writable Project or Initiative."""

        if target.pk is None:
            raise ValidationError({"target": "A saved project or initiative is required."})
        relation = self.target_relation(target)
        if not target.has_access("write"):
            raise PermissionDenied("Write access to the update target is required.")
        actor = current_actor()
        with transaction.atomic():
            with system_context(reason="portfolio.update.report.target"):
                locked_target = type(target).objects.lock_if_supported().get(pk=target.pk)
            verified_actor = self.check_create({relation: (locked_target,)})
            report = self.model(target=locked_target, health=health, body=body)
            report.full_clean(validate_unique=False, validate_constraints=False)
            report.sudo(reason="portfolio.update.report").save()
            bind_actor(report, verified_actor or actor)
            return report


class Update(WorkspaceVisibleMixin, AuditMixin, RecordRefMixin, AngeeDataModel):
    """A required-health report on a Project or Initiative, never a Product."""

    runtime = True
    sqid_prefix = "upd_"

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
        rebac_id_attr = "sqid"
        indexes = (models.Index(fields=("content_type", "object_id", "updated_at")),)

    def clean(self) -> None:
        """Require health and a teleological target."""

        super().clean()
        if self.health in (None, ""):
            raise ValidationError({"health": "A portfolio update must assert health."})
        if self.target is None:
            raise ValidationError({"target": "A project or initiative target is required."})
        UpdateManager.target_relation(self.target)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the report, its target relation, and the latest-health denorm."""

        if self.health in (None, ""):
            raise ValidationError({"health": "A portfolio update must assert health."})
        if self.target is None:
            raise ValidationError({"target": "A project or initiative target is required."})
        relation = UpdateManager.target_relation(self.target)
        if self.pk is not None and not self._state.adding:
            with system_context(reason="portfolio.update.immutable_target"):
                persisted = type(self)._base_manager.filter(pk=self.pk).values("content_type_id", "object_id").first()
            if persisted is not None and (
                persisted["content_type_id"] != self.content_type_id or persisted["object_id"] != self.object_id
            ):
                raise ValidationError({"target": "An update's target is immutable."})
        with transaction.atomic():
            super().save(*args, **kwargs)
            write_relationships(
                [
                    RelationshipTuple(
                        resource=to_object_ref(self),
                        relation=relation,
                        subject=SubjectRef(to_object_ref(self.target)),
                    )
                ]
            )
            # Django QuerySet.update() bypasses save-path re-denormalization by nature;
            # the API routes through instance saves, and internal bulk writers are on their honor.
            type(self.target)._base_manager.filter(pk=self.target.pk).update(
                health=self.health,
                health_updated_at=self.updated_at,
            )
        self.target.health = self.health
        self.target.health_updated_at = self.updated_at

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Delete the report and restore its target's surviving latest health."""

        target = self.target
        content_type_id = self.content_type_id
        object_id = self.object_id
        with transaction.atomic():
            if target is not None:
                with system_context(reason="portfolio.update.delete.target"):
                    target = type(target).objects.lock_if_supported().get(pk=target.pk)
            result = super().delete(*args, **kwargs)
            if target is not None:
                with system_context(reason="portfolio.update.delete.latest"):
                    latest = next(
                        iter(
                            type(self)
                            ._base_manager.filter(content_type_id=content_type_id, object_id=object_id)
                            .order_by("-updated_at", "-created_at")
                            .values("health", "updated_at")[:1]
                        ),
                        None,
                    )
                health = latest["health"] if latest is not None else None
                health_updated_at = latest["updated_at"] if latest is not None else None
                type(target)._base_manager.filter(pk=target.pk).update(
                    health=health,
                    health_updated_at=health_updated_at,
                )
                target.health = health
                target.health_updated_at = health_updated_at
        return result

    def __str__(self) -> str:
        """Return a readable target-qualified health assertion."""

        return f"{self.record_model_label}:{self.record_public_id} {self.health}"


class Release(WorkspaceVisibleMixin, AuditMixin, AngeeDataModel):
    """The named artifact a ship Project leaves on a Product timeline."""

    runtime = True
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
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("product", "name"),
                name="uq_portfolio_release_product_name",
            ),
        )

    def __str__(self) -> str:
        """Return the Product-qualified release name."""

        return f"{self.product_id}:{self.name}"


class ProjectPortfolio(AngeeModel):
    """Same-row portfolio placement folded into ``projects.Project``.

    Placement (``product``, ``priority``, and ``sort_order``) is deliberately
    project-side.
    """

    extends = "projects.Project"
    runtime = False

    hasura_readable_fields = (
        "product",
        "health",
        "health_updated_at",
        "priority",
        "sort_order",
    )
    hasura_filterable_fields = hasura_readable_fields
    hasura_sortable_fields = hasura_readable_fields
    hasura_aggregatable_fields = ("priority", "sort_order")
    hasura_groupable_fields = ("product", "health", "priority")
    # Projected as an object by the portfolio type extension (see work's
    # hasura_object_relation_fields precedent).
    hasura_object_relation_fields = ("product",)
    hasura_insertable_fields = ("product", "priority", "sort_order")
    hasura_updatable_fields = hasura_insertable_fields

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
    sort_order = FractionalRankField(default=FractionalRankField.STEP)

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

    def promote_to_product(self) -> models.Model:
        """Return the one Product matured from this Project."""

        product_model = apps.get_model("portfolio", "Product")
        return product_model.objects.from_project(self)


class TaskPortfolio(AngeeModel):
    """Same-row release attribution folded into ``projects.Task``."""

    extends = "projects.Task"
    runtime = False

    hasura_readable_fields = ("release",)
    hasura_filterable_fields = hasura_readable_fields
    hasura_sortable_fields = hasura_readable_fields
    hasura_aggregatable_fields: tuple[str, ...] = ()
    hasura_groupable_fields = hasura_readable_fields
    # Projected as an object by the portfolio type extension.
    hasura_object_relation_fields = ("release",)
    hasura_insertable_fields = hasura_readable_fields
    hasura_updatable_fields = hasura_readable_fields

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


PortfolioRole = role_anchor("portfolio/role")
"""Table-less REBAC anchor for the const-backed portfolio-admin namespace."""
