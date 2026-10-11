"""Frozen evidence facts, retained derivation links, and admission readability."""

from collections.abc import Sequence
from enum import StrEnum

from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.db import DEFAULT_DB_ALIAS, models
from pydantic import BaseModel, ConfigDict, JsonValue

from angee.base.identity import EvidenceReference as EvidenceReference
from angee.base.identity import readable_records as readable_records
from angee.base.mixins import AppendOnlyModel
from angee.base.models import AngeeDataModel
from angee.base.refs import MergePolicy, RecordRefMixin


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
