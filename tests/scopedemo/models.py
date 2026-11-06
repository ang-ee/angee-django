"""Installed test models for create gates and task composition regression tests.

``ScopedDoc`` composes this test app's local ``ScopeScopedMixin``. The blank-on-
input ``scope`` FK defaults from the actor's sole direct scope membership and
the adjacent ``permissions.zed`` gates ``create`` on ``scope->member``. That arm
fail-closes unless the pre-save create gate evaluates against the defaulted
scope, so these models keep coverage on model-owned defaults before authorization
without depending on any framework-owned business scope.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

from django.core.exceptions import ValidationError
from django.db import models, transaction
from rebac import RebacMixin, app_settings, current_actor, is_anonymous_actor, to_subject_ref
from rebac.models import active_relationship_model
from rebac.resources import model_resource_type

from angee.iam.managers import ProtectedManager
from angee.iam.models.base import ProtectedDataModel
from angee.iam.models.ownership import OwnerMixin
from angee.iam.querysets import ProtectedQuerySet
from angee.projects.models import Milestone as AbstractMilestone
from angee.projects.models import Task as AbstractTask
from angee.work.models import Stage as AbstractStage
from angee.work.models import TaskWork

SCOPE_MEMBER_RELATION = "direct_member"
"""Direct membership relation on the local ``scopedemo/scope`` test resource."""


def standalone_relation(source: type[models.Model], name: str) -> models.Field:
    """Copy a test model's FK without its source's reverse accessor."""

    field = deepcopy(source._meta.get_field(name))
    field.remote_field.related_name = "+"
    return field


def _local_constraint(constraint: models.BaseConstraint) -> models.BaseConstraint:
    """Retain an inherited rule under a test-app-unique database name."""

    local = constraint.clone()
    local.name = f"{constraint.name}_scopedemo"
    return local


class ProposalTarget(OwnerMixin, ProtectedDataModel):
    """A declared decision-method owner without an update timestamp."""

    sqid_prefix = "ptg_"
    updated_at = None
    name = models.CharField(max_length=80)
    parent = models.ForeignKey("scopedemo.ProposalTarget", null=True, blank=True, on_delete=models.SET_NULL)
    locked_value = models.CharField(max_length=20, editable=False, default="fixed")
    confirmed = models.BooleanField(default=False)
    decision_methods = ("confirm", "requires_input")

    def confirm(self):
        self.confirmed = True
        self.save(update_fields=["confirmed"])

    def requires_input(self, value):
        """An invalid declaration used to prove ask-time signature validation."""

    class Meta:
        rebac_resource_type = "scopedemo/proposal_target"


class ScopeQuerySet(ProtectedQuerySet[Any]):
    """Queryset vocabulary for local create-gate scopes."""


class ScopeManager(ProtectedManager.from_queryset(ScopeQuerySet)):  # type: ignore[misc]
    """Manager for the test-local scope resource."""

    def direct_memberships_of(self, actor: Any) -> ScopeQuerySet:
        """Return scopes where ``actor`` has the direct test membership tuple."""

        subject = to_subject_ref(actor)
        id_attr = str(getattr(self.model._meta, "rebac_id_attr", None) or app_settings.REBAC_RESOURCE_ID_ATTR)
        scope_ids = list(
            active_relationship_model()
            .objects.filter(
                resource_type=model_resource_type(self.model),
                relation=SCOPE_MEMBER_RELATION,
                subject_type=subject.subject_type,
                subject_id=subject.subject_id,
                optional_subject_relation=subject.optional_relation,
            )
            .values_list("resource_id", flat=True)
        )
        scope_free = cast(
            ScopeQuerySet,
            self.get_queryset().system_context(reason="direct scopedemo membership is itself the authorization"),
        )
        return scope_free.filter(**{f"{id_attr}__in": scope_ids})


class Scope(ProtectedDataModel):
    """A test-local REBAC scope with parent reach and direct membership."""

    sqid_prefix = "scp_"

    name = models.CharField(max_length=100)
    parent = models.ForeignKey(
        "scopedemo.Scope",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )

    objects = ScopeManager()

    class Meta(ProtectedDataModel.Meta):
        """Concrete test scope used by generic create-gate and zed tests."""

        abstract = False
        app_label = "scopedemo"
        db_table = "test_scopedemo_scope"
        rebac_resource_type = "scopedemo/scope"


class ScopeScopedMixin(models.Model):
    """Scope a test model's rows to one local ``Scope``."""

    scope = models.ForeignKey(
        "scopedemo.Scope",
        on_delete=models.PROTECT,
        related_name="+",
        blank=True,
    )

    class Meta:
        """Django model options for scope-only abstract inheritance."""

        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Default an unset ``scope`` from the acting user's sole membership."""

        if self.scope_id is None:
            self.scope = self._default_scope_from_membership()
        super().save(*args, **kwargs)

    def _default_scope_from_membership(self) -> Scope:
        """Return the acting user's sole direct scope, or raise naming ``scope``."""

        actor = current_actor()
        if actor is None or is_anonymous_actor(actor):
            raise ValidationError({"scope": "Select a scope: it cannot be defaulted for an unauthenticated actor."})
        scope_model = type(self)._meta.get_field("scope").related_model
        memberships = list(scope_model._default_manager.direct_memberships_of(actor)[:2])
        if len(memberships) == 1:
            return memberships[0]
        if not memberships:
            raise ValidationError(
                {"scope": "Select a scope: you are not a direct member of any scope to default from."}
            )
        raise ValidationError(
            {"scope": "Select a scope: you are a direct member of several, so it cannot be defaulted."}
        )


class ScopedDoc(ScopeScopedMixin, ProtectedDataModel):
    """A locally scoped document whose ``create`` rides ``scope->member``."""

    sqid_prefix = "scd_"

    title = models.CharField(max_length=200, blank=True, default="")

    class Meta(ProtectedDataModel.Meta):
        """Concrete locally scoped, create-gated document for the gate tests."""

        abstract = False
        app_label = "scopedemo"
        db_table = "test_scopedemo_doc"
        rebac_resource_type = "scopedemo/doc"


class FactoryDocQuerySet(ProtectedQuerySet["FactoryDoc"]):
    """Keep a companion row in the same transaction as every document insert."""

    def insert(self, obj: FactoryDoc) -> FactoryDoc:
        """Persist the prepared document and its companion exactly once."""

        with transaction.atomic():
            obj = super().insert(obj)
            FactoryCompanion.objects.create(document=obj)
        return obj


class FactoryDoc(ScopeScopedMixin, ProtectedDataModel):
    """A scoped document whose factory has a transactional companion invariant."""

    sqid_prefix = "fcd_"
    title = models.CharField(max_length=200)
    objects = ProtectedManager.from_queryset(FactoryDocQuerySet)()

    class Meta(ProtectedDataModel.Meta):
        abstract = False
        app_label = "scopedemo"
        rebac_resource_type = "scopedemo/factory_doc"


class FactoryCompanion(models.Model):
    """One row per factory invocation, so duplicate calls are observable."""

    document = models.ForeignKey(FactoryDoc, on_delete=models.CASCADE, related_name="companions")

    class Meta:
        app_label = "scopedemo"


class ProjectionDoc(ProtectedDataModel):
    """A create policy depending on a forward-then-reverse candidate relation."""

    sqid_prefix = "pjd_"
    title = models.CharField(max_length=200)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="children")

    class Meta(ProtectedDataModel.Meta):
        abstract = False
        app_label = "scopedemo"
        rebac_resource_type = "scopedemo/projection_doc"


class SharedDoc(ProtectedDataModel):
    """A candidate whose create permission reads its own sharing column."""

    sqid_prefix = "shd_"
    title = models.CharField(max_length=200)
    is_shared = models.BooleanField(default=False)

    class Meta(ProtectedDataModel.Meta):
        abstract = False
        app_label = "scopedemo"
        rebac_resource_type = "scopedemo/shared_doc"


class NativeAttachmentTarget(RebacMixin):
    """A native library row for generic attachment-lock coverage."""

    class Meta:
        rebac_resource_type = "scopedemo/native_attachment_target"


class ProjectAccessTask(AbstractTask):
    """Production task lifecycle with a small, independently protected project."""

    sqid_prefix = "pat_"
    owner_container = None
    assignee = standalone_relation(AbstractTask, "assignee")
    delegate = standalone_relation(AbstractTask, "delegate")
    activity_type = standalone_relation(AbstractTask, "activity_type")
    project = models.ForeignKey(Scope, null=True, blank=True, on_delete=models.SET_NULL)
    milestone = None
    milestone_id = None
    links = None
    file_attachments = None
    thread_attachments = None
    thread_create_autofollow_author = False

    class Meta(AbstractTask.Meta):
        abstract = False
        app_label = "scopedemo"
        rebac_resource_type = "scopedemo/project_access_task"


class TaskPromotion(models.Model):
    """Minimal relation target for the production task's phase expression."""

    converted_from = models.ForeignKey(ProjectAccessTask, on_delete=models.CASCADE, related_name="promoted_projects")
    current_milestone = models.ForeignKey(Scope, null=True, on_delete=models.SET_NULL)

    class Meta:
        app_label = "scopedemo"


class FreshnessStage(AbstractStage):
    queue = models.ForeignKey(Scope, on_delete=models.CASCADE)

    class Meta(AbstractStage.Meta):
        abstract = False
        app_label = "scopedemo"
        rebac_resource_type = "scopedemo/freshness_stage"


class FreshnessMilestone(AbstractMilestone):
    project = models.ForeignKey(Scope, on_delete=models.CASCADE)

    class Meta(AbstractMilestone.Meta):
        abstract = False
        app_label = "scopedemo"
        rebac_resource_type = "scopedemo/freshness_milestone"
        constraints = tuple(_local_constraint(constraint) for constraint in AbstractMilestone.Meta.constraints)


class FreshnessTask(TaskWork, AbstractTask):
    """Task with persisted stage and milestone relations for freshness checks."""

    # This scope intentionally supplies no item-ownership policy.
    owner_container = None
    assignee = standalone_relation(AbstractTask, "assignee")
    delegate = standalone_relation(AbstractTask, "delegate")
    activity_type = standalone_relation(AbstractTask, "activity_type")
    project = models.ForeignKey(Scope, null=True, blank=True, on_delete=models.SET_NULL)
    milestone = models.ForeignKey(FreshnessMilestone, null=True, blank=True, on_delete=models.SET_NULL)
    queue = models.ForeignKey(Scope, null=True, blank=True, on_delete=models.SET_NULL)
    stage = models.ForeignKey(FreshnessStage, null=True, blank=True, on_delete=models.SET_NULL)
    cycle = None
    cycle_id = None
    links = None
    file_attachments = None
    thread_attachments = None
    thread_create_autofollow_author = False

    class Meta(AbstractTask.Meta):
        abstract = False
        app_label = "scopedemo"
        rebac_resource_type = "scopedemo/freshness_task"
