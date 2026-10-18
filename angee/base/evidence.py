"""Frozen evidence facts, retained derivation links, and evidence readability."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Any

from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models
from pydantic import BaseModel, ConfigDict, JsonValue
from rebac.resources import model_resource_type

from angee.base.identity import instances_from_public_ids, public_id_for
from angee.base.refs import generic_pointer_model


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
    refs: Sequence[EvidenceReference], actor: Any, *, permission: str, standing: bool
) -> dict[EvidenceReference, Any]:
    """Resolve the records ``actor`` holds ``permission`` on, one scoped query per model.

    An unknown model label is a broken store and fails fast. A model without a
    REBAC resource type has no standing permission: admission (``standing``)
    refuses it, display simply finds nothing readable there.
    """

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
            queryset=model.objects.with_actor(actor).with_action(permission),
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
) -> list[EvidenceReference]:
    """Return the references whose records ``actor`` may read, in their stored order."""

    readable = _readable(refs, actor, permission=permission, standing=False)
    return [
        ref
        for ref in refs
        if EvidenceReference(model=ref.model.lower(), id=ref.id, pointer=ref.pointer) in readable
    ]


def readable_evidence_records(
    refs: Sequence[EvidenceReference],
    actor: Any,
    *,
    permission: str = "read",
) -> list[tuple[EvidenceReference, models.Model]]:
    """Return readable references with their already batched records, in stored order."""

    readable = _readable(refs, actor, permission=permission, standing=False)
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
) -> list[Any]:
    """Admit references only when every actor holds ``permission`` on every record."""

    if not actors:
        raise ValidationError("Evidence admission requires a reader.")
    wanted = {
        EvidenceReference(model=ref.model.lower(), id=ref.id, pointer=ref.pointer)
        for ref in refs
    }
    records: dict[EvidenceReference, Any] = {}
    for actor in actors:
        records = _readable(refs, actor, permission=permission, standing=True)
        if set(records) != wanted:
            raise PermissionDenied("Every participant requires the declared permission on every referenced record.")
    return [records[ref] for ref in sorted(wanted, key=lambda ref: (ref.model, ref.id, ref.pointer))]
