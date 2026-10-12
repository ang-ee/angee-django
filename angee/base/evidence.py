"""Frozen evidence facts, retained derivation links, and evidence readability."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Any

from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models
from pydantic import BaseModel, ConfigDict, JsonValue
from rebac.resources import model_resource_type

from angee.base.identity import instances_from_public_ids
from angee.base.mixins import AppendOnlyModel
from angee.base.models import AngeeDataModel
from angee.base.refs import MergePolicy, RecordRefMixin
from angee.base.scoping import read_scoped_queryset


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


def _readable(
    refs: Sequence[EvidenceReference], actor: Any, *, permission: str, standing: bool
) -> dict[EvidenceReference, Any]:
    """Resolve the records ``actor`` holds ``permission`` on, one scoped query per model.

    An unknown model label is a broken store and fails fast. A model without a
    REBAC resource type has no standing permission: admission (``standing``)
    refuses it, display simply finds nothing readable there.
    """

    grouped: dict[str, set[str]] = {}
    for ref in refs:
        grouped.setdefault(ref.model.lower(), set()).add(ref.id)
    resolved: dict[EvidenceReference, Any] = {}
    for label, ids in sorted(grouped.items()):
        try:
            model = apps.get_model(label)
        except (LookupError, ValueError) as error:
            raise ValidationError({"context": "Unknown referenced model."}) from error
        if not model_resource_type(model):
            if standing:
                raise PermissionDenied("Referenced records require a standing permission.")
            continue
        found = instances_from_public_ids(
            model, sorted(ids), queryset=read_scoped_queryset(model, actor, action=permission)
        )
        resolved.update((EvidenceReference(model=label, id=public_id), record) for public_id, record in found.items())
    return resolved


def readable_evidence(
    refs: Sequence[EvidenceReference],
    actor: Any,
    *,
    permission: str = "read",
) -> list[EvidenceReference]:
    """Return the references whose records ``actor`` may read, in their stored order."""

    readable = _readable(refs, actor, permission=permission, standing=False)
    return [ref for ref in refs if EvidenceReference(model=ref.model.lower(), id=ref.id) in readable]


def readable_records(
    refs: Sequence[EvidenceReference],
    actors: Sequence[Any],
    *,
    permission: str = "read",
) -> list[Any]:
    """Admit references only when every actor holds ``permission`` on every record."""

    if not actors:
        raise ValidationError("Evidence admission requires a reader.")
    wanted = {EvidenceReference(model=ref.model.lower(), id=ref.id) for ref in refs}
    records: dict[EvidenceReference, Any] = {}
    for actor in actors:
        records = _readable(refs, actor, permission=permission, standing=True)
        if set(records) != wanted:
            raise PermissionDenied("Every participant requires the declared permission on every referenced record.")
    return [records[ref] for ref in sorted(wanted, key=lambda ref: (ref.model, ref.id))]


class DerivedFrom(AppendOnlyModel, RecordRefMixin, AngeeDataModel):
    """One retained edge from an evidence owner to a canonical source record."""

    merge_policy = MergePolicy.KEEP

    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.PositiveBigIntegerField()
    record = GenericForeignKey("content_type", "object_id")

    class Meta:
        abstract = True
