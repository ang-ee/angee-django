"""Source adapters and durable, actor-scoped workflow admission."""

from __future__ import annotations

import logging
from contextvars import Context
from dataclasses import replace
from typing import Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import DatabaseError, transaction
from django.db.models import F, Q
from django.db.models.functions import Now
from django.db.models.signals import post_save
from rebac import actor_context, system_context, to_subject_ref
from rebac.actors import is_sudo

from angee.base.actors import actor_user_id
from angee.base.exceptions import exception_text
from angee.base.fields import ModelLabelField
from angee.base.identity import public_id_of
from angee.base.impl import ImplBase
from angee.base.models import AngeeManager, AngeeQuerySet
from angee.base.refs import canonical_record_target
from angee.base.scoping import lock_if_supported, read_scoped_queryset, system_queryset

logger = logging.getLogger(__name__)
TRIGGER_DRAIN_LIMIT = 100
"""One drain examines at most 100 ledger candidates, each in its own transaction."""


class TriggerSource(ImplBase):
    """An event's model and scope; domain policy belongs to Trigger extensions.

    Signal adapters and explicit bulk writers call ``dispatch(model, record)``
    inside their write transaction to feed admission and watches together.
    """
    registry_setting = "ANGEE_WORKFLOW_TRIGGER_SOURCE_CLASSES"

    model_label = ""
    scope_fields: tuple[str, ...] = ()
    """Trigger fields whose edits invalidate the enabling user's authority."""

    @classmethod
    def choice(cls) -> Any:
        """Expose the fixed model to editors without duplicating its declaration."""
        choice = super().choice()
        model_label = cls.model()._meta.label if cls.model_label else ""
        return replace(choice, defaults={**choice.defaults, "source_model": model_label})

    @classmethod
    def model(cls, trigger: Any = None) -> Any:
        """Resolve the fixed or opted-in model, refusing recursive engine sources."""
        label = cls.model_label or trigger.model_label
        try:
            model = apps.get_model(label)
        except (LookupError, ValueError) as error:
            raise ValidationError("The trigger source model is unknown.") from error
        if cls.model_label and trigger is not None and trigger.model_label:
            raise ValidationError("A fixed source owns its model; leave model_label empty.")
        cls.validate_model(model)
        return model

    @classmethod
    def validate_model(cls, model: Any) -> None:
        """Keep source eligibility identical at declaration, connection and capture."""
        if model._meta.app_label in {"workflows", "decisions"}:
            raise ValidationError("Workflow and decision models cannot be trigger sources.")
        if cls.model_label and model._meta.label != ModelLabelField.normalize(cls.model_label):
            raise ValidationError("This model does not belong to the fixed workflow source.")
        if not cls.model_label and not getattr(model, "workflow_trigger", False):
            raise ValidationError("This model has not opted in to workflow triggers.")

    @classmethod
    def check_watch_model(cls, model: Any) -> None:
        """Require a registered native source capable of observing this model."""
        field = apps.get_model("workflows", "Trigger")._meta.get_field("source")
        for key in field.registered_keys():
            try:
                field.resolve_class(key).validate_model(model)
            except ValidationError:
                continue
            return
        raise ValidationError("This model has no registered workflow event source.")

    @classmethod
    def dispatch(cls, model: Any, record: Any) -> None:
        """Capture once under the record lock, never taking a waiting run's lock.

        Locking also covers autocommit saves whose UPDATE preceded post_save.
        A savepoint contains capture failures without undoing the source write.
        """
        try:
            with transaction.atomic(), system_context(reason="workflows.source_dispatch"):
                cls.validate_model(model)
                target = canonical_record_target(record)
                records = system_queryset(target.content_type.model_class()).filter(pk=target.object_id)
                if lock_if_supported(records, no_key=True).first() is None:
                    return
                apps.get_model("workflows", "TriggerEvent").objects.record_change(model, record, source=cls.key)
                apps.get_model("workflows", "StepWatch").objects.record_change(record)
        except Exception:
            logger.exception("Workflow source capture failed.")

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
            cls.dispatch(sender, instance)


class TriggerEventQuerySet(AngeeQuerySet):
    """Pending admission is a re-armed, never-admitted ledger fact."""

    def pending(self) -> Any:
        """A later save retries a rejection; admission survives run pruning."""
        return self.filter(Q(evaluated_at__isnull=True) | Q(changed_at__gt=F("evaluated_at")), admitted_at=None)


class TriggerEventManager(AngeeManager.from_queryset(TriggerEventQuerySet)):  # type: ignore[misc]
    """Capture without compromising the transaction that changed the record."""

    def record_change(self, model: Any, record: Any, *, source: str = "record_changed") -> None:
        """Upsert concrete reference columns, re-arming rejects; never raise.

        Sources call this through TriggerSource.dispatch so watches also observe
        their event. The savepoint preserves writes when ledger capture fails.
        """
        try:
            with transaction.atomic(), system_context(reason="workflows.capture"):
                triggers = apps.get_model("workflows", "Trigger")
                implementation = triggers._meta.get_field("source").resolve_class(source)
                queryset = system_queryset(triggers).filter(enabled=True, source=source)
                if not implementation.model_label:
                    queryset = queryset.filter(model_label=model._meta.label)
                implementation.validate_model(model)
                content_type = ContentType.objects.get_for_model(model)
                for trigger in implementation.matching_triggers(queryset, record).order_by("pk"):
                    system_queryset(self.model).update_or_create(
                        trigger=trigger, record_content_type=content_type, record_object_id=record.pk,
                        defaults={"changed_at": Now()},
                    )
        except Exception:
            logger.exception("Workflow trigger capture failed.")


class TriggerQuerySet(AngeeQuerySet):
    """Keep authored writes at the validating model; engine updates use explicit scope."""

    def _check_bulk_write(self) -> None:
        if not self.is_sudo():
            raise ValidationError("Edit trigger configuration through model save().")

    def update(self, **kwargs: Any) -> int:
        """Reserve bulk transitions for the engine's explicitly elevated queryset."""
        self._check_bulk_write()
        return super().update(**kwargs)

    def bulk_create(self, *args: Any, **kwargs: Any) -> Any:
        """Keep signal-free insertion from choosing the server-owned acting identity."""
        self._check_bulk_write()
        return super().bulk_create(*args, **kwargs)


class TriggerManager(AngeeManager.from_queryset(TriggerQuerySet)):  # type: ignore[misc]
    """Enablement and admission serialize on the trigger before its ledger row."""

    def enable(self, trigger: Any, *, actor: Any) -> Any:
        """Require workflow write and pin the enabling user on the server."""
        if is_sudo():
            return Context().run(self.enable, trigger, actor=actor)
        user_id = actor_user_id(to_subject_ref(actor)) if actor is not None else None
        if user_id is None:
            raise PermissionDenied("Enabling a trigger requires an acting user.")
        if not system_queryset(get_user_model()).filter(pk=user_id, is_active=True).exists():
            raise PermissionDenied("Enabling a trigger requires an active user.")
        trigger.workflow.require_access("write", actor)
        with transaction.atomic(), actor_context(actor):
            current = system_queryset(self.model).filter(pk=trigger.pk).lock_if_supported(no_key=True).get()
            current.with_actor(actor)
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
        An isolated context drops a caller's ambient bypass before actor hooks.
        """
        if is_sudo():
            return Context().run(self.admit, event)
        events = apps.get_model("workflows", "TriggerEvent")
        with transaction.atomic():
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
                if not trigger.run_as.is_active:
                    raise ValidationError("The trigger requires an active acting user.")
                model, condition = trigger.validate_configuration()
            except (ValidationError, ImproperlyConfigured, LookupError) as error:
                reason = exception_text(error)
                self._disable(trigger, reason)
                system_queryset(events).filter(pk=current.pk).update(evaluated_at=F("changed_at"), rejection=reason)
                return False
            except Exception:
                logger.exception("Workflow trigger configuration failed for event %s.", current.pk)
                return False
            try:
                with transaction.atomic(), actor_context(trigger.run_as):
                    trigger.with_actor(trigger.run_as)
                    if current.record_content_type_id != ContentType.objects.get_for_model(model).pk:
                        raise ValidationError("The source model changed after this event was recorded.")
                    queryset = read_scoped_queryset(model, trigger.run_as)
                    record = (condition(queryset).filter(pk=current.record_object_id).first()
                              if queryset is not None else None)
                    if record is None:
                        raise PermissionDenied("The record is inaccessible or no longer matches the condition.")
                    trigger.source_class.check_access(trigger, trigger.run_as, record)
                    trigger.admit_record(record, actor=trigger.run_as)
                    apps.get_model("workflows", "WorkflowRun").objects.start(
                        trigger.workflow, actor=trigger.run_as, subject=record, input=trigger.admission_input(record),
                        request_key=f"trigger:{public_id_of(trigger)}:{public_id_of(record)}",
                        trigger_event=current,
                    )
            except (ValidationError, PermissionDenied) as error:
                system_queryset(events).filter(pk=current.pk).update(
                    evaluated_at=F("changed_at"), rejection=exception_text(error),
                )
                return False
            except DatabaseError:
                logger.exception("Workflow trigger admission will retry event %s.", current.pk)
                return False
            except Exception:
                logger.exception("Workflow trigger admission failed for event %s.", current.pk)
                return False
            system_queryset(events).filter(pk=current.pk).update(
                evaluated_at=F("changed_at"), admitted_at=Now(), rejection="",
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
