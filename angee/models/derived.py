"""The retained derivation edge: an evidence owner's pointer at a canonical source record.

Lives beside :mod:`angee.models.base` rather than in it because it composes
:class:`angee.refs.RecordRefMixin`, and :mod:`angee.refs` composes this package: the
package exposes ``DerivedFrom`` lazily (``angee.models.__getattr__``) so a fresh process may
import either side first.
"""

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from angee.models.append_only import AppendOnlyModel
from angee.models.base import AngeeDataModel
from angee.refs import MergePolicy, RecordRefMixin


class DerivedFrom(AppendOnlyModel, RecordRefMixin, AngeeDataModel):
    """One retained edge from an evidence owner to a canonical source record."""

    merge_policy = MergePolicy.KEEP

    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.PositiveBigIntegerField()
    record = GenericForeignKey("content_type", "object_id")

    class Meta:
        abstract = True
