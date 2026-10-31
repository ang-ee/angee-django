"""Source adapters and durable, actor-scoped workflow admission."""

from __future__ import annotations

import logging
from collections.abc import Hashable
from dataclasses import dataclass, field, replace
from inspect import getattr_static
from typing import TYPE_CHECKING, Any, ClassVar

from django.apps import apps
from django.core import checks
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Model
from django.db.models.signals import post_save
from django.utils.text import capfirst
from rebac import (
    ObjectRef,
    database_context,
    generic_target,
    system_context,
    to_subject_ref,
)
from rebac.backends import backend
from rebac.resources import model_for_resource_type
from rebac.schema import FieldBinding

from angee.base.fields.implementation import ImplClassField
from angee.base.fields.text import ModelLabelField
from angee.base.implementations import ImplBase, resolve_all_impl_classes
from angee.base.models.base import record_display_label
from angee.base.permissions import effective_rebac_definition
from angee.base.querysets import by_key, row_lock_paths
from angee.base.signals import connect_for_models
from angee.iam.identity import user_label

if TYPE_CHECKING:
    pass

from angee.workflows.querysets import TriggerEventQuerySet as TriggerEventQuerySet

logger = logging.getLogger(__name__)


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

        model = model_for_resource_type(self.resource.resource_type)
        if model is not None:
            definition = effective_rebac_definition(model)
            relation = (
                next((item for item in definition.relations if item.name == self.relation), None)
                if definition
                else None
            )
            if (
                relation is not None
                and isinstance(relation.backing, FieldBinding)
                and "__" not in relation.backing.path
            ):
                try:
                    field = model._meta.get_field(relation.backing.path)
                except FieldDoesNotExist:
                    pass
                else:
                    return capfirst(str(field.verbose_name))
        return capfirst(self.relation.replace("_", " "))

    def readable_target_label(self, actor: Any) -> str | None:
        """Return the record label only when the actor can read that row."""

        model = model_for_resource_type(self.resource.resource_type)
        if model is not None and model._meta.managed:
            visible = model.objects.with_actor(actor)
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
        return bool(
            self.grant_permission
            and backend().has_access(
                subject=to_subject_ref(actor),
                action=self.grant_permission,
                resource=self.grant_resource or self.resource,
            )
        )

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
        detail = (
            "grant provenance is missing; enable the trigger again"
            if not self.grant_permission
            else "the publisher cannot delegate it"
        )
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
            ObjectRef(value["resource_type"], value["resource_id"]),
            value["relation"],
            value.get("grant_permission", ""),
            ObjectRef(value["grant_resource_type"], value["grant_resource_id"])
            if "grant_resource_type" in value and "grant_resource_id" in value
            else None,
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
    fields: ClassVar[tuple[str, ...]] = ()
    watch_only: ClassVar[bool] = False
    """Observe saves for step watches without becoming an admission source."""
    scope_fields: ClassVar[tuple[str, ...]] = ()
    """Trigger fields whose edits invalidate its contributed grants."""

    @classmethod
    def registered(cls) -> tuple[type[TriggerSource], ...]:
        """Return admission-capable sources and refinements in key order."""
        return tuple(source for source in cls.observations() if not source.watch_only)

    @classmethod
    def observations(cls) -> tuple[type[TriggerSource], ...]:
        """Return every registered save observation in key order."""
        return tuple(source for source in resolve_all_impl_classes(TriggerSource) if issubclass(source, cls))

    @classmethod
    def connect_registered(cls) -> None:
        """Connect each native event once, through the source no other one refines."""
        sources = cls.observations()
        watched: dict[Any, type[TriggerSource]] = {}
        for source in sources:
            if not source.watch_only:
                continue
            model = source.model()
            if previous := watched.get(model):
                raise ImproperlyConfigured(
                    f"Multiple watch registrations for {model._meta.label}: {previous.key} and {source.key}."
                )
            watched[model] = source
            for admission in sources:
                if admission.watch_only:
                    continue
                try:
                    admission.validate_model(model)
                except ValidationError:
                    continue
                raise ImproperlyConfigured(
                    f"{model._meta.label} already has native trigger observation {admission.key}."
                )
        for source in sources:
            if not any(issubclass(source, base) for base in sources if base is not source):
                source.connect()

    @classmethod
    def observes(cls, model: Any) -> bool:
        """Whether a registered source captures this model's native changes."""
        for source in cls.observations():
            try:
                source.validate_model(model)
            except ValidationError:
                continue
            return True
        return False

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
        for name in cls.fields:
            model._meta.get_field(name)

    @classmethod
    def check_model(cls, model: Any) -> None:
        """Require one registered admission or watch-only observation for ``model``."""

        if cls.observes(model):
            return
        raise ValidationError("This model has no registered workflow observation.")

    @classmethod
    def dispatch(
        cls,
        model: Any,
        record: Any,
        *,
        created_batch: tuple[Any, ...] | None = None,
    ) -> None:
        """Capture one native save or a completed creation batch under target locks.

        Batch-aware capture runs on the final native send. All preceding rows
        have reached their ordinary receivers; admission and watch owners inspect
        their configuration once, retaining per-record scope and failure isolation.
        """
        if created_batch is not None:
            if not created_batch or record is not created_batch[-1]:
                return
            records = created_batch
        else:
            records = (record,)
        using = record._state.db
        try:
            with (
                database_context(using),
                transaction.atomic(using=using),
                system_context(reason="workflows.source_dispatch"),
            ):
                cls.validate_model(model)
                targets = [(item, generic_target(item, using=using)) for item in records]
                groups: dict[type[Model], set[Hashable]] = {}
                for _item, target in targets:
                    target_model = target.content_type.model_class()
                    assert target_model is not None
                    groups.setdefault(target_model, set()).add(target.object_id)
                present: set[tuple[type[Model], Hashable]] = set()
                for target_model, keys in sorted(groups.items(), key=lambda item: item[0]._meta.label_lower):
                    rows = target_model._default_manager.db_manager(using).order_by("pk").select_for_update(
                        no_key=True,
                        of=row_lock_paths(target_model),
                    )
                    for locked in by_key(rows, sorted(keys)):
                        # Native model selection keeps a column from every MTI
                        # ancestor, as Django requires for each requested OF lock.
                        present.update((target_model, row.pk) for row in locked)
                records = tuple(
                    item for item, target in targets if (target.content_type.model_class(), target.object_id) in present
                )
                events = apps.get_model("workflows", "TriggerEvent").objects.db_manager(using)
                for source in cls.registered():
                    events.record_changes(model, records, source=source.key)
                apps.get_model("workflows", "StepWatch").objects.db_manager(using).record_changes(records)
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

        if cls.watch_only:
            model = cls.model()
            post_save.connect(
                cls.changed,
                sender=model,
                weak=False,
                dispatch_uid=f"workflows.watch.{model._meta.label_lower}",
            )

    @classmethod
    def changed(
        cls,
        sender: Any,
        *,
        instance: Any,
        raw: bool = False,
        update_fields: frozenset[str] | None = None,
        created_batch: tuple[Any, ...] | None = None,
        **kwargs: Any,
    ) -> None:
        """Ignore fixture loads and saves that cannot change a watched predicate."""

        del kwargs
        if not raw and (update_fields is None or not cls.fields or set(cls.fields).intersection(update_fields)):
            cls.dispatch(sender, instance, created_batch=created_batch)


TriggerSourceField = ImplClassField
"""Historical import required by the host's materialized workflow migration."""


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
        connect_for_models(
            post_save,
            cls.changed,
            applies=lambda model: issubclass(model, RecordChangedOptIn),
            prepare=cls.validate_model,
            dispatch_uid="workflows.record_changed",
        )

    @classmethod
    def changed(
        cls,
        sender: Any,
        *,
        instance: Any,
        raw: bool = False,
        created_batch: tuple[Any, ...] | None = None,
        **kwargs: Any,
    ) -> None:
        """Ignore fixture loading and delegate all durable capture to its owner."""
        if not raw:
            cls.dispatch(sender, instance, created_batch=created_batch)


def check_record_changed_models(**kwargs: Any) -> list[checks.CheckMessage]:
    """Reject opted-in models lacking their required source grant declaration."""
    del kwargs
    inherited = getattr_static(RecordChangedOptIn, "record_changed_grant_targets")
    return [
        checks.Error(
            f"{model._meta.label} must implement record_changed_grant_targets().",
            obj=model,
            id="workflows.E001",
        )
        for model in apps.get_models()
        if issubclass(model, RecordChangedOptIn) and getattr_static(model, "record_changed_grant_targets") is inherited
    ]
