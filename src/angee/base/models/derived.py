"""The retained derivation edge from an evidence owner to a canonical source record."""

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from angee.base.models.append_only import AppendOnlyModel
from angee.base.models.base import AngeeDataModel
from angee.base.models.merge_references import MergePolicy
from angee.base.models.refs import RecordRefMixin


class DerivedFrom(AppendOnlyModel, RecordRefMixin, AngeeDataModel):
    """One retained edge from an evidence owner to a canonical source record."""

    merge_policy = MergePolicy.KEEP

    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.PositiveBigIntegerField()
    record = GenericForeignKey("content_type", "object_id")

    class Meta:
        abstract = True
