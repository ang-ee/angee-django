"""Frozen evidence facts, retained derivation links, and evidence readability."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, cast

from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from pydantic import BaseModel, ConfigDict, JsonValue

from angee.base.models.identity import instances_from_public_ids, public_id_for
from angee.base.refs import generic_pointer_model
from angee.provenance.engine.policy import get_policy


class FactAuthority(StrEnum):
    """The closed provenance vocabulary of a frozen fact."""

    SOURCE = "source"
    CORRECTION = "correction"
    UNVERIFIED = "unverified"


class EvidenceFact(BaseModel):
    """A retained value and the authority asserted for it."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    pointer: str
    label: str
    value: JsonValue
    authority: FactAuthority


class EvidenceReference(BaseModel):
    """Frozen public identity of one origin record."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    model: str
    id: str
    pointer: str = ""

    @classmethod
    def of(cls, record: models.Model, *, pointer: str = "") -> EvidenceReference:
        """Name a saved record by the model its generic pointers resolve to and its public id."""

        pointer_model = generic_pointer_model(type(record))
        return cls(model=pointer_model._meta.label, id=public_id_for(pointer_model, record.pk), pointer=pointer)


def _readable(
    refs: Sequence[EvidenceReference],
    actor: Any,
    *,
    permission: str,
    standing: bool,
    using: str | None = None,
) -> dict[EvidenceReference, Any]:
    """Resolve the records ``actor`` holds ``permission`` on, one scoped query per model.

    An unknown model label is a broken store and fails fast. A model without a
    REBAC resource type has no standing permission: admission (``standing``)
    refuses it, display simply finds nothing readable there.
    """

    from rebac.resources import model_resource_type

    from angee.iam.permissions import require_rebac_queryset

    grouped: dict[str, list[EvidenceReference]] = {}
    for ref in refs:
        grouped.setdefault(ref.model.lower(), []).append(ref)
    resolved: dict[EvidenceReference, Any] = {}
    for label, labelled in sorted(grouped.items()):
        try:
            model = apps.get_model(label)
        except (LookupError, ValueError) as error:
            raise ValidationError({"context": "Unknown referenced model."}) from error
        if not model_resource_type(model):
            if standing:
                raise PermissionDenied("Referenced records require a standing permission.")
            continue
        ids = sorted({ref.id for ref in labelled})
        found = instances_from_public_ids(
            model,
            ids,
            queryset=require_rebac_queryset(model).using(using).with_actor(actor).with_action(permission),
        )
        resolved.update(
            (
                EvidenceReference(model=label, id=ref.id, pointer=ref.pointer),
                found[ref.id],
            )
            for ref in labelled
            if ref.id in found
        )
    return resolved


def readable_evidence(
    refs: Sequence[EvidenceReference],
    actor: Any,
    *,
    permission: str = "read",
    using: str | None = None,
) -> list[EvidenceReference]:
    """Return the references whose records ``actor`` may read, in their stored order."""

    readable = _readable(refs, actor, permission=permission, standing=False, using=using)
    return [
        ref for ref in refs if EvidenceReference(model=ref.model.lower(), id=ref.id, pointer=ref.pointer) in readable
    ]


def readable_evidence_records(
    refs: Sequence[EvidenceReference],
    actor: Any,
    *,
    permission: str = "read",
    using: str | None = None,
) -> list[tuple[EvidenceReference, models.Model]]:
    """Return readable references with their already batched records, in stored order."""

    readable = _readable(refs, actor, permission=permission, standing=False, using=using)
    result = []
    for ref in refs:
        normalized = EvidenceReference(model=ref.model.lower(), id=ref.id, pointer=ref.pointer)
        if record := readable.get(normalized):
            result.append((ref, record))
    return result


def readable_records(
    refs: Sequence[EvidenceReference],
    actors: Sequence[Any],
    *,
    permission: str = "read",
    using: str | None = None,
) -> list[Any]:
    """Admit references only when every actor holds ``permission`` on every record."""

    if not actors:
        raise ValidationError("Evidence admission requires a reader.")
    wanted = {EvidenceReference(model=ref.model.lower(), id=ref.id, pointer=ref.pointer) for ref in refs}
    records: dict[EvidenceReference, Any] = {}
    for actor in actors:
        records = _readable(refs, actor, permission=permission, standing=True, using=using)
        if set(records) != wanted:
            raise PermissionDenied("Every participant requires the declared permission on every referenced record.")
    return [records[ref] for ref in sorted(wanted, key=lambda ref: (ref.model, ref.id, ref.pointer))]


@dataclass(frozen=True)
class RetainedEvidenceReference:
    """A retained Django model identity, not an authorization grant."""

    model: str
    pk: str

    @classmethod
    def from_record(cls, record: models.Model) -> RetainedEvidenceReference:
        if record.pk is None or record._state.adding:
            raise ValidationError("Evidence records must be persisted.")
        return cls(record._meta.label_lower, str(record.pk))

    def resolve(self, *, actor: object, using: str | None = None) -> models.Model:
        model = apps.get_model(self.model)
        record = model._base_manager.db_manager(using).get(pk=self.pk)
        get_policy().authorize(actor, "view", record)
        return cast(models.Model, record)


@dataclass(frozen=True)
class RetainedEvidenceFact:
    """A JSON-compatible assertion retained with its optional source reference."""

    value: Any
    source: RetainedEvidenceReference | None = None


def evidence_values(values: object) -> list[dict[str, Any]]:
    """Validate evidence before any assertion is written."""
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise ValidationError("Evidence must be a sequence of references or facts.")
    encoded: list[dict[str, Any]] = []
    for value in values:
        if isinstance(value, models.Model):
            value = RetainedEvidenceReference.from_record(value)
        if isinstance(value, RetainedEvidenceReference):
            encoded.append({"reference": asdict(value)})
        elif isinstance(value, RetainedEvidenceFact):
            encoded.append({"fact": asdict(value)})
        elif isinstance(value, dict):
            encoded.append(value)
        else:
            raise ValidationError("Evidence requires a typed reference or JSON fact mapping.")
    try:
        return cast(list[dict[str, Any]], json.loads(json.dumps(encoded, cls=DjangoJSONEncoder)))
    except (TypeError, ValueError) as error:
        raise ValidationError("Evidence facts must be JSON-compatible.") from error
