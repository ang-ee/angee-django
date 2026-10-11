"""Concrete probes for :class:`~angee.base.refs.RecordRefMixin` targets and edges, and for merge moves."""

from __future__ import annotations

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models.functions import Lower

from angee.base.mixins import SqidMixin
from angee.base.models import AngeeModel
from angee.base.refs import RecordRefMixin


class RecordRefTypedTarget(SqidMixin, AngeeModel):
    """Concrete sqid-backed target with a REBAC resource type."""

    sqid_prefix = "rrt_"
    name = models.CharField(max_length=32)

    class Meta:
        """Django model options for the typed target."""

        db_table = "test_record_ref_typed_target"
        rebac_resource_type = "tests/record-ref-target"


class RecordRefPlainTarget(SqidMixin, models.Model):
    """Concrete sqid-backed target without a REBAC resource type."""

    sqid_prefix = "rrp_"
    name = models.CharField(max_length=32)

    class Meta:
        """Django model options for the plain target."""

        db_table = "test_record_ref_plain_target"


class RecordRefTargetEdge(RecordRefMixin, models.Model):
    """Concrete target edge used by record-ref tests."""

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey("content_type", "object_id")

    class Meta:
        """Django model options for the target edge."""

        db_table = "test_record_ref_target_edge"


class RecordRefSubjectEdge(RecordRefMixin, models.Model):
    """Concrete subject edge used by record-ref tests."""

    subject_content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    subject_object_id = models.PositiveBigIntegerField()
    subject = GenericForeignKey("subject_content_type", "subject_object_id")

    class Meta:
        """Django model options for the subject edge."""

        db_table = "test_record_ref_subject_edge"


class RecordRefCustomEdge(RecordRefMixin, models.Model):
    """Concrete reference with independently named relation fields and columns."""

    kind = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+", db_column="kind_column")
    key = models.PositiveBigIntegerField(db_column="key_column")
    linked = GenericForeignKey("kind", "key")

    class Meta:
        """Django model options for the custom reference edge."""

        db_table = "test_record_ref_custom_edge"


class RecordRefNullableEdge(RecordRefMixin, models.Model):
    """Concrete nullable edge used by empty-reference tests."""

    content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    object_id = models.PositiveBigIntegerField(null=True, blank=True)
    target = GenericForeignKey("content_type", "object_id")

    class Meta:
        """Django model options for the nullable edge."""

        db_table = "test_record_ref_nullable_edge"


class CustomColumnEdge(RecordRefMixin, models.Model):
    """An alternate column adopter also requires native string-id conversion."""

    target_ct = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.CASCADE)
    target_id = models.CharField(max_length=64, null=True, blank=True)
    target = GenericForeignKey("target_ct", "target_id")

    class Meta:
        """Django model options for the string-keyed edge."""

        db_table = "test_record_ref_custom_columns"


class SelfLinkedNode(models.Model):
    """A record linked to records of its own model one way (``follows``) and both ways (``peers``)."""

    follows = models.ManyToManyField("self", symmetrical=False, related_name="followers")
    peers = models.ManyToManyField("self")

    class Meta:
        """Django model options for the self-linked merge probe."""

        db_table = "test_merge_self_linked_node"


class TagSlot(models.Model):
    """A row unique on an expression over its tag key, which no field set describes."""

    tag = models.ForeignKey("tags.Tag", on_delete=models.CASCADE, related_name="+")
    label = models.CharField(max_length=32)

    class Meta:
        """Django model options for the expression-constrained merge probe."""

        db_table = "test_merge_tag_slot"
        constraints = (models.UniqueConstraint(Lower("label"), "tag", name="uq_test_merge_tag_slot_label"),)
