"""Source adapters and durable, actor-scoped workflow admission."""

from __future__ import annotations

import logging
from contextvars import Context
from dataclasses import dataclass, field, replace
from inspect import getattr_static
from typing import Any, ClassVar

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core import checks
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import DatabaseError, transaction
from django.db.models import F, Q
from django.db.models.functions import Now
from django.db.models.signals import post_save
from rebac import (
    ObjectRef,
    RelationshipTuple,
    actor_context,
    generic_target,
    resolve_subjects,
    system_context,
    to_object_ref,
    to_subject_ref,
)
from rebac.actors import is_sudo
from rebac.backends import backend
from rebac.relationships import delete_relationship, write_relationships
from rebac.resources import model_for_resource_type

from angee.base.errors import exception_text
from angee.base.fields import ModelLabelField
from angee.base.identity import public_id_of
from angee.base.impl import ImplBase, resolve_all_impl_classes
from angee.base.models import AngeeManager, AngeeQuerySet, record_display_label
from angee.base.permissions import rebac_relation_label
from angee.base.scoping import lock_if_supported, read_scoped_queryset, system_queryset
from angee.iam.identity import user_label
from angee.iam.service_users import sync_service_user

logger = logging.getLogger(__name__)
TRIGGER_DRAIN_LIMIT = 100
"""One drain examines at most 100 ledger candidates, each in its own transaction."""


class PublisherAuthorityDenied(PermissionDenied):
    """A version's publisher can no longer delegate the principal's grants."""


@dataclass(frozen=True)
class TriggerEnablePreview:
    """Grant and run-reader facts shown before an authorized enablement."""

    grants: tuple[str, ...]
    run_readers: tuple[str, ...]


@dataclass(frozen=True)
class TriggerGrantTarget:
    """A source grant and the REBAC permission authorizing its delegation."""

    resource: ObjectRef
    relation: str
    grant_permission: str = field(default="", compare=False)
    grant_resource: ObjectRef | None = field(default=None, compare=False)

    def target_kind(self) -> str:
        """Use the model's reader noun, falling back to the declared resource kind."""
        model = model_for_resource_type(self.resource.resource_type)
        if model is not None:
            return str(model._meta.verbose_name)
        return self.resource.resource_type.replace("/", " ").replace("_", " ")

    def relation_label(self) -> str:
        """Use the relation owner's display name for this grant."""

        return rebac_relation_label(self.resource.resource_type, self.relation)

    def readable_target_label(self, actor: Any) -> str | None:
        """Return the record label only when the actor can read that row."""

        model = model_for_resource_type(self.resource.resource_type)
        if model is not None and model._meta.managed:
            visible = read_scoped_queryset(model, actor)
            target = visible.filter(pk=self.resource.resource_id).first()
            if target is not None:
                return record_display_label(target)
        return None

    def target_label(self, *, actor: Any | None = None) -> str:
        """Describe an accessible target without exposing a raw authorization ID."""

        if actor is not None and (label := self.readable_target_label(actor)) is not None:
            return label
        kind = self.target_kind()
        if self.resource.resource_type.endswith("/role"):
            return f"{str(self.resource.resource_id).replace('_', ' ')} ({kind})"
        return kind

    def permits(self, actor: Any) -> bool:
        """Check the source-declared delegation permission at its owning resource."""
        return bool(self.grant_permission and backend().has_access(
            subject=to_subject_ref(actor), action=self.grant_permission,
            resource=self.grant_resource or self.resource,
        ))

    def require_grant_access(self, actor: Any) -> None:
        """Use the target owner's REBAC permission before minting a tuple."""
        if not self.grant_permission:
            raise ImproperlyConfigured(f"No grant permission was declared for {self.resource}#{self.relation}.")
        if not self.permits(actor):
            raise PermissionDenied(f"The acting user cannot grant {self.relation} on {self.resource}.")

    def require_publisher_access(self, publisher: Any, version: Any) -> None:
        """Refuse a published graph when its author cannot delegate this grant now."""
        if self.permits(publisher):
            return
        detail = ("grant provenance is missing; enable the trigger again"
                  if not self.grant_permission else "the publisher cannot delegate it")
        raise PublisherAuthorityDenied(
            f"{version} published by {user_label(publisher)} cannot run as the workflow principal: "
            f"{self.relation} on {self.target_label(actor=publisher)} is unavailable because {detail}."
        )

    def stored(self) -> dict[str, str]:
        """Retain grant provenance when a source implementation later disappears."""
        return {
            "resource_type": self.resource.resource_type,
            "resource_id": str(self.resource.resource_id),
            "relation": self.relation,
            "grant_permission": self.grant_permission,
            "grant_resource_type": (self.grant_resource or self.resource).resource_type,
            "grant_resource_id": str((self.grant_resource or self.resource).resource_id),
        }

    @classmethod
    def from_stored(cls, value: dict[str, str]) -> TriggerGrantTarget:
        """Restore the exact tuple target that an enabled trigger contributed."""
        return cls(
            ObjectRef(value["resource_type"], value["resource_id"]), value["relation"],
            value.get("grant_permission", ""),
            ObjectRef(value["grant_resource_type"], value["grant_resource_id"])
            if "grant_resource_type" in value and "grant_resource_id" in value else None,
        )


class RecordChangedOptIn:
    """A model explicitly declares native record capture and its grant scope."""

    @classmethod
    def record_changed_grant_targets(cls, trigger: Any) -> tuple[TriggerGrantTarget, ...]:
        """Return the bounded source scope; concrete models must implement it."""
        raise NotImplementedError(f"{cls.__name__} must declare record_changed_grant_targets().")


class TriggerSource(ImplBase):
    """An event's model and scope; domain policy belongs to Trigger extensions.

    Signal adapters and explicit bulk writers call ``dispatch(model, record)``
    inside their write transaction to feed admission and watches together.
    A registered subclass of a registered source refines it under another key
    and shares its native event: the base alone connects and dispatches.
    """
    registry_setting = "ANGEE_WORKFLOW_TRIGGER_SOURCE_CLASSES"

    model_label: ClassVar[str] = ""
    scope_fields: ClassVar[tuple[str, ...]] = ()
    """Trigger fields whose edits invalidate its contributed grants."""

    @classmethod
    def registered(cls) -> tuple[type[TriggerSource], ...]:
        """Return this source and its registered refinements in key order."""
        return tuple(source for source in resolve_all_impl_classes(TriggerSource) if issubclass(source, cls))

    @classmethod
    def connect_registered(cls) -> None:
        """Connect each native event once, through the source no other one refines."""
        sources = cls.registered()
        for source in sources:
            if not any(issubclass(source, base) for base in sources if base is not source):
                source.connect()

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

    @classmethod
    def dispatch(cls, model: Any, record: Any) -> None:
        """Capture once under the record lock, never taking a waiting run's lock.

        One native event feeds the ledger of this source and of each registered
        refinement, then the watches. Locking also covers autocommit saves whose
        UPDATE preceded post_save. A savepoint contains capture failures without
        undoing the source write.
        """
        try:
            with transaction.atomic(), system_context(reason="workflows.source_dispatch"):
                cls.validate_model(model)
                target = generic_target(record)
                records = system_queryset(target.content_type.model_class()).filter(pk=target.object_id)
                if lock_if_supported(records, no_key=True).first() is None:
                    return
                events = apps.get_model("workflows", "TriggerEvent").objects
                for source in cls.registered():
                    events.record_change(model, record, source=source.key)
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
    def grant_targets(cls, trigger: Any) -> tuple[TriggerGrantTarget, ...]:
        """Declare the objects and relations this source grants on enable."""
        raise NotImplementedError(f"{cls.__name__} must declare grant_targets().")

    @classmethod
    def connect(cls) -> None:
        """Connect a native source signal during AppConfig.ready, if needed.

        ``connect_registered`` calls this for the source that owns the event,
        never for its registered refinements.
        """


class RecordChanged(TriggerSource):
    """Native saves of models with an explicit grant-bearing opt-in."""

    key = "record_changed"
    label = "Record changed"

    @classmethod
    def validate_model(cls, model: Any) -> None:
        """Only an explicitly opted-in model can produce record-change events."""
        super().validate_model(model)
        if not issubclass(model, RecordChangedOptIn):
            raise ValidationError("This model has not opted in to workflow triggers.")

    @classmethod
    def grant_targets(cls, trigger: Any) -> tuple[TriggerGrantTarget, ...]:
        """Use the opted-in model's declared permission boundary."""
        model = cls.model(trigger)
        return tuple(model.record_changed_grant_targets(trigger))

    @classmethod
    def connect(cls) -> None:
        """Connect opted-in senders independently of live broadcasting policy."""
        for model in apps.get_models():
            if issubclass(model, RecordChangedOptIn):
                cls.validate_model(model)
                post_save.connect(cls.changed, sender=model, weak=False,
                                  dispatch_uid=f"workflows.record_changed.{model._meta.label_lower}")

    @classmethod
    def changed(cls, sender: Any, *, instance: Any, raw: bool = False, **kwargs: Any) -> None:
        """Ignore fixture loading and delegate all durable capture to its owner."""
        if not raw:
            cls.dispatch(sender, instance)


def check_record_changed_models(**kwargs: Any) -> list[checks.CheckMessage]:
    """Reject opted-in models lacking their required source grant declaration."""
    del kwargs
    inherited = getattr_static(RecordChangedOptIn, "record_changed_grant_targets")
    return [
        checks.Error(
            f"{model._meta.label} must implement record_changed_grant_targets().",
            obj=model, id="workflows.E001",
        )
        for model in apps.get_models()
        if issubclass(model, RecordChangedOptIn)
        and getattr_static(model, "record_changed_grant_targets") is inherited
    ]


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
        """Keep signal-free insertion from bypassing server-owned activation."""
        self._check_bulk_write()
        return super().bulk_create(*args, **kwargs)


class TriggerManager(AngeeManager.from_queryset(TriggerQuerySet)):  # type: ignore[misc]
    """Enablement and admission serialize on the trigger before its ledger row."""

    def lock_grants(self, workflow_id: Any, *, skip_locked: bool = False) -> Any:
        """Serialize grants shared by triggers of one workflow."""
        workflow = apps.get_model("workflows", "Workflow")
        rows = lock_if_supported(
            system_queryset(workflow).filter(pk=workflow_id), no_key=True, skip_locked=skip_locked,
        )
        return rows.first() if skip_locked else rows.get()

    def reconcile_grants(self, trigger: Any, targets: tuple[TriggerGrantTarget, ...]) -> None:
        """Reconcile this trigger's contributed tuples without dropping shared grants."""
        old = {TriggerGrantTarget.from_stored(value) for value in trigger.granted_targets}
        new = set(targets)
        workflow = system_queryset(apps.get_model("workflows", "Workflow")).get(pk=trigger.workflow_id)
        user = sync_service_user(workflow, prefix="workflow")
        subject = to_subject_ref(user)
        retained = {
            TriggerGrantTarget.from_stored(value)
            for values in system_queryset(self.model).filter(
                workflow_id=trigger.workflow_id, enabled=True,
            ).exclude(pk=trigger.pk).values_list("granted_targets", flat=True)
            for value in values
        }
        def order(item: TriggerGrantTarget) -> tuple[str, str, str]:
            return item.resource.resource_type, str(item.resource.resource_id), item.relation

        with system_context(reason="workflows.trigger_grants"):
            if new:
                write_relationships([
                    RelationshipTuple(resource=target.resource, relation=target.relation, subject=subject)
                    for target in sorted(new, key=order)
                ])
            for target in sorted(old - new - retained, key=order):
                delete_relationship(RelationshipTuple(
                    resource=target.resource, relation=target.relation, subject=subject,
                ))
            stored = [target.stored() for target in sorted(new, key=order)]
            system_queryset(self.model).filter(pk=trigger.pk).update(granted_targets=stored)
            trigger.granted_targets = stored

    def _enable_targets(self, trigger: Any, actor: Any) -> tuple[TriggerGrantTarget, ...]:
        """Use one permission and source check for preview and enablement."""
        trigger.workflow.require_access("write", actor)
        trigger.validate_configuration()
        if not trigger.workflow.published_id:
            raise ValidationError("Publish the workflow before enabling its trigger.")
        trigger.source_class.check_access(trigger, actor)
        targets = tuple(trigger.source_class.grant_targets(trigger))
        if not targets:
            raise ValidationError("The trigger source must declare at least one grant target.")
        if len(set(targets)) != len(targets):
            raise ValidationError("The trigger source declared a duplicate grant target.")
        for target in targets:
            target.require_grant_access(actor)
        return targets

    def enable_preview(self, trigger: Any, *, actor: Any) -> TriggerEnablePreview | None:
        """Disclose source grants and workflow run readers only to eligible enablers."""
        if actor is None:
            return None
        try:
            targets = self._enable_targets(trigger, actor)
        except (PermissionDenied, ValidationError, ImproperlyConfigured):
            return None
        resource = to_object_ref(trigger.workflow)
        readers: list[str] = []
        for subject_type, label in (("auth/user", "User"), ("auth/group", "Group")):
            refs = backend().lookup_subjects(resource=resource, action="monitor", subject_type=subject_type)
            with system_context(reason="workflows.enable_preview readers"):
                resolved = resolve_subjects(refs)
            readers.extend(f"{label}: {user_label(row) if subject_type == 'auth/user' else row}"
                           for row in resolved.values())
        return TriggerEnablePreview(
            grants=tuple(sorted(
                f"{target.relation_label()} on {target.target_label(actor=actor)}" for target in targets
            )),
            run_readers=tuple(sorted(set(readers))),
        )

    def enable(self, trigger: Any, *, actor: Any) -> Any:
        """Require workflow write and source-declared delegation authority."""
        if is_sudo():
            return Context().run(self.enable, trigger, actor=actor)
        if actor is None:
            raise PermissionDenied("Enabling a trigger requires an acting user.")
        trigger.workflow.require_access("write", actor)
        with transaction.atomic(), actor_context(actor):
            self.lock_grants(trigger.workflow_id)
            current = system_queryset(self.model).filter(pk=trigger.pk).lock_if_supported(no_key=True).get()
            current.with_actor(actor)
            targets = self._enable_targets(current, actor)
            self.reconcile_grants(current, targets)
            system_queryset(self.model).filter(pk=current.pk).update(
                enabled=True, disabled_reason="",
            )
            current.refresh_from_db()
            return current

    def disable(self, trigger: Any, *, actor: Any) -> Any:
        """Stop admission and release this trigger's source grants."""
        trigger.workflow.require_access("write", actor)
        with transaction.atomic(), system_context(reason="workflows.disable_trigger"):
            self.lock_grants(trigger.workflow_id)
            current = system_queryset(self.model).filter(pk=trigger.pk).lock_if_supported(no_key=True).get()
            current.workflow.require_access("write", actor)
            self._disable(current, "")
            current.refresh_from_db()
            return current

    def revoke_grant(
        self, trigger: Any, *, actor: Any, resource_type: str, resource_id: str, relation: str,
    ) -> Any:
        """Revoke one contribution; disable admission when its source still needs it."""
        if is_sudo():
            return Context().run(
                self.revoke_grant, trigger, actor=actor,
                resource_type=resource_type, resource_id=resource_id, relation=relation,
            )
        trigger.workflow.require_access("write", actor)
        target = TriggerGrantTarget(ObjectRef(resource_type, resource_id), relation)
        with transaction.atomic(), actor_context(actor):
            self.lock_grants(trigger.workflow_id)
            current = system_queryset(self.model).filter(pk=trigger.pk).lock_if_supported(no_key=True).get()
            current.workflow.require_access("write", actor)
            held = {TriggerGrantTarget.from_stored(value) for value in current.granted_targets}
            if target not in held:
                raise ValidationError("This trigger does not hold the selected grant.")
            required = next(
                (candidate for candidate in current.source_class.grant_targets(current) if candidate == target), None,
            ) if current.enabled else None
            if required is not None:
                required.require_grant_access(actor)
                reason = (
                    f"The workflow principal's {relation} grant "
                    "was revoked; enable this trigger again."
                )
                self._disable(current, reason)
            else:
                self.reconcile_grants(current, tuple(held - {target}))
            current.refresh_from_db()
            return current

    def _disable(self, trigger: Any, reason: str) -> None:
        system_queryset(self.model).filter(pk=trigger.pk).update(
            enabled=False, disabled_reason=reason,
        )
        self.reconcile_grants(trigger, ())

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
            event = system_queryset(events).filter(pk=event.pk).select_related(
                "record_content_type", "trigger",
            ).first()
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
                if self.lock_grants(trigger.workflow_id, skip_locked=True) is None:
                    return False
                self._disable(trigger, reason)
                system_queryset(events).filter(pk=current.pk).update(evaluated_at=F("changed_at"), rejection=reason)
                return False
            except Exception:
                logger.exception("Workflow trigger configuration failed for event %s.", current.pk)
                return False
            try:
                principal = trigger.workflow.user
                with transaction.atomic(), actor_context(principal):
                    trigger.with_actor(principal)
                    if current.record_content_type_id != ContentType.objects.get_for_model(model).pk:
                        raise ValidationError("The source model changed after this event was recorded.")
                    queryset = read_scoped_queryset(model, principal)
                    record = condition(queryset).filter(pk=current.record_object_id).first()
                    if record is None:
                        raise PermissionDenied("The record is inaccessible or no longer matches the condition.")
                    trigger.source_class.check_access(trigger, principal, record)
                    trigger.admit_record(record, actor=principal)
                    apps.get_model("workflows", "WorkflowRun").objects.start(
                        trigger.workflow, actor=principal, subject=record, input=trigger.admission_input(record),
                        request_key=f"trigger:{public_id_of(trigger)}:{public_id_of(record)}",
                        trigger_event=current,
                    )
            except PublisherAuthorityDenied as error:
                reason = exception_text(error)
                if self.lock_grants(trigger.workflow_id, skip_locked=True) is None:
                    return False
                self._disable(trigger, reason)
                system_queryset(events).filter(pk=current.pk).update(
                    evaluated_at=F("changed_at"), rejection=reason,
                )
                return False
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
