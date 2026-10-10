"""Frozen evidence facts, retained derivation links, and admission readability."""

from collections.abc import Sequence
from enum import StrEnum
from typing import Any

from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DEFAULT_DB_ALIAS, models
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


class DerivedFrom(AppendOnlyModel, RecordRefMixin, AngeeDataModel):
    """One retained edge from an evidence owner to a canonical source record."""

    merge_policy = MergePolicy.KEEP

    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.PositiveBigIntegerField()
    record = GenericForeignKey("content_type", "object_id")

    class Meta:
        abstract = True


class DerivedFromRelation(GenericRelation):
    """Query retained derivation links without collecting them on source deletion.

    Declare on the canonical source model, matching the links' content type.
    """

    def bulk_related_objects(self, objs: Sequence[models.Model], using: str = DEFAULT_DB_ALIAS) -> list[models.Model]:
        return []


class EvidenceReference(BaseModel):
    """Frozen public identity required by evidence admission."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    model: str
    id: str


def readable_records(
    refs: Sequence[EvidenceReference], actors: Sequence[Any], *, permission: str = "read",
) -> list[Any]:
    """Check a standing permission in one scoped query per actor and model."""

    if not actors:
        raise ValidationError("Evidence admission requires a reader.")
    grouped: dict[str, set[str]] = {}
    for ref in refs:
        grouped.setdefault(ref.model.lower(), set()).add(ref.id)
    records: list[Any] = []
    for label, ids in sorted(grouped.items()):
        try:
            model = apps.get_model(label)
        except (LookupError, ValueError) as error:
            raise ValidationError({"context": "Unknown referenced model."}) from error
        if not model_resource_type(model):
            raise PermissionDenied("Referenced records require a standing permission.")
        for actor in actors:
            scoped = read_scoped_queryset(model, actor, action=permission)
            found = instances_from_public_ids(model, ids, queryset=scoped)
            if set(found) != ids:
                raise PermissionDenied("Every participant requires the declared permission on every referenced record.")
        records.extend(found.values())
    return records
