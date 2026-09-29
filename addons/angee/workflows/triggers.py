"""Source adapters and durable, actor-scoped workflow admission."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.db.models.functions import Now
from django.db.models.signals import post_save
from rebac import actor_context, system_context, to_subject_ref

from angee.base.actors import actor_user_id
from angee.base.exceptions import exception_text
from angee.base.impl import ImplBase
from angee.base.models import AngeeManager, AngeeQuerySet
from angee.base.scoping import lock_if_supported, read_scoped_queryset, system_queryset

logger = logging.getLogger(__name__)
TRIGGER_DRAIN_LIMIT = 100
"""One drain examines at most 100 ledger candidates, each in its own transaction."""


class TriggerSource(ImplBase):
    """An event's model and scope; domain policy belongs to Trigger extensions.

    Signal adapters call ``TriggerEvent.objects.record_change(model, record,
    source=cls.key)``. The model and scope contract is reusable by a future
    watch owner; current receivers dispatch only to this admission ledger.
    """

    model_label = ""

    @classmethod
    def choice(cls) -> Any:
        """Expose the fixed model to editors without duplicating its declaration."""
        choice = super().choice()
        return replace(choice, defaults={**choice.defaults, "source_model": cls.model_label})

    @classmethod
    def model(cls, trigger: Any) -> Any:
        """Resolve the fixed or opted-in model, refusing recursive engine sources."""
        label = cls.model_label or trigger.model_label
        try:
            model = apps.get_model(label)
        except (LookupError, ValueError) as error:
            raise ValidationError("The trigger source model is unknown.") from error
        if cls.model_label and trigger.model_label:
            raise ValidationError("A fixed source owns its model; leave model_label empty.")
        cls.validate_model(model)
        return model

    @classmethod
    def validate_model(cls, model: Any) -> None:
        """Keep source eligibility identical at declaration, connection and capture."""
        if model._meta.app_label in {"workflows", "decisions"}:
            raise ValidationError("Workflow and decision models cannot be trigger sources.")
        if not cls.model_label and not getattr(model, "workflow_trigger", False):
            raise ValidationError("This model has not opted in to workflow triggers.")

    @classmethod
    def matching_triggers(cls, queryset: Any, record: Any) -> Any:
        """Let scoped sources narrow emission before a ledger row is written."""
        return queryset

    @classmethod
    def check_access(cls, trigger: Any, actor: Any, record: Any = None) -> None:
        """Scoped sources recheck their scope on enable and every admission."""

    @classmethod
    def connect(cls) -> None:
        """Connect a native source signal during AppConfig.ready, if needed."""


class RecordChanged(TriggerSource):
    """Native saves of models that declare ``workflow_trigger = True``."""

    key = "record_changed"
    label = "Record changed"

    @classmethod
    def connect(cls) -> None:
        """Connect opted-in senders independently of live broadcasting policy."""
        for model in apps.get_models():
            if getattr(model, "workflow_trigger", False):
                cls.validate_model(model)
                post_save.connect(cls.changed, sender=model, weak=False,
                                  dispatch_uid=f"workflows.record_changed.{model._meta.label_lower}")

    @classmethod
    def changed(cls, sender: Any, *, instance: Any, raw: bool = False, **kwargs: Any) -> None:
        """Ignore fixture loading and delegate all durable capture to its owner."""
        if not raw:
            apps.get_model("workflows", "TriggerEvent").objects.record_change(sender, instance, source=cls.key)


class TriggerEventQuerySet(AngeeQuerySet):
    """Pending admission is a re-armed, never-admitted ledger fact."""

    def pending(self) -> Any:
        """A later save retries a rejection; admission survives run pruning."""
        return self.filter(Q(evaluated_at__isnull=True) | Q(changed_at__gt=F("evaluated_at")), admitted_at=None)


class TriggerEventManager(AngeeManager.from_queryset(TriggerEventQuerySet)):  # type: ignore[misc]
    """Capture without compromising the transaction that changed the record."""

    def record_change(self, model: Any, record: Any, *, source: str = "record_changed") -> None:
        """Upsert concrete reference columns, re-arming rejects; never raise.

        Bulk writers call this explicitly inside their write transaction. The
        savepoint contains capture failures, preserving their original write.
        """
        try:
            with transaction.atomic(), system_context(reason="workflows.capture"):
                triggers = apps.get_model("workflows", "Trigger")
                implementation = triggers._meta.get_field("source").resolve_class(source)
                queryset = system_queryset(triggers).filter(enabled=True, source=source)
                if implementation.model_label:
                    if model._meta.label_lower != implementation.model_label.lower():
                        return
                else:
                    queryset = queryset.filter(model_label=model._meta.label_lower)
                implementation.validate_model(model)
                content_type = ContentType.objects.get_for_model(model)
                for trigger in implementation.matching_triggers(queryset, record).order_by("pk"):
                    system_queryset(self.model).update_or_create(
                        trigger=trigger, record_content_type=content_type, record_object_id=record.pk,
                        defaults={"changed_at": Now()},
                    )
        except Exception:
            logger.exception("Workflow trigger capture failed.")


class TriggerManager(AngeeManager):
    """Enablement and admission serialize on the trigger before its ledger row."""

    def enable(self, trigger: Any, *, actor: Any) -> Any:
        """Require workflow write and pin the enabling user on the server."""
        user_id = actor_user_id(to_subject_ref(actor)) if actor is not None else None
        if user_id is None:
            raise PermissionDenied("Enabling a trigger requires an acting user.")
        trigger.workflow.require_access("write", actor)
        with transaction.atomic(), system_context(reason="workflows.enable_trigger"):
            current = system_queryset(self.model).filter(pk=trigger.pk).lock_if_supported(no_key=True).get()
            current.workflow.require_access("write", actor)
            current.validate_configuration()
            if not current.workflow.published_id:
                raise ValidationError("Publish the workflow before enabling its trigger.")
            current.source_class.check_access(current, actor)
            system_queryset(self.model).filter(pk=current.pk).update(
                enabled=True, run_as_id=user_id, disabled_reason="",
            )
            current.refresh_from_db()
            return current

    def disable(self, trigger: Any, *, actor: Any) -> Any:
        """Drop the acting identity when an operator disables admission."""
        trigger.workflow.require_access("write", actor)
        with transaction.atomic(), system_context(reason="workflows.disable_trigger"):
            current = system_queryset(self.model).filter(pk=trigger.pk).lock_if_supported(no_key=True).get()
            current.workflow.require_access("write", actor)
            self._disable(current, "")
            current.refresh_from_db()
            return current

    def _disable(self, trigger: Any, reason: str) -> None:
        system_queryset(self.model).filter(pk=trigger.pk).update(
            enabled=False, run_as=None, disabled_reason=reason,
        )

    def admit(self, event: Any) -> bool:
        """Lock the source record, trigger, then event and recheck current policy.

        Native saves already hold their record before capture. Sharing that order
        lets domain admission hooks write it without a record/event lock cycle.
        Busy records stay pending; deleted records proceed to a durable rejection.
        """
        events = apps.get_model("workflows", "TriggerEvent")
        with transaction.atomic(), system_context(reason="workflows.admit_trigger"):
            event = system_queryset(events).filter(pk=event.pk).select_related("record_content_type").first()
            if event is None:
                return False
            record_model = event.record_content_type.model_class()
            if record_model is not None:
                records = system_queryset(record_model).filter(pk=event.record_object_id)
                if lock_if_supported(records, no_key=True, skip_locked=True).first() is None and records.exists():
                    return False
            trigger = (system_queryset(self.model).filter(pk=event.trigger_id)
                       .lock_if_supported(no_key=True, skip_locked=True).first())
            if trigger is None or not trigger.enabled:
                return False
            current = (system_queryset(events).pending().filter(pk=event.pk)
                       .lock_if_supported(no_key=True, skip_locked=True).first())
            if current is None:
                return False
            try:
                model, condition = trigger.validate_configuration()
            except (ValidationError, ImproperlyConfigured, LookupError) as error:
                reason = exception_text(error)
                self._disable(trigger, reason)
                system_queryset(events).filter(pk=current.pk).update(evaluated_at=F("changed_at"), rejection=reason)
                return False
            try:
                with transaction.atomic(), actor_context(trigger.run_as):
                    if current.record_content_type_id != ContentType.objects.get_for_model(model).pk:
                        raise ValidationError("The source model changed after this event was recorded.")
                    queryset = read_scoped_queryset(model, trigger.run_as)
                    record = (condition(queryset).filter(pk=current.record_object_id).first()
                              if queryset is not None else None)
                    if record is None:
                        raise PermissionDenied("The record is inaccessible or no longer matches the condition.")
                    trigger.source_class.check_access(trigger, trigger.run_as, record)
                    trigger.check_admission(record, actor=trigger.run_as)
                    run = apps.get_model("workflows", "WorkflowRun").objects.start(
                        trigger.workflow, actor=trigger.run_as, subject=record, input=trigger.trigger_input(record),
                        request_key=f"trigger:{trigger.pk}:{record.pk}",
                    )
                    system_queryset(type(run)).filter(pk=run.pk).update(trigger_event=current)
            except Exception as error:
                system_queryset(events).filter(pk=current.pk).update(
                    evaluated_at=F("changed_at"), rejection=exception_text(error),
                )
                return False
            system_queryset(events).filter(pk=current.pk).update(
                evaluated_at=F("changed_at"), admitted_at=Now(), rejection="", run=run,
            )
            return True

    def drain(self) -> int:
        """Examine an ordered, bounded batch; one broken event cannot stop later rows."""
        events = apps.get_model("workflows", "TriggerEvent")
        candidates = list(system_queryset(events).pending().filter(trigger__enabled=True)
                          .order_by("pk")[:TRIGGER_DRAIN_LIMIT])
        admitted = 0
        for event in candidates:
            try:
                admitted += self.admit(event)
            except Exception:
                logger.exception("Workflow trigger admission failed for event %s.", event.pk)
        return admitted
