"""Native IAM target admission contributed to neutral generic record edges."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar, cast

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models
from django.utils.text import capfirst
from rebac import system_context
from rebac.field_backing import canonical_model
from rebac.resources import model_for_resource_type, model_resource_type
from rebac.schema import Definition, FieldBinding

from angee.base.models.refs import RecordRefMixin
from angee.iam.permissions import effective_rebac_definition


class ProtectedRecordRefMixin(RecordRefMixin, concrete=False):
    """Keep native protected edge admission with its optional IAM owner."""

    accepts_any_record_target: ClassVar[bool] = False

    class Meta:
        abstract = True

    @classmethod
    def declared_target_models(
        cls,
        *,
        definition_for: Callable[[type[models.Model]], Definition | None] = effective_rebac_definition,
    ) -> dict[str, type[models.Model]]:
        """Return the target models this edge's effective schema declares, by ``label_lower``.

        One relation per target type, backed by the generic pointer
        (``// rebac:field=target``), names the types the edge may hold under an
        actor. Owners derive their accepted-target sets from this one
        declaration instead of listing the types again.
        """

        definition = definition_for(cls)
        if definition is None:
            raise ImproperlyConfigured(f"{cls._meta.label} declares no REBAC definition to read target types from.")
        return cls._declared_target_models(definition)

    @classmethod
    def _declared_target_models(cls, definition: Definition) -> dict[str, type[models.Model]]:
        """Project one already-resolved effective definition through its pointer field."""

        pointer = cls.record_ref_field().name
        targets: dict[str, type[models.Model]] = {}
        for relation in definition.relations:
            backing = relation.backing
            if not isinstance(backing, FieldBinding) or backing.path != pointer:
                continue
            model = model_for_resource_type(relation.allowed_subjects[0].type)
            if model is not None:
                targets[model._meta.label_lower] = model
        return targets

    @classmethod
    def declared_target_model(cls, model_label: str) -> type[models.Model]:
        """Return the declared target a ``app_label.model`` label names, or a ``ValidationError``.

        A label names its canonical type, as :meth:`declares_target` decides: a
        multi-table child (a person) addresses its typed parent (a party).
        """

        try:
            model = apps.get_model(str(model_label).strip())
        except (LookupError, ValueError) as error:
            raise cls._undeclared_target(cls.declared_target_models()) from error
        if not cls.declares_target(model):
            raise cls._undeclared_target(cls.declared_target_models())
        return cast(type[models.Model], canonical_model(model))

    @classmethod
    def accepted_target_models(
        cls,
        *,
        definition_for: Callable[[type[models.Model]], Definition | None] = effective_rebac_definition,
    ) -> dict[str, type[models.Model]]:
        """Return the complete target set from the schema or the explicit open edge."""

        if not cls.accepts_any_record_target:
            definition = definition_for(cls)
            return (
                cls._declared_target_models(definition) if definition is not None else super().accepted_target_models()
            )
        targets = {
            target._meta.label_lower: target
            for model in cls._meta.apps.get_models()
            if (target := canonical_model(model)) is not None
        }
        return dict(sorted(targets.items()))

    @classmethod
    def declares_target(cls, model: type[models.Model]) -> bool:
        """Whether this edge's schema declares ``model``'s canonical type a target.

        A record of an undeclared type cannot carry the edge under an actor: the
        edge's ``create`` has no arm for it and :meth:`validate_target` refuses it.
        """

        target = canonical_model(model)
        return target is not None and target._meta.label_lower in cls.accepted_target_models()

    @classmethod
    def validate_target(cls, target: models.Model) -> None:
        """Reject a target whose canonical model the edge's schema does not declare."""

        super().validate_target(target)
        if effective_rebac_definition(cls) is not None and not cls.declares_target(type(target)):
            raise cls._undeclared_target(cls.declared_target_models())

    @classmethod
    def _undeclared_target(cls, declared: dict[str, type[models.Model]]) -> ValidationError:
        names = ", ".join(sorted(str(model._meta.verbose_name) for model in declared.values()))
        return ValidationError({"target": f"{capfirst(str(cls._meta.verbose_name))} may target only: {names}."})

    @classmethod
    def remove_target_refs(cls, rows: models.QuerySet[Any]) -> None:
        with system_context(reason="record_ref.target_delete"):
            super().remove_target_refs(rows)

    @property
    def record_resource_type(self) -> str:
        """Project the target's native permission identity outside its neutral value."""
        label = self.record_ref.model_label
        return (model_resource_type(apps.get_model(label)) or "") if label else ""


def record_edges_by_target(*, definition_for=effective_rebac_definition):
    """Project authored target policy for compiler metadata without querying rows."""
    index = {}
    for edge in sorted(apps.get_models(), key=lambda model: model._meta.label):
        if not issubclass(edge, ProtectedRecordRefMixin):
            continue
        if edge.accepts_any_record_target:
            targets = edge.accepted_target_models(definition_for=definition_for)
        else:
            definition = definition_for(edge)
            if definition is None:
                continue
            targets = edge._declared_target_models(definition)
        for target in targets.values():
            index.setdefault(target, []).append(edge)
    return {target: tuple(edges) for target, edges in index.items()}


class IAMRecordRefMixin(ProtectedRecordRefMixin, extends="base.RecordRefMixin"):
    """Donate native target admission without becoming an authored source ancestor."""

    class Meta:
        abstract = True
