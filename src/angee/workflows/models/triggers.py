"""Native workflow events owners."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any, ClassVar, cast

from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models, transaction
from django.db.models.expressions import BaseExpression
from django.db.models.functions import Now
from django_compose.models import Composable
from rebac import to_subject_ref
from rebac.models import active_relationship_model

from angee.base.fields.implementation import ImplClassField
from angee.base.fields.text import DiagnosticTextField, ModelLabelField
from angee.base.models.base import AngeeDataModel
from angee.base.models.merge_references import MergePolicy
from angee.base.models.refs import RecordRefMixin
from angee.graphql.mixins import ImplementationChoicesMixin
from angee.graphql.schema import GraphQLSchemas
from angee.resources.mixins import ResourceLoadMixin
from angee.workflows.managers import (
    TriggerEventManager,
    TriggerManager,
)
from angee.workflows.models.definitions import Workflow
from angee.workflows.resources import TriggerResource
from angee.workflows.states import (
    RecordOperation,
)
from angee.workflows.triggers import TriggerSource

logger = logging.getLogger(__name__)


class WorkflowDecision(Composable, extends="decisions.Decision"):
    """Contribute retained requesting runs to the decision owner's reference seam."""

    @staticmethod
    def _requesting_workflow_key_filter(rows: models.QuerySet[Any]) -> BaseExpression:
        """Project the first requesting step's workflow key for retained saved filters."""

        steps = (
            apps.get_model("workflows", "StepRun")
            ._base_manager.using(rows.db)
            .filter(
                decision_id=models.OuterRef("pk"),
            )
            .order_by("pk")
        )
        return models.Subquery(
            steps.values("run__version__workflow__key")[:1],
            output_field=models.CharField(null=True),
        )

    hasura_filter_expressions: ClassVar[Mapping[str, Callable[[models.QuerySet[Any]], BaseExpression]]] = {
        "requesting_workflow_key": _requesting_workflow_key_filter,
    }

    class Meta:
        abstract = True

    @classmethod
    def requester_querysets(cls) -> tuple[models.QuerySet, ...]:
        runs = (
            apps.get_model("workflows", "WorkflowRun")
            .system_queryset()
            .annotate(
                _decision_id=models.F("step_runs__decision_id"),
            )
        )
        retained = (
            apps.get_model("workflows", "WorkflowRun")
            .system_queryset()
            .filter(
                records__content_type__app_label=cls._meta.app_label,
                records__content_type__model=cls._meta.model_name,
                records__operation=RecordOperation.CREATED,
            )
            .annotate(_decision_id=models.F("records__object_id"))
        )
        return (*cast(Any, super()).requester_querysets(), runs, retained)


class Trigger(ImplementationChoicesMixin, ResourceLoadMixin, AngeeDataModel, concrete=True):
    """Disabled-by-default admission policy with source-scoped domain hooks.

    An ``extends`` donor implementing ``trigger_input`` or ``check_admission``
    declares ``trigger_sources`` as registered source keys. Admission runs all
    matching guards and accepts at most one matching input builder.
    """

    resource_class = TriggerResource
    sqid_prefix = "wft_"
    workflow = models.ForeignKey("workflows.Workflow", on_delete=models.CASCADE, related_name="triggers")
    source = ImplClassField(TriggerSource)
    model_label = ModelLabelField(max_length=200, blank=True, default="")
    condition = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(default=False, editable=False)
    granted_targets = models.JSONField(default=list, editable=False)
    """Targets this trigger contributes to the workflow principal's direct tuples."""
    disabled_reason = DiagnosticTextField(blank=True, default="", editable=False)
    objects = TriggerManager()

    @classmethod
    def include_impl_choice(cls, field_name: str, implementation: type[object]) -> bool:
        """Keep observation-only sources out of trigger admission pickers."""

        if field_name == "source" and issubclass(implementation, TriggerSource):
            return not implementation.watch_only
        return super().include_impl_choice(field_name, implementation)

    @classmethod
    def can_read_impl_choices(cls, field_name: str, actor: Any) -> bool:
        """Workflow authors can configure sources without platform administration."""
        if field_name != "source" or actor is None:
            return False
        model = cls._meta.get_field("workflow").related_model
        assert model is not None and issubclass(model, Workflow)
        workflows = model.objects.with_actor(actor).with_action("write")
        return workflows.exists()

    @property
    def source_class(self) -> type[TriggerSource]:
        """Resolve source behavior through its declared implementation field."""
        try:
            field = self._meta.get_field("source")
            assert isinstance(field, ImplClassField)
            return field.resolve_for(self)
        except ImportError as error:
            raise ImproperlyConfigured(f"Trigger source {self.source!r} cannot be loaded: {error}") from error

    @property
    def source_model(self) -> str:
        """Expose the resolved model's canonical label, retaining unavailable-source configuration."""
        try:
            return self.source_class.model(self)._meta.label
        except ImproperlyConfigured:
            return self.model_label

    def granted_relationships(self, *, actor: Any) -> tuple[Any, ...]:
        """List the currently stored direct tuples contributed by this trigger."""

        self.workflow.require_access("read", actor)
        if not self.workflow.user_id or not self.granted_targets:
            return ()
        subject = to_subject_ref(self.workflow.user)
        targets = models.Q()
        for value in self.granted_targets:
            targets |= models.Q(
                resource_type=value["resource_type"],
                resource_id=value["resource_id"],
                relation=value["relation"],
            )
        return tuple(
            active_relationship_model()
            .objects.filter(
                subject_type=subject.subject_type,
                subject_id=subject.subject_id,
                optional_subject_relation=subject.optional_relation,
            )
            .filter(targets)
            .order_by("resource_type", "resource_id", "relation")
        )

    def validate_configuration(self) -> tuple[Any, Any]:
        """Use the model's final resource input and native filter compiler."""
        if self.source_class.watch_only:
            raise ValidationError({"source": "Watch-only observations cannot be workflow trigger sources."})
        self._trigger_hooks("trigger_input")
        self._trigger_hooks("check_admission")
        model = self.source_class.model(self)
        if self.workflow.subject_model and self.workflow.subject_model != model._meta.label:
            raise ValidationError("The trigger source does not match the workflow subject model.")
        return model, GraphQLSchemas.from_discovery().resource_filter(model, self.condition)

    def trigger_input(self, record: Any) -> dict[str, Any]:
        """Domain extensions build admitted input; source adapters never do."""
        return {}

    def check_admission(self, record: Any, *, actor: Any) -> None:
        """Domain extensions may reject this record without disabling the trigger."""

    def _trigger_hooks(self, name: str) -> list[Any]:
        """Select only source-declared donor hooks in stable order; reject ambiguous input."""
        hooks = []
        for donor in type(self).mro():
            hook = donor.__dict__.get(name)
            if hook is None or donor is Trigger:
                continue
            sources = donor.__dict__.get("trigger_sources")
            if not isinstance(sources, tuple) or any(not isinstance(source, str) for source in sources):
                raise ImproperlyConfigured(f"{donor.__name__}.{name} must declare trigger_sources as a tuple of keys.")
            if self.source in sources:
                hooks.append((donor, hook))
        hooks.sort(key=lambda item: (item[0].__module__, item[0].__qualname__))
        if name == "trigger_input" and len(hooks) > 1:
            raise ImproperlyConfigured("Multiple trigger_input hooks contribute to this source.")
        return [hook for _, hook in hooks]

    def admission_input(self, record: Any) -> dict[str, Any]:
        """Build source-specific input from at most one declared domain contributor."""
        hooks = self._trigger_hooks("trigger_input")
        return hooks[0](self, record) if hooks else Trigger.trigger_input(self, record)

    def admit_record(self, record: Any, *, actor: Any) -> None:
        """Apply every source-specific domain admission policy."""
        hooks = self._trigger_hooks("check_admission")
        if hooks:
            for hook in hooks:
                hook(self, record, actor=actor)
        else:
            Trigger.check_admission(self, record, actor=actor)

    def clean(self) -> None:
        """Validate authoring and require disabling before changing an enabled rule."""
        super().clean()
        self.model_label = "" if self.source_class.model_label else ModelLabelField.normalize(self.model_label)
        self.validate_configuration()
        previous = type(self).system_queryset().filter(pk=self.pk).first() if self.pk else None
        self.granted_targets = previous.granted_targets if previous is not None else []
        if previous is not None and self.workflow_id != previous.workflow_id:
            raise ValidationError("A trigger's workflow cannot be changed.")
        if self.enabled and (previous is None or not previous.enabled):
            raise ValidationError("Enable triggers through the manager action.")
        fields = ("condition", "source", "model_label", *self.source_class.scope_fields)
        if previous is not None:
            for name in fields:
                field = self._meta.get_field(name)
                if not isinstance(field, models.Field):
                    raise ImproperlyConfigured(f"Trigger configuration {name!r} must be a forward field.")
                if getattr(self, field.attname) != getattr(previous, field.attname):
                    if previous.enabled and self.enabled:
                        raise ValidationError("Disable the trigger before editing its rule.")
                    self.disabled_reason = "Trigger configuration changed; enable it again."
                    break

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Validate native saves as well as resource writes."""
        with transaction.atomic(using=self._state.db):
            if self.pk:
                type(self).objects.lock_grants(self.workflow_id)
            previous = None
            if not self._state.adding:
                previous = type(self).system_queryset().filter(pk=self.pk).lock_if_supported(no_key=True).get()
            self.clean()
            self.workflow.require_access("write", self.actor())
            if not self.enabled and kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = {*kwargs["update_fields"], "enabled", "disabled_reason"}
            super().save(*args, **kwargs)
            if previous is not None and previous.granted_targets and not self.enabled:
                type(self).objects.reconcile_grants(self, ())

    def __str__(self) -> str:
        """Identify the source and its model's reader-facing noun."""
        try:
            source_class = self.source_class
            source = source_class.display_label()
            model = source_class.model(self)
        except ImproperlyConfigured, ValidationError:
            source = str(self.source)
            try:
                model = apps.get_model(self.model_label)
            except LookupError, ValueError:
                return source
        return f"{source}: {model._meta.verbose_name}"

    class Meta:
        """Compose admission configuration and its permission identity."""

        abstract = True
        rebac_resource_type = "workflows/trigger"


class TriggerEvent(RecordRefMixin, AngeeDataModel, concrete=True):
    """One retained admission fact per trigger and concrete record reference."""

    merge_policy = MergePolicy.KEEP
    sqid_prefix = "wfe_"
    trigger = models.ForeignKey("workflows.Trigger", on_delete=models.PROTECT, related_name="events")
    record_content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    record_object_id = models.PositiveBigIntegerField()
    record = GenericForeignKey("record_content_type", "record_object_id")
    changed_at = models.DateTimeField(db_default=Now())
    evaluated_at = models.DateTimeField(null=True, blank=True)
    admitted_at = models.DateTimeField(null=True, blank=True)
    rejection = DiagnosticTextField(blank=True, default="")
    objects = TriggerEventManager()

    def __str__(self) -> str:
        """Use the public ledger identity without resolving its protected record."""
        return str(self.sqid)

    class Meta:
        """Enforce concrete ledger identity independently of retained runs."""

        abstract = True
        rebac_resource_type = "workflows/trigger_event"
        constraints = [
            models.UniqueConstraint(
                fields=("trigger", "record_content_type", "record_object_id"),
                name="workflows_trigger_record_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(admitted_at__isnull=True) | models.Q(evaluated_at__isnull=False),
                name="workflows_event_admission_evaluated",
            ),
        ]
        indexes = [
            models.Index(
                fields=("changed_at", "id"),
                condition=models.Q(admitted_at__isnull=True),
                name="workflows_event_pending",
            )
        ]
