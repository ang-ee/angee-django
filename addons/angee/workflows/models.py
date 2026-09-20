"""Source models for workflow definitions.

The workflows addon owns graph definitions as data: a draft workflow lineage
head carries editable steps, edges, and triggers, while ``publish()`` copies that
draft into an immutable version. Step behavior remains in registry-selected
``StepImpl`` classes, so row data names keys and config, not Python callables.
Future runtime subject/artifact references use Django contenttypes-backed object
references; public ids stay at the transport boundary.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Self, cast

from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core import checks
from django.core.exceptions import (
    EmptyResultSet,
    FieldError,
    FullResultSet,
    ObjectDoesNotExist,
    ValidationError,
)
from django.db import DEFAULT_DB_ALIAS, OperationalError, ProgrammingError, connections, models, router, transaction
from django.db.models.functions import Coalesce
from django.utils import timezone
from rebac import resolve_subjects, system_context

from angee.base.fields import StateField
from angee.base.identity import canonical_subject_ref
from angee.base.impl import ImplClassField, ImplDefaultsMixin, resolve_all_impl_classes
from angee.base.mixins import AuditMixin
from angee.base.models import AngeeDataModel
from angee.base.refs import RecordRefMixin
from angee.base.scoping import system_queryset
from angee.base.transitions import StateTransitions, TransitionNotAllowed, save_state, transition
from angee.graphql.events import ChangeRelatedRecord
from angee.graphql.schema import GraphQLSchemas
from angee.resources.mixins import ResourceLoadMixin, ResourceWritePreparation
from angee.workflows.attempts import (
    AttemptCause,
    AttemptResultKind,
    AttemptStatus,
    FixtureRole,
    JsonPresence,
    LeaseRevocationReason,
    WorkflowScope,
    json_values_equal,
    validate_json_presence,
)
from angee.workflows.dispatch import (
    WorkflowDispatchEnvelope,
    WorkflowDispatchKind,
)
from angee.workflows.graph import GraphIdentity, WorkflowGraph
from angee.workflows.manager_authority import (
    _artifact_write_capability,
    _attempt_save_capability,
    _attempt_write_active,
    _decision_resolution_session,
    _decision_save_capability,
    _decision_write_active,
    _definition_write_session,
    _dispatch_save_capability,
    _recovery_evidence_write_row,
    _step_run_save_capability,
    _test_fixture_write_run,
)
from angee.workflows.managers import (
    DecisionManager,
    DefinitionQuerySet,
    EdgeManager,
    StepArtifactManager,
    StepAttemptManager,
    StepAttemptSystemManager,
    StepExternalSubscriptionManager,
    StepManager,
    StepRunManager,
    TriggerManager,
    WorkflowDispatchManager,
    WorkflowManager,
    WorkflowRecoveryEvidenceManager,
    WorkflowRunManager,
    WorkflowRunSystemManager,
    WorkflowTestFixtureManager,
    _combined_delete_results,
    _definition_rows,
)
from angee.workflows.states import (
    DecisionGate,
    JoinRule,
    ParentRelation,
    RunOrigin,
    RunStatus,
    StepRunStatus,
    TriggerKind,
    Verdict,
    WaitingKind,
    WorkflowPurpose,
    WorkflowStatus,
)
from angee.workflows.steps import (
    StepExecutionMode,
    StepImpl,
)
from angee.workflows.trigger_declarations import (
    EventSource,
    EventTriggerConfig,
    ScheduleTriggerConfig,
    TriggerConfig,
    validate_trigger_config,
)

logger = logging.getLogger(__name__)
_CHANGE_FEED_FIX = "declare changes() for the model to join the change feed"


def _empty_workflow_output_schema() -> dict[str, Any]:
    """Return the declared empty output used by workflows without result rules."""

    return {"type": "object", "properties": {}, "additionalProperties": False}


_UNSET_RESULT_OUTPUT = object()


@contextmanager
def _workflow_child_create(instance: Any) -> Iterator[None]:
    """Authorize one unsaved child through its proposed workflow relation."""

    if not instance._state.adding:
        yield
        return
    actor, bypass = instance.effective_actor(strict=True)
    if bypass:
        yield
        return
    assert actor is not None
    with DefinitionQuerySet.caller_context(instance):
        verified_actor = type(instance)._default_manager.check_create(
            {"workflow": (instance.workflow,)}
        )
    instance.with_actor(verified_actor).sudo(reason="workflows.child.create")
    try:
        yield
    finally:
        instance.with_actor(verified_actor)


@dataclass(frozen=True, slots=True)
class StepConfigProjection:
    """Canonical authoring value and diagnostics for one persisted step config."""

    value: Any
    errors: dict[str, list[str]]


def _save_workflow_status(instance: models.Model, source: Any, target: Any) -> None:
    """Persist a workflow status transition through the immutable-row variant."""

    workflow = cast("Workflow", instance)
    workflow._allow_immutable_status_save = True
    try:
        save_state(workflow, source, target)
    finally:
        del workflow._allow_immutable_status_save


def _save_run_terminal(instance: models.Model, source: Any, target: Any) -> None:
    """Commit a terminal run result and its child-completion intent together."""

    run = cast("WorkflowRun", instance)
    alias = run._state.db or router.db_for_write(type(run), instance=run)
    with transaction.atomic(using=alias), system_context(reason="workflows.runs.terminal"):
        save_state(run, source, target)
        delivery_target = run.delivery_target()
        if delivery_target.parent_relation in {ParentRelation.OWNED_CALL, ParentRelation.CONTINUATION}:
            dispatch_model = apps.get_model("workflows", "WorkflowDispatch")
            dispatch_model.objects.db_manager(alias).schedule_artifact_delivery(delivery_target)


#: Statuses that participate in version currency: a newer ARCHIVED row
#: supersedes older PUBLISHED rows, retiring the whole lineage.
_CURRENCY_STATUSES = (WorkflowStatus.PUBLISHED, WorkflowStatus.ARCHIVED)


class Workflow(ResourceLoadMixin, AuditMixin, AngeeDataModel):
    """Editable workflow lineage head or immutable published workflow version.

    A resource-assigned stable key identifies the lineage independently of its
    mutable display name and is shared by every published version.
    """

    runtime = True
    rebac_grantable = {"editor": "write", "viewer": "write"}

    sqid_prefix = "wfl_"
    key = models.SlugField(max_length=100, blank=True, default="")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    purpose = StateField(choices_enum=WorkflowPurpose, default=WorkflowPurpose.AUTOMATION)
    subject_declaration = models.CharField(max_length=200, blank=True, default="")
    status = StateField(choices_enum=WorkflowStatus, default=WorkflowStatus.DRAFT)
    version = models.PositiveIntegerField(default=0)
    draft_revision = models.PositiveIntegerField(default=0, editable=False)
    published_from = models.ForeignKey(
        "workflows.Workflow",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="published_versions",
    )
    error_workflow = models.ForeignKey(
        "workflows.Workflow",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="error_for_workflows",
    )
    max_steps = models.PositiveIntegerField(default=1000)
    budget = models.JSONField(default=dict, blank=True)
    input_schema = models.JSONField(default=dict, blank=True)
    output_schema = models.JSONField(default=_empty_workflow_output_schema, blank=True)
    result_rules = models.JSONField(default=list, blank=True)

    status_transitions = StateTransitions(
        status,
        {
            WorkflowStatus.DRAFT: [WorkflowStatus.TEST, WorkflowStatus.PUBLISHED],
            WorkflowStatus.PUBLISHED: [WorkflowStatus.ARCHIVED],
        },
    )

    objects = WorkflowManager()

    class Meta:
        """Django model options for workflow definitions."""

        abstract = True
        ordering = ("name", "version")
        rebac_resource_type = "workflows/workflow"
        constraints = (
            models.UniqueConstraint(
                fields=("key",),
                condition=models.Q(published_from__isnull=True) & ~models.Q(key=""),
                name="uniq_workflows_workflow_head_key",
                violation_error_code="unique",
                violation_error_message="A workflow with this key already exists.",
            ),
            models.UniqueConstraint(
                fields=("published_from", "draft_revision"),
                condition=models.Q(status=WorkflowStatus.TEST),
                name="uniq_workflows_test_snapshot_revision",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status=WorkflowStatus.TEST)
                    | (models.Q(published_from__isnull=False) & models.Q(version=0))
                ),
                name="chk_workflows_test_snapshot_shape",
            ),
        )

    def __str__(self) -> str:
        """Return the workflow's display label."""

        return self.name

    def validate_record_access_target(self) -> None:
        """Keep direct grants on the mutable owner of a workflow lineage."""

        if self.published_from_id is not None:
            raise ValidationError("Direct record access can only be managed on a workflow lineage head.")

    @classmethod
    def lineage_projection_annotation(cls) -> dict[str, Any]:
        """Return the ORM projection for lineage and current publication context."""

        lineage = models.Q(pk=models.OuterRef("_workflow_lineage_id")) | models.Q(
            published_from_id=models.OuterRef("_workflow_lineage_id")
        )
        current = cls.objects.current_published().filter(lineage)
        latest = cls.objects.filter(lineage, status__in=_CURRENCY_STATUSES).order_by("-version", "-pk")
        return {
            "_workflow_lineage_id": models.functions.Coalesce("published_from_id", "pk"),
            "_workflow_publication_status": models.functions.Coalesce(
                models.Subquery(latest.values("status")[:1]),
                models.Value(WorkflowStatus.DRAFT),
            ),
            "_workflow_current_published_pk": models.Subquery(current.values("pk")[:1]),
            "_workflow_current_published_version": models.Subquery(current.values("version")[:1]),
            "_workflow_current_published_subject_declaration": models.Subquery(
                current.values("subject_declaration")[:1]
            ),
        }

    @classmethod
    def resource_import_owner(cls) -> type[Any]:
        """Keep checked graph declarations under the Workflow definition owner."""

        return cls

    @classmethod
    def import_resource_groups(
        cls, groups: tuple[tuple[Any, Any], ...], *, ledger_model: type[Any], addon_aliases: Mapping[str, str]
    ) -> Any:
        """Install native Workflow/Step/Edge groups through checked definition commands."""

        # Import after model loading: resource_install imports Workflow while
        # this method is a safe runtime hook invoked only after app population.
        from angee.workflows.resource_install import import_resource_groups

        return import_resource_groups(cls, groups, ledger_model=ledger_model, addon_aliases=addon_aliases)

    @classmethod
    def resource_write_preparation(cls, resource: Any, dataset: Any) -> ResourceWritePreparation | None:
        """Declare existing lineage heads before a resource batch starts writing."""

        targets = frozenset(
            instance.pk
            for xref in dataset["_xref"]
            if (instance := resource.instance_for_xref(xref)) is not None
        )
        return ResourceWritePreparation(cls, targets) if targets else None

    @classmethod
    @contextmanager
    def prepare_resource_writes(cls, workflow_ids: Iterable[int]) -> Iterator[None]:
        """Prelock every declared resource lineage in deterministic order."""

        ids = sorted(set(workflow_ids))
        alias = router.db_for_write(cls)
        with transaction.atomic(using=alias):
            rows = list(
                system_queryset(cls, using=alias, lock=("self",))
                .filter(pk__in=ids)
                .order_by("pk")
            )
            if len(rows) != len(ids):
                raise ValidationError("A workflow definition parent no longer exists.")
            if any(row.is_immutable for row in rows):
                raise ValidationError("Published workflow versions are immutable.")
            yield

    @classmethod
    def after_resource_load(
        cls,
        instances: Iterable[Any],
        *,
        tier: str,
        source: str,
        publish: bool = False,
    ) -> None:
        """Reconcile stable keys after the checked resource definition install."""

        for workflow in sorted(instances, key=lambda instance: instance.pk or 0):
            if workflow.published_from_id is not None:
                continue
            workflow._propagate_resource_key_backfill()
        super().after_resource_load(instances, tier=tier, source=source, publish=publish)

    @transition(status, source=WorkflowStatus.DRAFT, target=WorkflowStatus.PUBLISHED, on_success=_save_workflow_status)
    def mark_published(self) -> None:
        """Mark this copied version as published."""

        session = _definition_write_session.get()
        if (
            self.published_from_id is None
            or session is None
            or self.pk not in session.copy_target_ids
        ):
            raise ValidationError("Only a new snapshot created by publish() can be marked published.")

    @transition(status, source=WorkflowStatus.DRAFT, target=WorkflowStatus.TEST, on_success=_save_workflow_status)
    def mark_test(self) -> None:
        """Mark a copied saved revision as an immutable test snapshot."""

        session = _definition_write_session.get()
        if self.published_from_id is None or session is None or self.pk not in session.copy_target_ids:
            raise ValidationError("Only a copied saved revision can be marked as a test snapshot.")

    @transition(
        status,
        source=WorkflowStatus.PUBLISHED,
        target=WorkflowStatus.ARCHIVED,
        on_success=_save_workflow_status,
    )
    def archive(self) -> None:
        """Archive a published workflow version."""

    def clean(self) -> None:
        """Validate lineage-owned links and normalize stable and subject keys."""

        super().clean()
        self.key = (self.key or "").lower()
        if self.published_from_id is not None and self.key != self.published_from.key:
            raise ValidationError({"key": "Published workflow versions must share their lineage stable key."})
        if self.error_workflow_id is not None and self.error_workflow.published_from_id is not None:
            raise ValidationError({"error_workflow": "Error workflow must point to a workflow lineage head."})
        # Mirror Trigger.event_model_label: store the canonical label_lower form
        # and reject labels that resolve to no installed model.
        self.subject_declaration = self.subject_declaration.strip().lower()
        if self.subject_declaration:
            try:
                apps.get_model(self.subject_declaration)
            except (LookupError, ValueError) as error:
                raise ValidationError(
                    {
                        "subject_declaration": (
                            f"Subject declaration {self.subject_declaration!r} is not an installed model label."
                        )
                    }
                ) from error

    def validate_subject_declaration(self, subject: Any) -> None:
        """Raise when ``subject`` does not satisfy this workflow's subject declaration."""

        if not self.subject_declaration:
            return
        if subject is None:
            raise ValidationError(
                {"subject": f"Subject declaration {self.subject_declaration!r} requires a subject."}
            )
        if subject._meta.label_lower != self.subject_declaration:
            raise ValidationError(
                {
                    "subject": (
                        f"Subject model {subject._meta.label!r} does not satisfy "
                        f"subject declaration {self.subject_declaration!r}."
                    )
                }
            )

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the workflow after enforcing immutability and model validation."""

        alias = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        if self._state.adding:
            if self.published_from_id is not None:
                session = _definition_write_session.get()
                if session is None or self.published_from_id not in session.workflow_ids:
                    raise ValidationError("Published workflow versions can only be created by publish().")
                if self.status != WorkflowStatus.DRAFT:
                    raise ValidationError({"status": "New publications must begin as draft snapshots."})
            elif self.status != WorkflowStatus.DRAFT or self.version != 0 or self.draft_revision != 0:
                raise ValidationError("New workflow lineage heads must begin as revision-zero drafts.")
            with DefinitionQuerySet.caller_context(self):
                self.full_clean()
            super().save(*args, **kwargs)
            return

        manager = type(self).objects.db_manager(alias)
        with manager._definition_write(
            (self.pk,),
            using=alias,
            _allow_status_transition=getattr(self, "_allow_immutable_status_save", False),
        ):
            persisted = cast(Self, _definition_rows(type(self), alias).get(pk=self.pk))
            self._raise_if_immutable_save(persisted)
            if self.published_from_id != persisted.published_from_id:
                raise ValidationError({"published_from": "Workflow lineage ownership is immutable."})
            if self.version != persisted.version:
                raise ValidationError({"version": "Workflow publication versions are immutable."})
            if self.status != persisted.status and not getattr(self, "_allow_immutable_status_save", False):
                raise ValidationError({"status": "Workflow status changes require a declared transition."})
            # The database counter is the sole owner; a stale model can never write it backwards.
            self.draft_revision = persisted.draft_revision
            with DefinitionQuerySet.caller_context(self):
                self.full_clean()
            self._raise_if_key_changed(persisted)
            update_fields = kwargs.get("update_fields")
            definition_fields = {
                "name",
                "description",
                "purpose",
                "subject_declaration",
                "error_workflow",
                "error_workflow_id",
                "max_steps",
                "budget",
                "input_schema",
                "output_schema",
                "result_rules",
            }
            updated = None if update_fields is None else set(update_fields)
            considered = definition_fields if updated is None else {
                field for field in definition_fields if field in updated or field.removesuffix("_id") in updated
            }
            changed = any(getattr(persisted, field) != getattr(self, field) for field in considered)
            assigns_stable_key = (
                not persisted.key
                and bool(self.key)
                and (update_fields is None or "key" in update_fields)
            )
            super().save(*args, **kwargs)
            if assigns_stable_key:
                self._propagate_resource_key_backfill()
            if changed and self.published_from_id is None:
                manager.mark_definition_changed(self.pk)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Delete only mutable workflow rows."""

        alias = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        manager = type(self).objects.db_manager(alias)
        with manager._definition_write((self.pk,), using=alias):
            persisted = cast(Self, _definition_rows(type(self), alias).get(pk=self.pk))
            self._raise_if_immutable_save(persisted)
            if persisted.published_versions.exists():
                raise ValidationError("A workflow with publication history cannot be deleted.")
            # Collector skips child instance delete methods. Own the full cascade here.
            edges = persisted.edges.using(alias).all().bound_to(self).all().delete()
            steps = persisted.steps.using(alias).all().bound_to(self).all().delete()
            with DefinitionQuerySet.caller_context(self):
                workflow = super().delete(*args, **kwargs)
            return _combined_delete_results(edges, steps, workflow)

    def publish(self) -> Self:
        """Copy this draft lineage head into an immutable published version."""

        if self.published_from_id is not None:
            raise ValidationError({"published_from": "Only a workflow lineage head can be published."})
        alias = router.db_for_write(type(self), instance=self)
        manager = type(self).objects.db_manager(alias)
        with manager._definition_write((self.pk,), using=alias):
            draft = cast(
                Self,
                DefinitionQuerySet.bind_instance(_definition_rows(type(self), alias).get(pk=self.pk), self),
            )
            if draft.status != WorkflowStatus.DRAFT:
                raise ValidationError({"status": "Only draft workflows can be published."})
            with DefinitionQuerySet.caller_context(draft):
                draft._validate_publishable()
            with DefinitionQuerySet.caller_context(draft):
                version = draft._next_published_version()
            published = draft._new_definition_copy(
                version=version,
                draft_revision=manager._definition_revision(draft.pk, draft.draft_revision),
            )
            DefinitionQuerySet.bind_instance(published, draft)
            published.save(using=alias)
            with manager._copy_to(published.pk):
                with DefinitionQuerySet.caller_context(draft):
                    draft._copy_definition_to(published)
                published.mark_published()
            return cast(Self, published)

    def _new_definition_copy(self, *, version: int, draft_revision: int) -> Self:
        """Build an unsaved immutable-definition copy with lineage-owned fields."""

        return type(self)(
            key=self.key,
            name=self.name,
            description=self.description,
            purpose=self.purpose,
            subject_declaration=self.subject_declaration,
            status=WorkflowStatus.DRAFT,
            version=version,
            draft_revision=draft_revision,
            published_from=self,
            error_workflow=self.error_workflow,
            max_steps=self.max_steps,
            budget=copy.deepcopy(self.budget),
            input_schema=copy.deepcopy(self.input_schema),
            output_schema=copy.deepcopy(self.output_schema),
            result_rules=copy.deepcopy(self.result_rules),
            created_by_id=self.created_by_id,
            updated_by_id=self.updated_by_id,
        )

    def publish_if_changed(self) -> Self | None:
        """Publish this draft only when no current version has the same definition."""

        alias = router.db_for_write(type(self), instance=self)
        manager = type(self).objects.db_manager(alias)
        with manager._definition_write((self.pk,), using=alias):
            draft = cast(
                Self,
                DefinitionQuerySet.bind_instance(_definition_rows(type(self), alias).get(pk=self.pk), self),
            )
            with DefinitionQuerySet.caller_context(draft):
                current = manager.current_published_for(draft)
                if current is not None and draft._definition_signature() == current._definition_signature():
                    return None
            return draft.publish()

    def _next_published_version(self) -> int:
        """Return the next immutable version number for this lineage head."""

        current = (
            type(self).objects.filter(published_from=self).aggregate(max_version=models.Max("version"))["max_version"]
            or 0
        )
        return int(current) + 1

    def _copy_definition_to(self, published: Workflow) -> None:
        """Copy this draft's steps and edges to ``published``."""

        step_model = self.steps.model
        edge_model = self.edges.model
        step_map: dict[int, Any] = {}
        for step in self.steps.order_by("pk"):
            copied = step_model(
                workflow=published,
                key=step.key,
                name=step.name,
                step_class=step.step_class,
                config=copy.deepcopy(step.config),
                input_binding=copy.deepcopy(step.input_binding),
                join_rule=step.join_rule,
                is_entry=step.is_entry,
                position=copy.deepcopy(step.position),
            )
            DefinitionQuerySet.bind_instance(copied, published)
            copied.save()
            step_map[step.pk] = copied
        for edge in self.edges.select_related("source", "target").order_by("pk"):
            copied_edge = edge_model(
                workflow=published,
                source=step_map[edge.source_id],
                target=step_map[edge.target_id],
                condition=edge.condition,
            )
            DefinitionQuerySet.bind_instance(copied_edge, published)
            copied_edge.save()

    def _definition_signature(self) -> dict[str, Any]:
        """Return the versioned definition content for publish idempotency."""

        return {
            "workflow": {
                "name": self.name,
                "description": self.description,
                "purpose": str(self.purpose),
                "subject_declaration": self.subject_declaration,
                "error_workflow_id": self.error_workflow_id,
                "max_steps": self.max_steps,
                "budget": copy.deepcopy(self.budget),
                "input_schema": copy.deepcopy(self.input_schema),
                "output_schema": copy.deepcopy(self.output_schema),
                "result_rules": copy.deepcopy(self.result_rules),
            },
            "steps": [
                {
                    "key": step.key,
                    "name": step.name,
                    "step_class": step.step_class,
                    "config": copy.deepcopy(step.config),
                    "input_binding": copy.deepcopy(step.input_binding),
                    "join_rule": str(step.join_rule),
                    "is_entry": step.is_entry,
                    "position": copy.deepcopy(step.position),
                }
                for step in self.steps.order_by("key", "pk")
            ],
            "edges": [
                {
                    "source": edge.source.key,
                    "target": edge.target.key,
                    "condition": edge.condition,
                }
                for edge in self.edges.select_related("source", "target").order_by(
                    "source__key",
                    "target__key",
                    "condition",
                    "pk",
                )
            ],
        }

    def _validate_publishable(self) -> None:
        """Validate the exact locked graph before creating an executable snapshot."""

        self.validate_readiness()

    def graph_diagnostics(self) -> tuple[Any, ...]:
        """Return readiness diagnostics for the currently persisted definition."""

        return type(self).objects.definition_graph(self).diagnostics()

    def validate_readiness(self) -> None:
        """Raise all readiness diagnostics for the currently persisted definition."""

        type(self).objects.definition_graph(self).validate()

    def _persisted_save_snapshot(self) -> Self | None:
        """Return the persisted status and stable key for save guards."""

        if self._state.adding:
            return None
        try:
            return cast(Self, type(self)._base_manager.only("status", "key").get(pk=self.pk))
        except ObjectDoesNotExist:
            return None

    def _raise_if_immutable_save(self, persisted: Self | None) -> None:
        """Reject writes to persisted published or archived workflow definitions."""

        if persisted is None or getattr(self, "_allow_immutable_status_save", False):
            return
        if persisted.is_immutable:
            raise ValidationError("Published workflow versions are immutable.")

    def _raise_if_key_changed(self, persisted: Self | None) -> None:
        """Keep an assigned stable key immutable while allowing legacy backfill."""

        if persisted is None:
            return
        if persisted.key and self.key != persisted.key:
            raise ValidationError({"key": "Workflow stable keys are immutable once assigned."})

    def _propagate_resource_key_backfill(self) -> None:
        """Copy a resource-assigned stable key to legacy published versions."""

        if not self.key:
            return
        versions = type(self)._base_manager.filter(published_from=self)
        if versions.exclude(key__in=("", self.key)).exists():
            raise ValidationError({"key": "Published workflow versions disagree with their lineage stable key."})
        models.QuerySet.update(
            versions.filter(key=""),
            key=self.key,
            updated_at=timezone.now(),
        )

    @property
    def is_immutable(self) -> bool:
        """Return whether this workflow version rejects definition edits."""

        return self.status in {WorkflowStatus.TEST, WorkflowStatus.PUBLISHED, WorkflowStatus.ARCHIVED}


class WorkflowDefinitionChildMixin:
    """Share resource-install ownership for rows belonging to a Workflow."""

    @classmethod
    def resource_import_owner(cls) -> type[Any]:
        return cls._meta.get_field("workflow").remote_field.model

    @classmethod
    def resource_write_preparation(cls, resource: Any, dataset: Any) -> ResourceWritePreparation | None:
        """Declare old and proposed workflow parents for a child resource batch."""

        workflows = set(resource.related_instances(dataset, "workflow"))
        workflows.update(
            instance.workflow
            for xref in dataset["_xref"]
            if (instance := resource.instance_for_xref(xref)) is not None
        )
        if not workflows:
            return None
        workflow_model = cls._meta.get_field("workflow").remote_field.model
        return ResourceWritePreparation(workflow_model, frozenset(row.pk for row in workflows))


class Step(WorkflowDefinitionChildMixin, ImplDefaultsMixin, AuditMixin, AngeeDataModel):
    """One node in a workflow definition graph."""

    runtime = True

    sqid_prefix = "wfs_"
    workflow = models.ForeignKey("workflows.Workflow", on_delete=models.CASCADE, related_name="steps")
    key = models.SlugField(max_length=100)
    name = models.CharField(max_length=200)
    step_class = ImplClassField(
        base_class=StepImpl,
        registry_setting="ANGEE_WORKFLOW_STEP_CLASSES",
    )
    config = models.JSONField(default=dict, blank=True)
    input_binding = models.JSONField(null=True, blank=True, default=None)
    join_rule = StateField(choices_enum=JoinRule, default=JoinRule.ALL_SUCCESS)
    is_entry = models.BooleanField(default=False)
    position = models.JSONField(default=dict, blank=True)

    objects = StepManager()

    class Meta:
        """Django model options for workflow steps."""

        abstract = True
        ordering = ("workflow", "key")
        rebac_resource_type = "workflows/step"
        constraints = (models.UniqueConstraint(fields=("workflow", "key"), name="uniq_workflows_step_key"),)

    def __str__(self) -> str:
        """Return the step's display label."""

        return self.name or self.key

    def config_projection(self) -> StepConfigProjection:
        """Project legacy config for repair without rewriting its stored value."""

        impl = cast(type[StepImpl], self.resolve_impl("step_class"))
        raw = copy.deepcopy(self.config)
        try:
            impl.validate_config(raw)
            value = impl.normalize_config(raw) if impl.config_model is not None else raw
            return StepConfigProjection(value=value, errors={})
        except ValidationError as error:
            return StepConfigProjection(value=raw, errors=error.message_dict)

    def clean(self) -> None:
        """Validate structural draft fields without requiring readiness."""

        super().clean()
        if not isinstance(self.config, Mapping):
            raise ValidationError({"config": "Step config must be an object."})
        if self.input_binding is not None and not isinstance(self.input_binding, Mapping):
            raise ValidationError({"input_binding": "Step input binding must be an object or null."})
        try:
            self.resolve_impl("step_class")
        except ValidationError:
            raise
        except Exception as error:
            raise ValidationError({"step_class": "Unknown workflow step class."}) from error

    def validate_impl_configs(self, *, update_fields: Any = None) -> None:
        """Canonicalize complete drafts while preserving incomplete object configs."""

        if not self._state.adding and update_fields is not None and "config" not in update_fields:
            return
        if not isinstance(self.config, Mapping):
            raise ValidationError({"config": "Step config must be an object."})
        impl = cast(type[StepImpl], self.resolve_impl("step_class"))
        if impl.config_model is None:
            return
        try:
            self.config = impl.normalize_config(self.config)
        except ValidationError:
            # Incomplete typed config is a readiness diagnostic, not a storage error.
            self.config = copy.deepcopy(dict(self.config))

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the step after enforcing parent immutability and validation."""

        alias = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        old_parent_id = None
        if not self._state.adding:
            old_parent_id = _definition_rows(type(self), alias).filter(pk=self.pk).values_list(
                "workflow_id", flat=True
            ).first()
        parent_ids = {value for value in (old_parent_id, self.workflow_id) if value is not None}
        manager = type(self.workflow).objects
        with manager._definition_write(parent_ids, using=alias):
            old = None if self._state.adding else _definition_rows(type(self), alias).filter(pk=self.pk).first()
            if not self._state.adding and (old is None or old.workflow_id != old_parent_id):
                raise ValidationError("The stored workflow step changed while it was being edited.")
            if old is not None and old.workflow_id != self.workflow_id:
                if _definition_rows(type(self), alias).filter(
                    models.Q(outgoing_edges__isnull=False) | models.Q(incoming_edges__isnull=False), pk=self.pk
                ).exists():
                    raise ValidationError({"workflow": "A connected step cannot move to another workflow."})
            with DefinitionQuerySet.caller_context(self):
                self.full_clean()
            update_fields = kwargs.get("update_fields")
            self.validate_impl_configs(update_fields=update_fields)
            fields = {
                "workflow_id",
                "key",
                "name",
                "step_class",
                "config",
                "input_binding",
                "join_rule",
                "is_entry",
                "position",
            }
            updated = None if update_fields is None else set(update_fields)
            considered = fields if updated is None else {
                field for field in fields if field in updated or field.removesuffix("_id") in updated
            }
            changed = old is None or any(
                getattr(old, field) != getattr(self, field)
                for field in considered
            )
            with _workflow_child_create(self):
                super().save(*args, **kwargs)
            session = _definition_write_session.get()
            if changed and session is not None and self.workflow_id not in session.copy_target_ids:
                for workflow_id in parent_ids:
                    manager.mark_definition_changed(workflow_id)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Delete only steps belonging to mutable workflow rows."""

        alias = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        workflow_id = _definition_rows(type(self), alias).filter(pk=self.pk).values_list(
            "workflow_id", flat=True
        ).first()
        if workflow_id is None:
            return (0, {})
        manager = type(self.workflow).objects
        with manager._definition_write((workflow_id,), using=alias):
            persisted = _definition_rows(type(self), alias).get(pk=self.pk)
            if persisted.workflow_id != workflow_id:
                raise ValidationError("The stored workflow step changed while it was being deleted.")
            edge_model = persisted.outgoing_edges.model
            edges = edge_model.objects.using(alias).all().bound_to(self).filter(
                models.Q(source_id=self.pk) | models.Q(target_id=self.pk)
            ).delete()
            with DefinitionQuerySet.caller_context(self):
                step = super().delete(*args, **kwargs)
            session = _definition_write_session.get()
            if session is not None and workflow_id not in session.copy_target_ids:
                manager.mark_definition_changed(workflow_id)
            return _combined_delete_results(edges, step)

class Edge(WorkflowDefinitionChildMixin, AuditMixin, AngeeDataModel):
    """Directed edge between two workflow steps."""

    runtime = True

    sqid_prefix = "wfe_"
    workflow = models.ForeignKey("workflows.Workflow", on_delete=models.CASCADE, related_name="edges")
    source = models.ForeignKey("workflows.Step", on_delete=models.CASCADE, related_name="outgoing_edges")
    target = models.ForeignKey("workflows.Step", on_delete=models.CASCADE, related_name="incoming_edges")
    condition = models.SlugField(max_length=100, blank=True, default="")

    objects = EdgeManager()

    class Meta:
        """Django model options for workflow edges."""

        abstract = True
        ordering = ("workflow", "source", "target", "condition")
        rebac_resource_type = "workflows/edge"
        constraints = (
            models.UniqueConstraint(fields=("source", "target", "condition"), name="uniq_workflows_edge_condition"),
        )

    def __str__(self) -> str:
        """Return a compact edge label."""

        return f"{self.source_id}->{self.target_id}:{self.condition}"

    def clean(self) -> None:
        """Validate that an edge is fully contained in one workflow."""

        super().clean()
        errors: dict[str, str] = {}
        if self.workflow_id is not None and self.source_id is not None and self.source.workflow_id != self.workflow_id:
            errors["source"] = "Edge source must belong to the same workflow."
        if self.workflow_id is not None and self.target_id is not None and self.target.workflow_id != self.workflow_id:
            errors["target"] = "Edge target must belong to the same workflow."
        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the edge after enforcing parent immutability and validation."""

        alias = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        old_parent_id = None
        if not self._state.adding:
            old_parent_id = _definition_rows(type(self), alias).filter(pk=self.pk).values_list(
                "workflow_id", flat=True
            ).first()
        step_model = self._meta.get_field("source").remote_field.model
        parent_ids = {value for value in (old_parent_id, self.workflow_id) if value is not None}
        manager = type(self.workflow).objects
        with manager._definition_write(parent_ids, using=alias):
            old = None if self._state.adding else _definition_rows(type(self), alias).filter(pk=self.pk).first()
            if not self._state.adding and (old is None or old.workflow_id != old_parent_id):
                raise ValidationError("The stored workflow edge changed while it was being edited.")
            endpoints = dict(
                _definition_rows(step_model, alias)
                .filter(pk__in=(self.source_id, self.target_id))
                .values_list("pk", "workflow_id")
            )
            if endpoints.get(self.source_id) != self.workflow_id:
                raise ValidationError({"source": "Edge source must belong to the same workflow."})
            if endpoints.get(self.target_id) != self.workflow_id:
                raise ValidationError({"target": "Edge target must belong to the same workflow."})
            with DefinitionQuerySet.caller_context(self):
                self.full_clean()
            update_fields = kwargs.get("update_fields")
            fields = {"workflow_id", "source_id", "target_id", "condition"}
            updated = None if update_fields is None else set(update_fields)
            considered = fields if updated is None else {
                field for field in fields if field in updated or field.removesuffix("_id") in updated
            }
            changed = old is None or any(
                getattr(old, field) != getattr(self, field)
                for field in considered
            )
            with _workflow_child_create(self):
                super().save(*args, **kwargs)
            session = _definition_write_session.get()
            if changed and session is not None and self.workflow_id not in session.copy_target_ids:
                for workflow_id in parent_ids:
                    manager.mark_definition_changed(workflow_id)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Delete only edges belonging to mutable workflow rows."""

        alias = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        workflow_id = _definition_rows(type(self), alias).filter(pk=self.pk).values_list(
            "workflow_id", flat=True
        ).first()
        if workflow_id is None:
            return (0, {})
        manager = type(self.workflow).objects
        with manager._definition_write((workflow_id,), using=alias):
            persisted = _definition_rows(type(self), alias).get(pk=self.pk)
            if persisted.workflow_id != workflow_id:
                raise ValidationError("The stored workflow edge changed while it was being deleted.")
            with DefinitionQuerySet.caller_context(self):
                result = super().delete(*args, **kwargs)
            session = _definition_write_session.get()
            if session is not None and workflow_id not in session.copy_target_ids:
                manager.mark_definition_changed(workflow_id)
            return result

def check_event_trigger_publishers(
    app_configs: list[object] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Report persisted event triggers with invalid publisher declarations."""

    del app_configs, kwargs
    try:
        trigger_model = apps.get_model("workflows", "Trigger")
    except LookupError:
        return []
    try:
        errors = []
        for trigger in trigger_model._base_manager.filter(kind=TriggerKind.EVENT).order_by("pk").iterator():
            try:
                trigger.validated_config(require_publisher=True)
            except ValidationError as error:
                errors.append(
                    checks.Error(
                        f"Workflow trigger {trigger.pk}: {'; '.join(error.messages)}",
                        obj=trigger_model,
                        id="angee.workflows.E001",
                    )
                )
                if len(errors) == 20:
                    break
    except (OperationalError, ProgrammingError):
        return []
    return errors


def check_database_command_replay_declarations(
    app_configs: list[object] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Warn when a registered database command leaves recovery policy implicit."""

    del app_configs, kwargs
    implementations = resolve_all_impl_classes(
        "ANGEE_WORKFLOW_STEP_CLASSES",
        StepImpl,
        on_error=lambda key, error: None,
    )
    warnings = []
    for implementation in implementations:
        recovery_owner = next(
            base for base in implementation.__mro__ if "recovery_capability" in base.__dict__
        )
        if (
            implementation.execution_mode is StepExecutionMode.DATABASE_COMMAND
            and implementation.replay_mode is None
            and recovery_owner is StepImpl
        ):
            warnings.append(
                checks.Warning(
                    f"Database command {implementation.key!r} has no explicit recovery policy.",
                    hint="Declare replay_mode or override recovery_capability() with the reason replay is unavailable.",
                    obj=implementation,
                    id="angee.workflows.W001",
                )
            )
    return warnings


class Trigger(AuditMixin, AngeeDataModel):
    """Start rule attached to a workflow lineage head.

    The default event publisher is the GraphQL change feed. Same-row donors
    contribute additional publishers through ``event_publisher_model`` and own
    their source-specific matching and admission rules.
    """

    runtime = True
    trigger_protected_fields = ("execution_actor", "execution_actor_id")

    sqid_prefix = "wft_"
    workflow = models.ForeignKey("workflows.Workflow", on_delete=models.CASCADE, related_name="triggers")
    execution_actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    kind = StateField(choices_enum=TriggerKind, default=TriggerKind.MANUAL)
    enabled = models.BooleanField(default=False)
    config = models.JSONField(default=dict, blank=True)
    event_model_label = models.CharField(max_length=200, blank=True, default="")
    next_fire_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_fire_at = models.DateTimeField(null=True, blank=True)
    hourly_window_started_at = models.DateTimeField(null=True, blank=True)
    hourly_fire_count = models.PositiveIntegerField(default=0)

    objects = TriggerManager()

    class Meta:
        """Django model options for workflow triggers."""

        abstract = True
        ordering = ("workflow", "kind", "created_at")
        rebac_resource_type = "workflows/trigger"
        indexes = (
            models.Index(
                fields=("event_model_label",),
                condition=models.Q(kind=TriggerKind.EVENT, enabled=True),
                name="idx_wft_event_enabled",
            ),
        )

    def __str__(self) -> str:
        """Return the trigger's display label."""

        return f"{self.workflow_id}:{self.kind}"

    def event_input_snapshot(self, subject: models.Model, actor: Any, source: Any) -> JsonPresence:
        """Return immutable input captured when one new event occurrence is admitted.

        Same-row donors may validate their own scope and cooperatively extend the
        returned JSON object. The base trigger contributes no event input.
        """

        del subject, actor, source
        return JsonPresence()

    def event_publisher_model(self, declaration: EventTriggerConfig) -> type[models.Model]:
        """Resolve a declared publisher; same-row donors handle their own source keys."""

        if declaration.source != EventSource.CHANGE_PUBLISHED:
            raise ValidationError({"config": f"Unknown event publisher {declaration.source!r}."})
        for model in GraphQLSchemas.from_discovery().change_publisher_models():
            if model._meta.label_lower == declaration.model:
                return model
        raise ValidationError(
            {
                "event_model_label": (
                    f"Event trigger target {declaration.model!r} is not in the change feed; {_CHANGE_FEED_FIX}."
                )
            }
        )

    def event_subject_matches(self, subject: models.Model, *, source: str) -> bool:
        """Let publisher donors constrain a delivery to their declared scope."""

        return True

    def validate_event_admission(self, subject: models.Model, *, source: str, dedup_key: str) -> None:
        """Validate publisher-specific invariants under the locked workflow lineage."""

    def clean(self) -> None:
        """Validate lineage ownership and trigger declaration shape."""

        super().clean()
        if self.workflow_id is not None and self.workflow.published_from_id is not None:
            raise ValidationError({"workflow": "Triggers attach only to workflow lineage heads."})
        declaration = self.validated_config(require_publisher=self.enabled)
        self._sync_index_fields(declaration)
        if self.enabled and type(self.workflow).objects.current_published_for(self.workflow) is None:
            raise ValidationError({"enabled": "Publish this workflow before enabling its trigger."})

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the trigger after model validation."""

        adding = self._state.adding
        if adding:
            self.enabled = False
        update_fields = kwargs.get("update_fields")
        alias = kwargs.get("using") or self._state.db or router.db_for_write(type(self), instance=self)
        operational_fields = {
            "last_fire_at",
            "hourly_window_started_at",
            "hourly_fire_count",
            "next_fire_at",
            "updated_at",
        }
        if update_fields is not None and set(update_fields) <= operational_fields:
            raise RuntimeError("Trigger operational fields are written only by the owning manager.")
        old_workflow_id = None
        discovered_state: tuple[Any, Any, Any, Any, Any] | None = None
        if not adding:
            discovered = system_queryset(type(self), using=alias, lock=None).filter(pk=self.pk).values(
                "workflow_id", "kind", "config", "enabled", "execution_actor_id"
            ).first()
            if discovered is None:
                raise type(self).DoesNotExist
            old_workflow_id = discovered["workflow_id"]
            discovered_state = (
                discovered["workflow_id"],
                discovered["kind"],
                discovered["config"],
                discovered["enabled"],
                discovered["execution_actor_id"],
            )
        workflow_ids = sorted({value for value in (old_workflow_id, self.workflow_id) if value is not None})
        with DefinitionQuerySet.caller_context(self), transaction.atomic(using=alias):
            workflow_model = type(self)._meta.get_field("workflow").remote_field.model
            list(
                system_queryset(workflow_model, using=alias, lock=("self",))
                .filter(pk__in=workflow_ids)
                .order_by("pk")
            )
            persisted_rule: tuple[Any, Any] | None = None
            if not adding:
                locked = system_queryset(type(self), using=alias, lock=("self",)).get(pk=self.pk)
                if locked.workflow_id != old_workflow_id:
                    raise ValidationError({"workflow": "The trigger lineage changed during this edit."})
                locked_state = (
                    locked.workflow_id, locked.kind, locked.config, locked.enabled, locked.execution_actor_id
                )
                if locked_state != discovered_state:
                    raise ValidationError("The trigger changed during this edit; reload and try again.")
                fields = None if update_fields is None else set(update_fields)
                if fields is not None:
                    for field_name in ("workflow_id", "kind", "config", "enabled", "execution_actor_id"):
                        public_name = field_name.removesuffix("_id")
                        if public_name not in fields and field_name not in fields:
                            setattr(self, field_name, getattr(locked, field_name))
                if self.enabled != locked.enabled:
                    raise ValidationError({"enabled": "Use the trigger enable or disable action."})
                persisted_rule = (locked.kind, locked.config)
                self.last_fire_at = locked.last_fire_at
                self.hourly_window_started_at = locked.hourly_window_started_at
                self.hourly_fire_count = locked.hourly_fire_count
                self.next_fire_at = locked.next_fire_at
            self.full_clean()
            if adding and self.kind == TriggerKind.EVENT and isinstance(self.config, dict):
                declaration = self.validated_config()
                if isinstance(declaration, EventTriggerConfig) and "admission_policy" not in self.config:
                    self.config = {**self.config, "admission_policy": str(declaration.admission_policy)}
            rule_changed = persisted_rule is not None and persisted_rule != (self.kind, self.config)
            cadence_changed = False
            if rule_changed and self.enabled and persisted_rule is not None:
                persisted_declaration = validate_trigger_config(cast(Any, persisted_rule[0]), persisted_rule[1])
                declaration = self.validated_config()
                persisted_cadence = (
                    persisted_declaration.cadence
                    if isinstance(persisted_declaration, ScheduleTriggerConfig)
                    else None
                )
                cadence = declaration.cadence if isinstance(declaration, ScheduleTriggerConfig) else None
                cadence_changed = persisted_cadence != cadence
            if rule_changed and self.enabled and cadence_changed:
                self.next_fire_at = (
                    self.initial_fire_at(now=timezone.now()) if self.kind == TriggerKind.SCHEDULE else None
                )
            if update_fields is not None:
                fields = set(update_fields)
                if {"kind", "config", "event_model_label"} & fields:
                    fields.update({"event_model_label", "next_fire_at"})
                kwargs["update_fields"] = fields
            self._save_validated(*args, **kwargs)

    def _save_validated(self, *args: Any, **kwargs: Any) -> None:
        """Persist after the owning locked path has completed validation."""

        with _workflow_child_create(self):
            super().save(*args, **kwargs)

    def enable(self) -> None:
        """Enable this trigger through the model owner."""

        enabled = type(self).objects.db_manager(self._state.db).set_enabled(self, enabled=True)
        self.enabled = enabled.enabled
        self.next_fire_at = enabled.next_fire_at

    def disable(self) -> None:
        """Disable this trigger through the model owner."""

        disabled = type(self).objects.db_manager(self._state.db).set_enabled(self, enabled=False)
        self.enabled = disabled.enabled
        self.next_fire_at = disabled.next_fire_at

    def rate_limit_allows(self, *, timestamp: datetime) -> bool:
        """Return whether this trigger can fire at ``timestamp``."""

        declaration = self.validated_config()
        cooldown_seconds = declaration.cooldown_seconds
        if cooldown_seconds and self.last_fire_at is not None:
            if self.last_fire_at + timedelta(seconds=cooldown_seconds) > timestamp:
                return False

        hourly_cap = declaration.hourly_cap
        if hourly_cap is None:
            return True
        window_start = self.hourly_window_started_at
        if window_start is None or timestamp - window_start >= timedelta(hours=1):
            return True
        return int(self.hourly_fire_count) < hourly_cap

    def record_fire(self, *, timestamp: datetime) -> None:
        """Record one trigger fire and persist rate-limit counters."""

        trigger = type(self).objects.db_manager(self._state.db).record_fire(self, timestamp=timestamp)
        self.last_fire_at = trigger.last_fire_at
        self.hourly_window_started_at = trigger.hourly_window_started_at
        self.hourly_fire_count = trigger.hourly_fire_count
        self.next_fire_at = trigger.next_fire_at

    def condition_matches(self, sender: type[models.Model], instance: models.Model) -> bool:
        """Return whether this event trigger matches a saved model instance."""

        condition = self.config_mapping.get("condition", {})
        if not isinstance(condition, Mapping):
            return False
        with system_context(reason="workflows.event_triggers.condition"):
            return sender._default_manager.using(instance._state.db).filter(pk=instance.pk, **dict(condition)).exists()

    def initial_fire_at(self, *, now: datetime) -> datetime | None:
        """Return the first persisted due timestamp for this schedule trigger."""

        declaration = self.validated_config()
        if not isinstance(declaration, ScheduleTriggerConfig):
            return None
        return declaration.next_fire_at(after=now, now=now)

    def compute_next_fire_at(self, *, after: datetime, now: datetime) -> datetime | None:
        """Return the next scheduled occurrence after ``after`` and later than ``now``."""

        declaration = self.validated_config()
        if not isinstance(declaration, ScheduleTriggerConfig):
            return None
        return declaration.next_fire_at(after=after, now=now)

    def validated_config(self, *, require_publisher: bool = False) -> TriggerConfig:
        """Return the declaration-owned rule and optionally verify event delivery."""

        try:
            declaration = validate_trigger_config(cast(Any, self.kind), self.config)
        except (ValueError, TypeError) as error:
            raise ValidationError({"config": str(error)}) from error
        if require_publisher and isinstance(declaration, EventTriggerConfig):
            event_model = self.event_publisher_model(declaration)
            try:
                condition_query = event_model._base_manager.filter(**(declaration.condition or {})).query
                try:
                    condition_query.get_compiler(using=self._state.db or DEFAULT_DB_ALIAS).as_sql()
                except (EmptyResultSet, FullResultSet):
                    pass
            except (FieldError, LookupError, TypeError, ValueError) as error:
                raise ValidationError({"condition": f"Event trigger condition is invalid: {error}"}) from error
        return declaration

    def activation_blocker(self, *, has_current_publication: bool | None = None) -> str | None:
        """Return the rule or publication reason that prevents activation."""

        try:
            self.validated_config(require_publisher=True)
        except ValidationError as error:
            return "; ".join(error.messages)
        if has_current_publication is None:
            has_current_publication = type(self.workflow).objects.current_published_for(self.workflow) is not None
        if not has_current_publication:
            return "Publish this workflow before enabling its trigger."
        return None

    @property
    def config_mapping(self) -> Mapping[str, Any]:
        """Return trigger config when it is a JSON object."""

        return self.config if isinstance(self.config, Mapping) else {}

    def _sync_index_fields(self, declaration: TriggerConfig) -> None:
        """Mirror config-owned event declarations into indexed query fields."""

        self.event_model_label = declaration.model if isinstance(declaration, EventTriggerConfig) else ""


class WorkflowRun(AuditMixin, RecordRefMixin, AngeeDataModel):
    """One execution of a pinned published workflow version."""

    runtime = True
    rebac_grantable = {"reader": "write", "operator": "write"}
    invocation_identity_attnames = frozenset({
        "workflow_id", "origin", "trigger_id", "parent_step_run_id", "parent_relation",
        "reprocessed_from_id", "subject_content_type_id", "subject_object_id",
        "dedup_key", "occurrence_id", "admitted_actor_ref", "input_present", "input",
        "test_request_actor_ref", "test_scope", "test_step_id", "test_source_step_id",
        "test_repair_source_attempt_id", "recovery_source_attempt_id",
        "recovery_request_actor_ref", "recovery_mode", "recovery_uncertainty_ack",
    })

    record_ref_field_prefix = "subject"

    sqid_prefix = "wfr_"
    workflow = models.ForeignKey("workflows.Workflow", on_delete=models.PROTECT, related_name="runs")
    origin = StateField(choices_enum=RunOrigin, default=RunOrigin.UNKNOWN)
    trigger = models.ForeignKey(
        "workflows.Trigger",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="runs",
    )
    parent_step_run = models.ForeignKey(
        "workflows.StepRun",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="child_runs",
    )
    parent_relation = StateField(choices_enum=ParentRelation, blank=True, default="", editable=False)
    reprocessed_from = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="reprocessed_runs", editable=False
    )
    status = StateField(choices_enum=RunStatus, default=RunStatus.PENDING)
    subject_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    subject_object_id = models.PositiveBigIntegerField(null=True, blank=True)
    subject = GenericForeignKey("subject_content_type", "subject_object_id")
    dedup_key = models.CharField(max_length=255, unique=True, null=True, blank=True)
    occurrence_id = models.CharField(max_length=255, null=True, blank=True, editable=False)
    admitted_actor_ref = models.CharField(max_length=255, blank=True, default="", editable=False)
    test_request_actor_ref = models.CharField(max_length=255, blank=True, editable=False)
    test_scope = StateField(choices_enum=WorkflowScope, blank=True, default="", editable=False)
    test_step = models.ForeignKey(
        "workflows.Step",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="selected_test_runs",
        editable=False,
    )
    test_source_step_id = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    test_repair_source_attempt = models.ForeignKey(
        "workflows.StepAttempt",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="repair_test_runs",
        editable=False,
    )
    recovery_source_attempt = models.ForeignKey(
        "workflows.StepAttempt",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="recovery_runs",
        editable=False,
    )
    recovery_request_actor_ref = models.CharField(max_length=255, blank=True, editable=False)
    recovery_mode = models.CharField(max_length=32, blank=True, editable=False)
    recovery_uncertainty_ack = models.BooleanField(default=False, editable=False)
    input_present = models.BooleanField(default=False, editable=False)
    input = models.JSONField(null=True, blank=True, editable=False)
    wake_at = models.DateTimeField(null=True, blank=True, db_index=True)
    deliveries = models.PositiveBigIntegerField(default=0)
    steps_taken = models.PositiveIntegerField(default=0)
    budget_spent = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    result = models.JSONField(null=True, blank=True, editable=False)

    status_transitions = StateTransitions(
        status,
        {
            RunStatus.PENDING: [RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELED],
            RunStatus.RUNNING: [RunStatus.WAITING, RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELED],
            RunStatus.WAITING: [RunStatus.RUNNING, RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELED],
        },
    )

    objects = WorkflowRunManager()
    system_objects = WorkflowRunSystemManager()

    class Meta:
        """Django model options for workflow runs."""

        abstract = True
        base_manager_name = "system_objects"
        ordering = ("-created_at", "sqid")
        rebac_resource_type = "workflows/run"
        indexes = (models.Index(fields=("subject_content_type", "subject_object_id"), name="idx_wfr_subject_ref"),)
        constraints = (
            models.CheckConstraint(
                condition=(
                    models.Q(origin=RunOrigin.TEST)
                    | models.Q(test_repair_source_attempt__isnull=True)
                ),
                name="chk_wfr_test_repair_source",
            ),
            models.CheckConstraint(
                condition=models.Q(input_present=True) | models.Q(input__isnull=True),
                name="chk_wfr_absent_input_null",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(origin=RunOrigin.TEST, test_request_actor_ref__gt="")
                    | (~models.Q(origin=RunOrigin.TEST) & models.Q(test_request_actor_ref=""))
                ),
                name="chk_wfr_test_request_actor",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        origin=RunOrigin.TEST,
                        test_scope__in=("", WorkflowScope.WHOLE),
                        test_step__isnull=True,
                        test_source_step_id__isnull=True,
                    )
                    | models.Q(
                        origin=RunOrigin.TEST,
                        test_scope=WorkflowScope.NODE,
                        test_step__isnull=False,
                        test_source_step_id__isnull=False,
                    )
                    | (
                        ~models.Q(origin=RunOrigin.TEST)
                        & models.Q(
                            test_scope="",
                            test_step__isnull=True,
                            test_source_step_id__isnull=True,
                        )
                    )
                ),
                name="chk_wfr_test_scope",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        origin=RunOrigin.RECOVERY,
                        recovery_source_attempt__isnull=False,
                        recovery_request_actor_ref__gt="",
                        recovery_mode__gt="",
                    )
                    | (
                        ~models.Q(origin=RunOrigin.RECOVERY)
                        & models.Q(
                            recovery_source_attempt__isnull=True,
                            recovery_request_actor_ref="",
                            recovery_mode="",
                            recovery_uncertainty_ack=False,
                        )
                    )
                ),
                name="chk_wfr_recovery_identity",
            ),
            models.UniqueConstraint(
                fields=("parent_step_run",),
                condition=models.Q(parent_step_run__isnull=False),
                name="uniq_workflows_run_parent_step_run",
            ),
        )

    def admission_actor_subject(self) -> Any | None:
        """Return the immutable admission subject, including historical rows."""

        if self.admitted_actor_ref:
            return canonical_subject_ref(self.admitted_actor_ref)
        return None

    def admission_actor(self) -> Any | None:
        """Resolve the retained admission subject when that principal still exists."""

        subject = self.admission_actor_subject()
        return None if subject is None else resolve_subjects((subject,)).get(subject)

    def execution_admission_actor(self) -> Any:
        """Resolve the immutable actor admitted by this recovery lineage root."""

        root_id = self.execution_lineage_root_id()
        root = (
            self
            if root_id == self.pk
            else system_queryset(type(self), using=self._state.db, lock=None).get(pk=root_id)
        )
        actor = root.admission_actor()
        if actor is None:
            raise ValidationError({"actor": "Workflow execution requires its admitted actor."})
        return actor

    @property
    def is_terminal(self) -> bool:
        """Return whether this run has reached a terminal status."""

        return self.status in RunStatus.TERMINAL

    def execution_lineage_root_id(self) -> int:
        """Return the original run retained by this recovery provenance chain.

        This identity does not grant access to either run. Callers must still
        authorize every record and validate their domain payloads independently.
        """

        current = self
        seen: set[int] = set()
        while current.origin == RunOrigin.RECOVERY:
            if current.pk is None or current.pk in seen:
                raise ValidationError({"run": "Workflow recovery lineage is cyclic."})
            seen.add(current.pk)
            source = current.recovery_source_attempt
            if (
                source is None
                or source.step_run.current_attempt_id != source.pk
                or source.step_run.status not in {StepRunStatus.FAILED, StepRunStatus.CANCELED}
                or source.step_run.run.workflow_id != current.workflow_id
            ):
                raise ValidationError({"run": "Workflow recovery lineage is incomplete or stale."})
            current = source.step_run.run
        if current.pk is None:
            raise ValidationError({"run": "Workflow execution lineage requires a saved run."})
        return current.pk

    def delivery_target(self) -> Self:
        """Return the original run identity subscribed by parent operations."""

        root_id = self.execution_lineage_root_id()
        if root_id == self.pk:
            return self
        return system_queryset(type(self), using=self._state.db, lock=None).get(pk=root_id)

    def same_execution_lineage(self, other: Any) -> bool:
        """Return whether two authorized runs share exact recovery provenance."""

        if not isinstance(other, type(self)):
            raise TypeError("Workflow execution lineage requires two WorkflowRun records.")
        return self.execution_lineage_root_id() == other.execution_lineage_root_id()

    def allows_test_step(self, step: Any) -> bool:
        """Return whether the pinned test scope admits one copied step."""

        if step is None or step.workflow_id != self.workflow_id:
            return False
        if self.origin != RunOrigin.TEST or self.test_scope in {"", WorkflowScope.WHOLE}:
            return True
        if self.test_scope != WorkflowScope.NODE or self.test_step_id is None:
            return False

        with system_context(reason="workflows.test_scope.plan"):
            plan = WorkflowGraph.from_workflow(self.workflow).test_plan(
                GraphIdentity(existing_id=self.test_step_id)
            )
        return GraphIdentity(existing_id=step.pk) in plan.executable

    @classmethod
    def waiting_projection_annotation(cls) -> dict[str, Any]:
        """Return declared active wait kind and the next genuine scheduled wake."""

        step_run = cls._meta.apps.get_model("workflows", "StepRun")
        waiting = step_run.objects.filter(run_id=models.OuterRef("pk"), status=StepRunStatus.WAITING)
        scheduled = waiting.filter(waiting_kind=WaitingKind.SCHEDULED)
        return {
            "_workflow_waiting_kind": models.Case(
                models.When(~models.Q(status=RunStatus.WAITING), then=models.Value("")),
                models.When(
                    models.Exists(waiting.filter(waiting_kind=WaitingKind.APPROVAL)),
                    then=models.Value(WaitingKind.APPROVAL),
                ),
                models.When(
                    models.Exists(waiting.filter(waiting_kind=WaitingKind.EXTERNAL)),
                    then=models.Value(WaitingKind.EXTERNAL),
                ),
                models.When(
                    models.Exists(waiting.filter(waiting_kind=WaitingKind.CHILDREN)),
                    then=models.Value(WaitingKind.CHILDREN),
                ),
                models.When(models.Exists(scheduled), then=models.Value(WaitingKind.SCHEDULED)),
                default=models.Value(""),
                output_field=models.CharField(),
            ),
            "_workflow_next_wake_at": models.Subquery(
                scheduled.filter(run__status=RunStatus.WAITING)
                .order_by("wait_until", "pk")
                .values("wait_until")[:1],
                output_field=models.DateTimeField(),
            ),
        }

    @classmethod
    def active_step_projection_annotation(cls) -> dict[str, Any]:
        """Return the oldest active journal step label without loading the run journal."""

        step_run = cls._meta.apps.get_model("workflows", "StepRun")
        active = step_run.objects.filter(
            run_id=models.OuterRef("pk"),
            status__in=(StepRunStatus.STARTED, StepRunStatus.WAITING),
        ).order_by("created_at", "pk")
        return {
            "_workflow_active_step": models.Subquery(
                active.annotate(
                    display=Coalesce(
                        "step__name",
                        "system_kind",
                        output_field=models.CharField(),
                    ),
                ).values("display")[:1],
                output_field=models.CharField(),
            ),
        }

    def awaiting_decision(self) -> bool:
        """Return whether this run has an unresolved workflow decision."""

        return self.step_runs.filter(decisions__verdict=Verdict.PENDING).exists()

    @transition(status, source=RunStatus.PENDING, target=RunStatus.RUNNING, on_success=save_state)
    def mark_running(self) -> None:
        """Mark a pending run as actively orchestrating."""

    @transition(status, source=RunStatus.WAITING, target=RunStatus.RUNNING, on_success=save_state)
    def resume(self) -> None:
        """Mark a waiting run as actively orchestrating again."""

    @transition(
        status,
        source=RunStatus.RUNNING,
        target=RunStatus.WAITING,
        on_success=save_state,
    )
    def mark_waiting(self, *, wake_at: Any = None) -> None:
        """Mark a run as waiting on durable external or timer state."""

        self.wake_at = wake_at
        self._transition_fields = {"wake_at"}

    @transition(
        status,
        source=[RunStatus.RUNNING, RunStatus.WAITING],
        target=RunStatus.SUCCEEDED,
        on_success=_save_run_terminal,
    )
    def mark_succeeded(self, *, outcome: str = "completed", output: Any = _UNSET_RESULT_OUTPUT) -> None:
        """Mark a run as successful."""

        self.result = {
            "status": "succeeded", "outcome": outcome,
            "output": {} if output is _UNSET_RESULT_OUTPUT else output, "error": None,
        }
        self.wake_at = None
        self._transition_fields = {"wake_at", "result"}

    @transition(
        status,
        source=[RunStatus.PENDING, RunStatus.RUNNING, RunStatus.WAITING],
        target=RunStatus.FAILED,
        on_success=_save_run_terminal,
    )
    def mark_failed(self, error: str = "") -> None:
        """Mark a run as failed with an optional durable error message."""

        self.error = error
        self.result = {"status": "failed", "outcome": "failed", "output": None, "error": error}
        self.wake_at = None
        self._transition_fields = {"error", "wake_at", "result"}

    @transition(
        status,
        source=[RunStatus.PENDING, RunStatus.RUNNING, RunStatus.WAITING],
        target=RunStatus.CANCELED,
        on_success=_save_run_terminal,
    )
    def mark_canceled(self) -> None:
        """Mark a run as canceled."""

        self.result = {"status": "canceled", "outcome": "canceled", "output": None, "error": None}
        self.wake_at = None
        self._transition_fields = {"wake_at", "result"}

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the run while keeping start identity and input immutable."""

        if self._state.adding:
            self._save_run_guarded(*args, **kwargs)
            return
        alias = kwargs.get("using") or self._state.db or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=alias):
            retained = (
                system_queryset(type(self), using=alias, lock=("self",))
                .values("status", "result", *sorted(self.invocation_identity_attnames))
                .get(pk=self.pk)
            )
            changed = {
                field: "Retained workflow invocation identity is immutable."
                for field in self.invocation_identity_attnames
                if not (
                    json_values_equal(retained[field], getattr(self, field))
                    if field == "input" else retained[field] == getattr(self, field)
                )
            }
            if changed:
                raise ValidationError(changed)
            if retained["status"] in RunStatus.TERMINAL and (
                self.status != retained["status"] or self.result != retained["result"]
            ):
                raise ValidationError("A retained terminal workflow run cannot be overwritten.")
            self._save_run_guarded(*args, **kwargs)

    @classmethod
    def invocation_identity_write_names(cls) -> frozenset[str]:
        """Include Django FK field names as well as their stored attnames."""

        return cls.invocation_identity_attnames | frozenset(
            field.name for field in cls._meta.concrete_fields
            if field.attname in cls.invocation_identity_attnames
        )

    def _save_run_guarded(self, *args: Any, **kwargs: Any) -> None:
        """Validate new-run shape; existing identity is checked on the locked row."""

        if self._state.adding:
            if (self.parent_step_run_id is None) != (self.parent_relation == ""):
                raise ValidationError({"parent_relation": "Parent relationship is required exactly for a parent step."})
            if self.result is not None:
                raise ValidationError({"result": "A new workflow run cannot have a terminal result."})
        self._raise_if_test_identity_changed()
        super().save(*args, **kwargs)

    def _raise_if_test_identity_changed(self) -> None:
        """Validate the shape of one newly admitted test request."""

        if self._state.adding:
            if self.origin == RunOrigin.TEST and not self.test_request_actor_ref:
                raise ValidationError({"test_request_actor_ref": "Test runs require their requesting actor."})
            if self.origin != RunOrigin.TEST and self.test_request_actor_ref:
                raise ValidationError({"test_request_actor_ref": "Only test runs have a requesting actor."})
            if self.origin != RunOrigin.TEST and self.test_repair_source_attempt_id is not None:
                raise ValidationError(
                    {"test_repair_source_attempt": "Only test runs retain repair source evidence."}
                )
            if self.origin != RunOrigin.RECOVERY and self.recovery_uncertainty_ack:
                raise ValidationError({
                    "recovery_uncertainty_ack": (
                        "Only recovery runs can retain uncertainty acknowledgement."
                    )
                })
            if self.origin == RunOrigin.TEST:
                if self.test_scope not in WorkflowScope.values:
                    raise ValidationError({"test_scope": "Test runs require a declared scope."})
                if self.test_scope == WorkflowScope.NODE and (
                    self.test_step_id is None or self.test_source_step_id is None
                ):
                    raise ValidationError({"test_step": "Node test runs require an exact selected step."})
                if self.test_scope == WorkflowScope.WHOLE and (
                    self.test_step_id is not None or self.test_source_step_id is not None
                ):
                    raise ValidationError({"test_step": "Whole workflow tests cannot select one step."})
                if self.test_step_id is not None and self.test_step.workflow_id != self.workflow_id:
                    raise ValidationError({"test_step": "The selected test step must belong to the pinned snapshot."})
            elif self.test_scope or self.test_step_id is not None or self.test_source_step_id is not None:
                raise ValidationError({"test_scope": "Only test runs carry test scope facts."})

    def debit_budget(self, delta: Mapping[str, int]) -> None:
        """Atomically add usage deltas to this run's budget ledger."""

        if not delta:
            return
        alias = self._state.db or router.db_for_write(type(self), instance=self)
        with system_context(reason="workflows.runs.debit_budget"), transaction.atomic(using=alias):
            locked = system_queryset(type(self), using=alias, lock=("self",)).get(pk=self.pk)
            spent = dict(locked.budget_spent or {})
            for key, value in delta.items():
                spent[str(key)] = int(spent.get(str(key), 0)) + int(value)
            locked.budget_spent = spent
            locked.save(using=alias, update_fields=["budget_spent", "updated_at"])


class WorkflowTestFixture(AuditMixin, AngeeDataModel):
    """Immutable manual or captured evidence admitted for one workflow test."""

    runtime = True
    sqid_prefix = "wtf_"

    run = models.ForeignKey("workflows.WorkflowRun", on_delete=models.PROTECT, related_name="test_fixtures")
    step = models.ForeignKey("workflows.Step", on_delete=models.PROTECT, related_name="test_fixtures")
    role = StateField(choices_enum=FixtureRole)
    item_index = models.PositiveIntegerField(null=True, blank=True, editable=False)
    value_present = models.BooleanField(default=False, editable=False)
    value = models.JSONField(null=True, blank=True, editable=False)
    outcome = models.SlugField(max_length=100, blank=True, default="", editable=False)
    captured_attempt = models.ForeignKey(
        "workflows.StepAttempt",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="test_fixture_captures",
        editable=False,
    )

    objects = WorkflowTestFixtureManager()

    class Meta:
        abstract = True
        base_manager_name = "objects"
        ordering = ("run_id", "step_id", "role", "item_index", "pk")
        constraints = (
            models.CheckConstraint(
                condition=models.Q(value_present=True) | models.Q(value__isnull=True),
                name="chk_wtf_absent_value_null",
            ),
            models.UniqueConstraint(
                fields=("run", "step", "role", "item_index"),
                name="uniq_workflows_test_fixture_slot",
                nulls_distinct=False,
            ),
        )

    def clean(self) -> None:
        super().clean()
        if self.run_id is not None and self.step_id is not None:
            if self.run.origin != RunOrigin.TEST or self.step.workflow_id != self.run.workflow_id:
                raise ValidationError({"step": "Fixture steps must belong to the pinned test snapshot."})
        try:
            validate_json_presence(
                JsonPresence(self.value_present, self.value), label="test fixture value"
            )
        except ValueError as error:
            raise ValidationError({"value": str(error)}) from error

    def save(self, *args: Any, **kwargs: Any) -> None:
        alias = kwargs.get("using") or self._state.db or "default"
        connection = connections[alias]
        expected = (
            (alias, id(connection), id(connection.atomic_blocks[0]), self.run_id)
            if connection.in_atomic_block
            else None
        )
        if (
            not self._state.adding
            or expected is None
            or _test_fixture_write_run.get() != expected
            or getattr(self, "_fixture_owner_write", None) != expected
        ):
            raise TypeError("Workflow test fixtures can only be written by WorkflowRunManager.")
        del self._fixture_owner_write
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise TypeError("Workflow test fixtures are retained admission facts.")


class WorkflowRecoveryEvidence(AuditMixin, AngeeDataModel):
    """Protected link to an accepted predecessor result reused by recovery."""

    runtime = True
    sqid_prefix = "wre_"
    run = models.ForeignKey("workflows.WorkflowRun", on_delete=models.PROTECT, related_name="recovery_evidence")
    source_attempt = models.ForeignKey(
        "workflows.StepAttempt", on_delete=models.PROTECT, related_name="reused_by_recoveries"
    )
    step = models.ForeignKey("workflows.Step", on_delete=models.PROTECT, related_name="recovery_evidence")
    map_index = models.IntegerField(default=-1, editable=False)
    objects = WorkflowRecoveryEvidenceManager()

    class Meta:
        abstract = True
        ordering = ("run_id", "step_id", "map_index")
        rebac_resource_type = "workflows/recovery_evidence"
        constraints = (
            models.UniqueConstraint(fields=("run", "step", "map_index"), name="uniq_wre_run_step_slot"),
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        expected = (
            id(self), self.run_id, self.source_attempt_id, self.step_id, self.map_index
        )
        if not self._state.adding or _recovery_evidence_write_row.get() != expected:
            raise TypeError("Recovery evidence can only be saved by WorkflowRunManager.")
        _recovery_evidence_write_row.set(None)
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise TypeError("Recovery evidence is immutable admission data.")


class StepRun(AuditMixin, AngeeDataModel):
    """Journal row for one workflow step execution or system-injected event."""

    runtime = True

    sqid_prefix = "wsr_"
    run = models.ForeignKey("workflows.WorkflowRun", on_delete=models.CASCADE, related_name="step_runs")
    step = models.ForeignKey(
        "workflows.Step",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="step_runs",
    )
    system_kind = models.SlugField(max_length=100, blank=True, default="")
    map_index = models.IntegerField(default=-1)
    status = StateField(choices_enum=StepRunStatus, default=StepRunStatus.SCHEDULED)
    previous = models.ManyToManyField("self", symmetrical=False, blank=True, related_name="next_step_runs")
    input = models.JSONField(default=dict, null=True, blank=True)
    output = models.JSONField(default=dict, null=True, blank=True)
    output_present = models.BooleanField(default=False)
    resume_state = models.JSONField(default=dict, blank=True)
    claimed_deliveries = models.PositiveBigIntegerField(default=0)
    outcome = models.SlugField(max_length=100, blank=True, default="")
    attempt = models.PositiveIntegerField(default=0)
    effect_key = models.UUIDField(null=True, blank=True, editable=False)
    effect_generation = models.PositiveIntegerField(default=0, editable=False)
    current_attempt = models.OneToOneField(
        "workflows.StepAttempt",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="current_for_step_run",
        editable=False,
    )
    current_map_expansion = models.ForeignKey(
        "workflows.StepAttempt",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="current_map_members",
        editable=False,
    )
    wait_until = models.DateTimeField(null=True, blank=True, db_index=True)
    waiting_kind = StateField(choices_enum=WaitingKind, blank=True, default="")
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
    stacktrace = models.TextField(blank=True)

    def change_related_records(self) -> tuple[ChangeRelatedRecord, ...]:
        """Invalidate exact reads of this execution's parent workflow run."""

        run_model = self._meta.get_field("run").related_model
        return (ChangeRelatedRecord(run_model._meta.label, run_model.public_id_from_pk(self.run_id)),)

    status_transitions = StateTransitions(
        status,
        {
            StepRunStatus.SCHEDULED: [
                StepRunStatus.STARTED,
                StepRunStatus.CANCELED,
                StepRunStatus.SKIPPED,
            ],
            StepRunStatus.STARTED: [
                StepRunStatus.WAITING,
                StepRunStatus.SUCCEEDED,
                StepRunStatus.FAILED,
                StepRunStatus.CANCELED,
            ],
            StepRunStatus.WAITING: [
                StepRunStatus.STARTED,
                StepRunStatus.SUCCEEDED,
                StepRunStatus.FAILED,
                StepRunStatus.CANCELED,
                StepRunStatus.SKIPPED,
            ],
            StepRunStatus.SUCCEEDED: [StepRunStatus.SCHEDULED],
            StepRunStatus.FAILED: [StepRunStatus.SCHEDULED],
            StepRunStatus.CANCELED: [StepRunStatus.SCHEDULED],
            StepRunStatus.SKIPPED: [StepRunStatus.SCHEDULED],
        },
    )

    objects = StepRunManager()

    class Meta:
        """Django model options for workflow step-run journal rows."""

        abstract = True
        ordering = ("created_at", "sqid")
        rebac_resource_type = "workflows/step_run"
        constraints = (
            models.UniqueConstraint(fields=("run", "step", "map_index"), name="uniq_workflows_step_run_map"),
            models.CheckConstraint(
                condition=(
                    models.Q(current_map_expansion__isnull=True)
                    | (models.Q(map_index__gte=0) & models.Q(step__isnull=False))
                ),
                name="chk_wsr_map_membership_body",
            ),
        )

    @property
    def is_terminal(self) -> bool:
        """Return whether this journal row has reached a terminal status."""

        return self.status in StepRunStatus.TERMINAL

    @property
    def is_retained(self) -> bool:
        """Return whether this slot has crossed the permanent retained boundary."""

        return (
            self.effect_key is not None
            or self.current_attempt_id is not None
            or self.current_map_expansion_id is not None
            or self.attempts.exists()
        )

    @property
    def decision_gate(self) -> DecisionGate:
        """Return the single parsed decision policy for this suspension."""

        return DecisionGate.from_resume_state(self.resume_state)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Keep attempt-owned facts immutable outside the attempt manager."""

        alias = kwargs.get("using") or self._state.db or router.db_for_write(type(self), instance=self)
        protected = {
            "run", "run_id", "step", "step_id", "map_index",
            "status", "input", "output", "output_present", "resume_state", "claimed_deliveries", "outcome",
            "attempt", "current_attempt", "current_attempt_id", "current_map_expansion",
            "current_map_expansion_id", "effect_key", "effect_generation",
            "wait_until", "waiting_kind", "heartbeat_at", "error", "stacktrace",
        }
        if self._state.adding:
            invalid = (
                self.attempt != 0
                or self.effect_key is not None
                or self.effect_generation != 0
                or self.current_attempt_id is not None
                or self.current_map_expansion_id is not None
            )
            if invalid:
                raise ValidationError({"effect_key": "Attempt identity is initialized by StepAttemptManager."})
        elif not self._state.adding:
            loaded = system_queryset(type(self), using=alias, lock=None).filter(pk=self.pk).values(
                "run_id",
                "step_id",
                "map_index",
                "attempt",
                "current_attempt_id",
                "current_map_expansion_id",
                "effect_key",
                "effect_generation",
            ).get()
            update_fields = kwargs.get("update_fields")
            touches_projection = update_fields is None or bool(protected.intersection(update_fields))
            attempt_model = self._meta.get_field("current_attempt").remote_field.model
            retained = (
                loaded["current_attempt_id"] is not None
                or loaded["current_map_expansion_id"] is not None
                or loaded["effect_key"] is not None
                or system_queryset(attempt_model, using=alias, lock=None).filter(step_run_id=self.pk).exists()
            )
            changed_ancestry = {
                name
                for name in ("run_id", "step_id", "map_index")
                if loaded[name] != getattr(self, name)
            }
            if retained and changed_ancestry:
                raise ValidationError(
                    {name: "Retained StepRun ancestry is immutable." for name in changed_ancestry}
                )
            if (retained or _attempt_write_active(alias, self.pk)) and touches_projection:
                capability = _step_run_save_capability.get()
                if (
                    capability is None
                    or capability.run_id != loaded["run_id"]
                    or capability.step_run_id != self.pk
                    or not capability.atomic.consume(alias, self)
                ):
                    raise TypeError("Retained StepRun projections can only be saved by their manager owner.")
            if _attempt_write_active(alias, self.pk):
                super().save(*args, **kwargs)
                return
            changed = {
                name
                for name in ("current_attempt_id", "effect_key", "effect_generation")
                if loaded[name] != getattr(self, name)
            }
            if loaded["attempt"] != self.attempt and (
                loaded["current_attempt_id"] is not None or loaded["effect_key"] is not None
            ):
                changed.add("attempt")
            if changed:
                raise ValidationError({name: "This field is owned by StepAttemptManager." for name in changed})
        super().save(*args, **kwargs)

    @transition(
        status,
        source=[StepRunStatus.SCHEDULED, StepRunStatus.WAITING],
        target=StepRunStatus.STARTED,
        on_success=save_state,
    )
    def mark_started(self, *, heartbeat_at: Any = None, claimed_deliveries: int = 0) -> None:
        """Claim this row for execution."""

        self.heartbeat_at = heartbeat_at
        self.claimed_deliveries = claimed_deliveries
        self.waiting_kind = cast(WaitingKind, "")
        self._transition_fields = {"heartbeat_at", "claimed_deliveries", "waiting_kind"}

    @transition(status, source=StepRunStatus.STARTED, target=StepRunStatus.WAITING, on_success=save_state)
    def mark_waiting(
        self,
        *,
        until: Any = None,
        resume_state: dict[str, Any] | None = None,
        waiting_kind: WaitingKind = cast(WaitingKind, WaitingKind.SCHEDULED),
    ) -> None:
        """Persist durable wait conditions for this row."""

        self.wait_until = until
        self.waiting_kind = waiting_kind
        if resume_state is not None:
            self.resume_state = resume_state
        self._transition_fields = {"wait_until", "resume_state", "waiting_kind"}

    def wake(self, *, at: datetime) -> None:
        """Make this waiting journal row due without changing its state.

        Event delivery changes only the durable due time. The engine owns the
        later ``WAITING`` → ``STARTED`` claim and therefore remains the sole
        scheduler of implementation work.
        """

        if self.status != StepRunStatus.WAITING:
            raise TransitionNotAllowed(
                f"StepRun.wake requires status={StepRunStatus.WAITING}; found {self.status}."
            )
        self.wait_until = at
        self.save(update_fields=["wait_until", "resume_state", "updated_at"])

    @transition(
        status,
        source=[StepRunStatus.STARTED, StepRunStatus.WAITING],
        target=StepRunStatus.SUCCEEDED,
        on_success=save_state,
    )
    def mark_succeeded(self, *, output: Any = None, output_present: bool = True, outcome: str = "") -> None:
        """Persist a successful step result."""

        self.output = output if output_present else None
        self.output_present = output_present
        self.outcome = outcome
        self.error = ""
        self.stacktrace = ""
        self.wait_until = None
        self.waiting_kind = cast(WaitingKind, "")
        self._transition_fields = {
            "output", "output_present", "outcome", "error", "stacktrace", "wait_until", "waiting_kind",
        }

    @transition(
        status,
        source=[StepRunStatus.STARTED, StepRunStatus.WAITING],
        target=StepRunStatus.FAILED,
        on_success=save_state,
    )
    def mark_failed(self, *, error: str = "", stacktrace: str = "", outcome: str = "failed") -> None:
        """Persist a failed step result."""

        self.error = error
        self.stacktrace = stacktrace
        self.outcome = outcome
        self.wait_until = None
        self.waiting_kind = cast(WaitingKind, "")
        self._transition_fields = {"error", "stacktrace", "outcome", "wait_until", "waiting_kind"}

    @transition(
        status,
        source=[StepRunStatus.SCHEDULED, StepRunStatus.WAITING],
        target=StepRunStatus.SKIPPED,
        on_success=save_state,
    )
    def mark_skipped(self) -> None:
        """Mark this row as skipped by routing or join semantics."""

        self.wait_until = None
        self.waiting_kind = cast(WaitingKind, "")
        self._transition_fields = {"wait_until", "waiting_kind", "resume_state"}

    @transition(
        status,
        source=[StepRunStatus.SCHEDULED, StepRunStatus.STARTED, StepRunStatus.WAITING],
        target=StepRunStatus.CANCELED,
        on_success=save_state,
    )
    def mark_canceled(self) -> None:
        """Mark this row as canceled."""

        self.wait_until = None
        self.waiting_kind = cast(WaitingKind, "")
        self._transition_fields = {"wait_until", "waiting_kind", "resume_state"}

    @transition(
        status,
        source=[StepRunStatus.SUCCEEDED, StepRunStatus.FAILED, StepRunStatus.CANCELED, StepRunStatus.SKIPPED],
        target=StepRunStatus.SCHEDULED,
        on_success=save_state,
    )
    def reschedule_for_override(self, *, input: Any = None) -> None:
        """Reset a terminal journal row so a manual override can run it again."""

        self.input = input if input is not None else {}
        self.output = {}
        self.resume_state = {}
        self.claimed_deliveries = 0
        self.outcome = ""
        self.wait_until = None
        self.waiting_kind = cast(WaitingKind, "")
        self.heartbeat_at = None
        self.error = ""
        self.stacktrace = ""
        self._transition_fields = {
            "input",
            "output",
            "resume_state",
            "claimed_deliveries",
            "outcome",
            "wait_until",
            "waiting_kind",
            "heartbeat_at",
            "error",
            "stacktrace",
        }


class StepAttempt(AuditMixin, AngeeDataModel):
    """Append-only evidence for one physical execution attempt."""

    runtime = True

    sqid_prefix = "wsa_"
    step_run = models.ForeignKey("workflows.StepRun", on_delete=models.PROTECT, related_name="attempts")

    def change_related_records(self) -> tuple[ChangeRelatedRecord, ...]:
        """Invalidate exact reads of this attempt's execution journal row."""

        step_run_model = self._meta.get_field("step_run").related_model
        return (ChangeRelatedRecord(
            step_run_model._meta.label,
            step_run_model.public_id_from_pk(self.step_run_id),
        ),)
    retry_of = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="retry_successor",
        editable=False,
    )
    retry_index = models.PositiveIntegerField(default=0, editable=False)
    available_at = models.DateTimeField(null=True, blank=True, db_index=True, editable=False)
    ordinal = models.PositiveIntegerField()
    cause = models.CharField(max_length=32, choices=[(value.value, value.name.title()) for value in AttemptCause])
    lease_token = models.UUIDField(editable=False)
    effect_key = models.UUIDField(editable=False)
    effect_generation = models.PositiveIntegerField(default=0, editable=False)
    input_present = models.BooleanField(default=False)
    input = models.JSONField(null=True, blank=True)
    input_provenance = models.JSONField(default=dict, blank=True)
    map_expansion = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="map_item_attempts",
        editable=False,
    )
    map_item_index = models.PositiveIntegerField(null=True, blank=True, editable=False)
    map_item_present = models.BooleanField(default=False, editable=False)
    map_item = models.JSONField(null=True, blank=True, editable=False)
    test_fixture = models.ForeignKey(
        "workflows.WorkflowTestFixture",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="applied_attempts",
        editable=False,
    )
    recovery_source_attempt = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True,
        related_name="recovery_attempts", editable=False,
    )
    recovery_mode = models.CharField(max_length=32, blank=True, editable=False)
    intended_effect_key = models.UUIDField(null=True, blank=True, editable=False)
    claimed_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    external_content_type = models.ForeignKey(
        ContentType, on_delete=models.PROTECT, null=True, blank=True, related_name="+", editable=False,
    )
    external_object_id = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    lease_revoked_at = models.DateTimeField(null=True, blank=True)
    lease_revocation_reason = models.CharField(
        max_length=32,
        blank=True,
        choices=[(value.value, value.name.title()) for value in LeaseRevocationReason],
    )
    result_kind = models.CharField(
        max_length=32,
        blank=True,
        choices=[(value.value, value.name.title()) for value in AttemptResultKind],
    )
    result_recorded_at = models.DateTimeField(null=True, blank=True)
    output_present = models.BooleanField(default=False)
    output = models.JSONField(null=True, blank=True)
    checkpoint_present = models.BooleanField(default=False)
    checkpoint = models.JSONField(null=True, blank=True)
    error = models.TextField(null=True, blank=True)
    stacktrace = models.TextField(null=True, blank=True)
    outcome = models.SlugField(max_length=100, blank=True, default="")
    waiting_kind = models.CharField(max_length=32, blank=True, default="")
    result_requested_until = models.DateTimeField(null=True, blank=True)
    result_decisions = models.JSONField(default=list, blank=True)
    decision_settlement = models.JSONField(default=dict, blank=True, editable=False)
    artifacts_present = models.BooleanField(default=False, editable=False)
    orchestration_error = models.TextField(blank=True, default="", editable=False)
    applied_at = models.DateTimeField(null=True, blank=True)

    objects = StepAttemptManager()
    system_objects = StepAttemptSystemManager()

    class Meta:
        abstract = True
        base_manager_name = "system_objects"
        ordering = ("step_run_id", "ordinal")
        rebac_resource_type = "workflows/step_attempt"
        constraints = (
            models.UniqueConstraint(fields=("step_run", "ordinal"), name="uniq_workflows_step_attempt_ordinal"),
            models.UniqueConstraint(fields=("lease_token",), name="uniq_workflows_step_attempt_lease"),
            models.CheckConstraint(
                condition=models.Q(input_present=True) | models.Q(input__isnull=True),
                name="chk_wsa_absent_input_null",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(external_content_type__isnull=True, external_object_id__isnull=True)
                    | models.Q(external_content_type__isnull=False, external_object_id__isnull=False)
                ),
                name="chk_wsa_external_target_pair",
            ),
            models.CheckConstraint(
                condition=models.Q(output_present=True) | models.Q(output__isnull=True),
                name="chk_wsa_absent_output_null",
            ),
            models.CheckConstraint(
                condition=models.Q(checkpoint_present=True) | models.Q(checkpoint__isnull=True),
                name="chk_wsa_absent_checkpoint_null",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(lease_revoked_at__isnull=True, lease_revocation_reason="")
                    | (models.Q(lease_revoked_at__isnull=False) & ~models.Q(lease_revocation_reason=""))
                ),
                name="chk_wsa_revocation_pair",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(result_recorded_at__isnull=True, result_kind="")
                    | (models.Q(result_recorded_at__isnull=False) & ~models.Q(result_kind=""))
                ),
                name="chk_wsa_result_pair",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(applied_at__isnull=True)
                    | (models.Q(result_recorded_at__isnull=False) & models.Q(lease_revoked_at__isnull=True))
                ),
                name="chk_wsa_applied_result",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        cause=AttemptCause.AUTOMATIC_RETRY,
                        retry_of__isnull=False,
                        retry_index__gt=0,
                        available_at__isnull=False,
                    )
                    | (
                        ~models.Q(cause=AttemptCause.AUTOMATIC_RETRY)
                        & models.Q(retry_of__isnull=True, retry_index=0, available_at__isnull=True)
                    )
                ),
                name="chk_wsa_retry_series",
            ),
            models.CheckConstraint(
                condition=models.Q(orchestration_error="") | models.Q(result_kind=AttemptResultKind.TRANSIENT_ERROR),
                name="chk_wsa_orchestration_error",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        map_expansion__isnull=True,
                        map_item_index__isnull=True,
                        map_item_present=False,
                        map_item__isnull=True,
                    )
                    | models.Q(
                        map_expansion__isnull=False,
                        map_item_index__isnull=False,
                        map_item_present=True,
                    )
                ),
                name="chk_wsa_map_item_source",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(cause=AttemptCause.TEST_FIXTURE, test_fixture__isnull=False)
                    | ~models.Q(cause=AttemptCause.TEST_FIXTURE)
                ),
                name="chk_wsa_test_fixture_cause",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        cause=AttemptCause.MANUAL_RETRY,
                        recovery_source_attempt__isnull=False,
                        recovery_mode__gt="",
                        intended_effect_key__isnull=False,
                    )
                    | (
                        ~models.Q(cause=AttemptCause.MANUAL_RETRY)
                        & models.Q(
                            recovery_source_attempt__isnull=True,
                            recovery_mode="",
                            intended_effect_key__isnull=True,
                        )
                    )
                ),
                name="chk_wsa_recovery_attempt",
            ),
        )

    @property
    def status(self) -> AttemptStatus:
        """Derive lifecycle display from retained lease and result evidence."""

        if self.result_recorded_at is not None:
            return AttemptStatus.COMPLETED if self.applied_at is not None else AttemptStatus.LATE_RESULT
        if self.lease_revoked_at is not None:
            return AttemptStatus.REVOKED
        if self.started_at is not None:
            return AttemptStatus.RUNNING
        if self.claimed_at is not None:
            return AttemptStatus.CLAIMED
        return AttemptStatus.ALLOCATED

    def save(self, *args: Any, **kwargs: Any) -> None:
        alias = kwargs.get("using") or self._state.db or router.db_for_write(type(self), instance=self)
        capability = _attempt_save_capability.get()
        if (
            capability is None
            or not _attempt_write_active(alias, self.step_run_id)
            or capability.step_run_id != self.step_run_id
            or capability.pk != self.pk
            or capability.adding != self._state.adding
            or not capability.atomic.consume(alias, self)
        ):
            raise TypeError("Step attempts can only be saved by StepAttemptManager.")
        if not self._state.adding:
            retained = system_queryset(type(self), using=alias, lock=("self",)).filter(
                pk=self.pk
            ).values_list("decision_settlement", flat=True).get()
            if retained != self.decision_settlement:
                session = _decision_resolution_session.get()
                if (
                    retained
                    or not isinstance(self.decision_settlement, dict)
                    or set(self.decision_settlement) != {"decision_ids", "outcome"}
                    or not isinstance(self.decision_settlement["decision_ids"], list)
                    or not self.decision_settlement["decision_ids"]
                    or not isinstance(self.decision_settlement["outcome"], str)
                    or self.result_kind != str(AttemptResultKind.SUSPEND)
                    or self.applied_at is None
                    or session is None
                    or session.completed
                    or session.alias != alias
                    or session.decision_id not in self.decision_settlement["decision_ids"]
                ):
                    raise TypeError("Decision settlement is a write-once retained suspension fact.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise TypeError("Step attempts are retained execution evidence and cannot be deleted.")


class StepExternalSubscription(AuditMixin, AngeeDataModel):
    """One exact domain record observed by an attempt's external wait predicate."""

    runtime = True
    sqid_prefix = "wes_"
    attempt = models.ForeignKey(
        "workflows.StepAttempt",
        on_delete=models.CASCADE,
        related_name="external_subscriptions",
        editable=False,
    )
    target_content_type = models.ForeignKey(
        ContentType, on_delete=models.PROTECT, related_name="+", editable=False,
    )
    target_object_id = models.PositiveBigIntegerField(editable=False)
    target = GenericForeignKey("target_content_type", "target_object_id")

    objects = StepExternalSubscriptionManager()

    class Meta:
        abstract = True
        ordering = ("attempt_id", "target_content_type_id", "target_object_id")
        indexes = (
            models.Index(
                fields=("target_content_type", "target_object_id"),
                name="idx_wes_target",
            ),
        )
        constraints = (
            models.UniqueConstraint(
                fields=("attempt", "target_content_type", "target_object_id"),
                name="uniq_wes_attempt_target",
            ),
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        alias = kwargs.get("using") or self._state.db or router.db_for_write(type(self), instance=self)
        if not self._state.adding or not _attempt_write_active(alias, self.attempt.step_run_id):
            raise TypeError("External subscriptions can only be recorded by StepAttemptManager.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise TypeError("External subscriptions are retained for their attempt lifecycle.")


class StepArtifact(AuditMixin, AngeeDataModel):
    """Immutable ordered pointer explicitly emitted by one retained attempt result."""

    runtime = True
    sqid_prefix = "war_"
    attempt = models.ForeignKey("workflows.StepAttempt", on_delete=models.PROTECT, related_name="artifacts")
    declaration_index = models.PositiveIntegerField(editable=False)
    label = models.CharField(max_length=255, editable=False)
    target_content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, related_name="+")
    target_object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey("target_content_type", "target_object_id")

    objects = StepArtifactManager()

    class Meta:
        abstract = True
        ordering = ("attempt_id", "declaration_index")
        rebac_resource_type = "workflows/step_artifact"
        indexes = (
            models.Index(fields=("target_content_type", "target_object_id"), name="idx_war_target"),
        )
        constraints = (
            models.UniqueConstraint(fields=("attempt", "declaration_index"), name="uniq_war_attempt_index"),
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        alias = kwargs.get("using") or self._state.db or router.db_for_write(type(self), instance=self)
        capability = _artifact_write_capability.get()
        if (
            capability is None
            or capability.attempt_id != self.attempt_id
            or capability.declaration_index != self.declaration_index
            or not self._state.adding
            or not capability.atomic.consume(alias, self)
        ):
            raise TypeError("Workflow artifacts can only be saved by StepArtifactManager.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise TypeError("Workflow artifacts are immutable retained result evidence.")


class Decision(AuditMixin, AngeeDataModel):
    """One awaited resolution slot for a suspended step-run."""

    runtime = True
    rebac_grantable = {"reader": "share", "pending_decision": "share"}
    _form_schema_state_attribute = "_workflows_form_schema_state"

    sqid_prefix = "wdc_"
    step_run = models.ForeignKey("workflows.StepRun", on_delete=models.CASCADE, related_name="decisions")
    suspension_attempt = models.ForeignKey(
        "workflows.StepAttempt", on_delete=models.PROTECT, null=True, blank=True, related_name="decisions"
    )
    declaration_index = models.PositiveIntegerField(null=True, blank=True, editable=False)
    priority = models.IntegerField(default=0)
    action = models.SlugField(max_length=100)
    payload = models.JSONField(default=dict, blank=True)
    target_model = models.CharField(max_length=255, blank=True, default="", db_index=True)
    target_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    target_tab = models.CharField(max_length=100, blank=True, default="")
    target_authority_decision = models.ForeignKey(
        "workflows.Decision", on_delete=models.PROTECT, null=True, blank=True,
        related_name="authorized_target_decisions", editable=False,
    )
    record_access = models.JSONField(default=list, blank=True, editable=False)
    verdict = StateField(choices_enum=Verdict, default=Verdict.PENDING)
    resolution = models.JSONField(default=dict, blank=True)
    resolved_by = models.CharField(max_length=255, blank=True, default="")
    resolved_at = models.DateTimeField(null=True, blank=True, editable=False)
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    escalate_at = models.DateTimeField(null=True, blank=True, db_index=True)

    verdict_transitions = StateTransitions(
        verdict,
        {
            Verdict.PENDING: [
                Verdict.COMPLETED,
                Verdict.REJECTED,
                Verdict.ESCALATED,
                Verdict.EXPIRED,
            ],
        },
    )

    objects = DecisionManager()

    class Meta:
        """Django model options for workflow decisions."""

        abstract = True
        base_manager_name = "objects"
        ordering = ("step_run", "priority", "declaration_index", "created_at", "sqid")
        rebac_resource_type = "workflows/decision"
        indexes = (
            models.Index(fields=("step_run", "verdict", "priority"), name="idx_wdc_step_verdict"),
            models.Index(fields=("target_model", "target_id"), name="idx_wdc_target"),
        )
        constraints = (
            models.UniqueConstraint(
                fields=("suspension_attempt", "declaration_index"), name="uniq_wdc_attempt_declaration"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(suspension_attempt__isnull=True, declaration_index__isnull=True)
                    | models.Q(suspension_attempt__isnull=False, declaration_index__isnull=False)
                ),
                name="chk_wdc_declaration_source_pair",
            ),
            models.CheckConstraint(
                condition=(models.Q(target_model="", target_id="", target_tab="")
                           | (~models.Q(target_model="") & ~models.Q(target_id=""))),
                name="chk_wdc_target_pair",
            ),
        )

    @property
    def is_terminal(self) -> bool:
        """Return whether this decision has a terminal verdict."""

        return self.verdict in Verdict.TERMINAL

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Keep retained suspension provenance immutable outside its manager."""

        alias = kwargs.get("using") or self._state.db or router.db_for_write(type(self), instance=self)
        if self._state.adding:
            if (
                (self.suspension_attempt_id is not None or self.declaration_index is not None)
                and not _decision_write_active(alias, self)
            ):
                raise TypeError("Decision suspension provenance is owned by DecisionManager.")
        else:
            retained = system_queryset(type(self), using=alias, lock=None).filter(pk=self.pk).values(
                "suspension_attempt_id", "declaration_index", "priority", "action", "payload",
                "target_model", "target_id", "target_tab", "target_authority_decision_id",
                "record_access", "max_attempts", "expires_at", "escalate_at",
            ).get()
            if retained["suspension_attempt_id"] != self.suspension_attempt_id or retained[
                "declaration_index"
            ] != self.declaration_index:
                raise TypeError("Decision suspension provenance is immutable.")
            immutable = {
                name
                for name in (
                    "priority", "action", "payload", "target_model", "target_id", "target_tab",
                    "target_authority_decision_id", "record_access",
                    "max_attempts", "expires_at", "escalate_at"
                )
                if retained[name] != getattr(self, name)
            }
            if retained["suspension_attempt_id"] is not None and immutable:
                raise TypeError("Retained Decision declarations are immutable.")
            update_fields = kwargs.get("update_fields")
            execution_fields = {"verdict", "resolution", "resolved_by", "resolved_at", "attempts"}
            touches_execution = update_fields is None or bool(execution_fields.intersection(update_fields))
            if retained["suspension_attempt_id"] is not None and touches_execution:
                capability = _decision_save_capability.get()
                if (
                    capability is None
                    or capability.decision_id != self.pk
                    or not capability.atomic.consume(alias, self)
                ):
                    raise TypeError("Retained Decisions can only be changed by their transition owner.")
        super().save(*args, **kwargs)

    @classmethod
    def form_schema_annotation(cls) -> dict[str, Any]:
        """Return the narrow ORM projection consumed by :attr:`form_schema`."""

        return {cls._form_schema_state_attribute: models.F("step_run__resume_state")}

    @property
    def form_schema(self) -> dict[str, Any] | None:
        """Return the enforced JSON-authored form schema, excluding Python model schemas."""

        state = getattr(self, self._form_schema_state_attribute, None)
        if not isinstance(state, dict):
            state = self.step_run.resume_state
        if not isinstance(state, dict):
            return None
        schemas = state.get("_decision_schemas", {})
        if isinstance(schemas, dict) and str(self.pk) in schemas:
            schema = schemas[str(self.pk)]
            return dict(schema) if isinstance(schema, dict) and schema else None
        gate = state.get("gate")
        if isinstance(gate, dict):
            schema = gate.get("decision_schema")
            return dict(schema) if isinstance(schema, dict) and schema else None
        return None

    @transition(verdict, source=Verdict.PENDING, target=Verdict.COMPLETED, on_success=save_state)
    def mark_completed(self, *, resolution: Any = None, resolved_by: str = "") -> None:
        """Resolve this slot as completed."""

        self._set_resolution(resolution=resolution, resolved_by=resolved_by)

    @transition(verdict, source=Verdict.PENDING, target=Verdict.REJECTED, on_success=save_state)
    def mark_rejected(self, *, resolution: Any = None, resolved_by: str = "") -> None:
        """Resolve this slot as rejected."""

        self._set_resolution(resolution=resolution, resolved_by=resolved_by)

    @transition(verdict, source=Verdict.PENDING, target=Verdict.ESCALATED, on_success=save_state)
    def mark_escalated(self, *, resolution: Any = None, resolved_by: str = "") -> None:
        """Resolve this slot as escalated."""

        self._set_resolution(resolution=resolution, resolved_by=resolved_by)

    @transition(verdict, source=Verdict.PENDING, target=Verdict.EXPIRED, on_success=save_state)
    def mark_expired(self, *, resolution: Any = None, resolved_by: str = "") -> None:
        """Resolve this slot as expired."""

        self._set_resolution(resolution=resolution, resolved_by=resolved_by)

    def record_invalid_resolution(self) -> None:
        """Record one failed validation attempt while leaving the slot pending."""

        self.attempts += 1
        self.save(update_fields=["attempts", "updated_at"])

    def resolve(self, verdict: Verdict, *, resolution: Any = None, resolved_by: str = "") -> None:
        """Resolve this slot through the transition matching ``verdict``."""

        if verdict == Verdict.COMPLETED:
            self.mark_completed(resolution=resolution, resolved_by=resolved_by)
        elif verdict == Verdict.REJECTED:
            self.mark_rejected(resolution=resolution, resolved_by=resolved_by)
        elif verdict == Verdict.ESCALATED:
            self.mark_escalated(resolution=resolution, resolved_by=resolved_by)
        elif verdict == Verdict.EXPIRED:
            self.mark_expired(resolution=resolution, resolved_by=resolved_by)
        else:
            raise ValidationError({"verdict": "Decision verdict must be terminal."})

    def _set_resolution(self, *, resolution: Any = None, resolved_by: str = "") -> None:
        """Persist normalized resolution audit fields for a terminal verdict."""

        self.resolution = resolution if resolution is not None else {}
        self.resolved_by = resolved_by
        self.resolved_at = timezone.now()
        self._transition_fields = {"resolution", "resolved_by", "resolved_at"}


class WorkflowDispatch(AuditMixin, AngeeDataModel):
    """One durable, resendable workflow delivery intent."""

    runtime = True

    sqid_prefix = "wfd_"
    kind = models.CharField(
        max_length=32,
        choices=[(value.value, value.name.title()) for value in WorkflowDispatchKind],
    )
    run = models.ForeignKey(
        "workflows.WorkflowRun", on_delete=models.PROTECT, null=True, blank=True, related_name="dispatches"
    )
    step_attempt = models.ForeignKey(
        "workflows.StepAttempt", on_delete=models.PROTECT, null=True, blank=True, related_name="dispatches"
    )
    decision = models.ForeignKey(
        "workflows.Decision", on_delete=models.PROTECT, null=True, blank=True, related_name="dispatches"
    )
    generation = models.PositiveIntegerField(null=True, blank=True, editable=False)
    artifact_content_type = models.ForeignKey(
        ContentType, on_delete=models.PROTECT, null=True, blank=True, related_name="+", editable=False,
    )
    artifact_object_id = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    available_at = models.DateTimeField(db_index=True, editable=False)
    next_send_at = models.DateTimeField(db_index=True, editable=False)
    consumed_at = models.DateTimeField(null=True, blank=True, editable=False)
    send_count = models.PositiveIntegerField(default=0, editable=False)
    last_sent_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_send_error = models.CharField(max_length=64, blank=True, default="", editable=False)

    objects = WorkflowDispatchManager()

    class Meta:
        abstract = True
        base_manager_name = "objects"
        ordering = ("available_at", "pk")
        constraints = (
            models.CheckConstraint(
                condition=(
                    models.Q(kind=WorkflowDispatchKind.ADVANCE, run__isnull=False, step_attempt__isnull=True,
                             decision__isnull=True, generation__isnull=True,
                             artifact_content_type__isnull=True, artifact_object_id__isnull=True)
                    | models.Q(kind=WorkflowDispatchKind.EXECUTE, run__isnull=True, step_attempt__isnull=False,
                               decision__isnull=True, generation__isnull=True,
                               artifact_content_type__isnull=True, artifact_object_id__isnull=True)
                    | models.Q(kind__in=[WorkflowDispatchKind.DECISION_EXPIRE,
                                         WorkflowDispatchKind.DECISION_ESCALATE], run__isnull=True,
                               step_attempt__isnull=True, decision__isnull=False, generation__isnull=False,
                               artifact_content_type__isnull=True, artifact_object_id__isnull=True)
                    | models.Q(kind=WorkflowDispatchKind.ARTIFACT_DELIVERY, run__isnull=True,
                               step_attempt__isnull=True, decision__isnull=True, generation__isnull=True,
                               artifact_content_type__isnull=False, artifact_object_id__isnull=False)
                    | models.Q(kind=WorkflowDispatchKind.CHILD_CANCEL, run__isnull=False,
                               step_attempt__isnull=True, decision__isnull=True, generation__isnull=True,
                               artifact_content_type__isnull=True, artifact_object_id__isnull=True)
                    | models.Q(kind=WorkflowDispatchKind.RUN_CANCEL, run__isnull=False,
                               step_attempt__isnull=True, decision__isnull=True, generation__isnull=True,
                               artifact_content_type__isnull=True, artifact_object_id__isnull=True)
                ),
                name="chk_wfd_target_shape",
            ),
            models.UniqueConstraint(
                fields=("step_attempt",),
                condition=models.Q(kind=WorkflowDispatchKind.EXECUTE),
                name="uniq_wfd_execute_attempt",
            ),
            models.UniqueConstraint(
                fields=("kind", "decision", "generation"),
                condition=models.Q(kind__in=[WorkflowDispatchKind.DECISION_EXPIRE,
                                             WorkflowDispatchKind.DECISION_ESCALATE]),
                name="uniq_wfd_decision_timer",
            ),
            models.UniqueConstraint(
                fields=("kind", "run"), condition=models.Q(kind=WorkflowDispatchKind.CHILD_CANCEL),
                name="uniq_wfd_child_cancel",
            ),
            models.UniqueConstraint(
                fields=("kind", "run"), condition=models.Q(kind=WorkflowDispatchKind.RUN_CANCEL),
                name="uniq_wfd_run_cancel",
            ),
        )

    @property
    def target_identity(self) -> tuple[int | None, ...]:
        """Return immutable target fields used by the exact-row write guard."""

        return (
            self.run_id, self.step_attempt_id, self.decision_id, self.generation,
            self.artifact_content_type_id, self.artifact_object_id,
        )

    @property
    def envelope(self) -> WorkflowDispatchEnvelope:
        """Return the identifier-only transport message for this validated row."""

        kind = WorkflowDispatchKind(self.kind)
        target_id = (
            self.pk if kind == WorkflowDispatchKind.ARTIFACT_DELIVERY else
            self.run_id if kind in {
                WorkflowDispatchKind.ADVANCE,
                WorkflowDispatchKind.CHILD_CANCEL,
                WorkflowDispatchKind.RUN_CANCEL,
            } else
            self.step_attempt_id if kind == WorkflowDispatchKind.EXECUTE else self.decision_id
        )
        if target_id is None:
            raise ValueError("Workflow dispatch target does not match its kind.")
        lease_token = self.step_attempt.lease_token if kind == WorkflowDispatchKind.EXECUTE else None
        return WorkflowDispatchEnvelope(self.pk, kind, target_id, self.generation, lease_token)

    def save(self, *args: Any, **kwargs: Any) -> None:
        alias = kwargs.get("using") or self._state.db or router.db_for_write(type(self), instance=self)
        capability = _dispatch_save_capability.get()
        if (
            capability is None
            or capability.pk != self.pk
            or capability.adding != self._state.adding
            or capability.kind != str(self.kind)
            or capability.target != self.target_identity
            or not capability.atomic.consume(alias, self)
        ):
            raise TypeError("Workflow dispatches can only be saved by WorkflowDispatchManager.")
        if not self._state.adding:
            persisted = system_queryset(type(self), using=alias, lock=None).values(
                "kind", "run_id", "step_attempt_id", "decision_id", "generation",
                "artifact_content_type_id", "artifact_object_id", "available_at"
            ).get(pk=self.pk)
            if (
                persisted["kind"] != self.kind
                or persisted["run_id"] != self.run_id
                or persisted["step_attempt_id"] != self.step_attempt_id
                or persisted["decision_id"] != self.decision_id
                or persisted["generation"] != self.generation
                or persisted["artifact_content_type_id"] != self.artifact_content_type_id
                or persisted["artifact_object_id"] != self.artifact_object_id
                or persisted["available_at"] != self.available_at
            ):
                raise TypeError("Workflow dispatch identity and availability are immutable.")
        if self._state.adding and self.next_send_at is None:
            self.next_send_at = self.available_at
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise TypeError("Workflow dispatches are durable delivery evidence and cannot be deleted.")
