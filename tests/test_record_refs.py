"""Tests for generic contenttypes record references."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import connection, models
from django.test.utils import CaptureQueriesContext
from rebac import system_context

from angee.base.mixins import SqidMixin
from angee.base.models import AngeeModel
from angee.base.refs import RecordRef, RecordRefMixin, record_ref_for
from tests.conftest import _clear_model_tables, _create_missing_tables


class RecordRefTypedTarget(SqidMixin, AngeeModel):
    """Concrete sqid-backed target with a REBAC resource type."""

    sqid_prefix = "rrt_"
    name = models.CharField(max_length=32)

    class Meta:
        """Django model options for the typed target."""

        app_label = "auth"
        db_table = "test_record_ref_typed_target"
        rebac_resource_type = "tests/record-ref-target"


class RecordRefPlainTarget(SqidMixin, models.Model):
    """Concrete sqid-backed target without a REBAC resource type."""

    sqid_prefix = "rrp_"
    name = models.CharField(max_length=32)

    class Meta:
        """Django model options for the plain target."""

        app_label = "auth"
        db_table = "test_record_ref_plain_target"


class RecordRefTargetEdge(RecordRefMixin, models.Model):
    """Concrete default-prefix edge used by record-ref tests."""

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey("content_type", "object_id")

    class Meta:
        """Django model options for the default-prefix edge."""

        app_label = "auth"
        db_table = "test_record_ref_target_edge"


class RecordRefSubjectEdge(RecordRefMixin, models.Model):
    """Concrete subject-prefix edge used by record-ref tests."""

    record_ref_field_prefix = "subject"

    subject_content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    subject_object_id = models.PositiveBigIntegerField()
    subject = GenericForeignKey("subject_content_type", "subject_object_id")

    class Meta:
        """Django model options for the subject-prefix edge."""

        app_label = "auth"
        db_table = "test_record_ref_subject_edge"


class RecordRefNullableEdge(RecordRefMixin, models.Model):
    """Concrete nullable edge used by empty-reference tests."""

    content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    object_id = models.PositiveBigIntegerField(null=True, blank=True)
    target = GenericForeignKey("content_type", "object_id")

    class Meta:
        """Django model options for the nullable edge."""

        app_label = "auth"
        db_table = "test_record_ref_nullable_edge"


RECORD_REF_TEST_MODELS = (
    RecordRefTypedTarget,
    RecordRefPlainTarget,
    RecordRefTargetEdge,
    RecordRefSubjectEdge,
    RecordRefNullableEdge,
)


@pytest.fixture()
def record_ref_tables(transactional_db: Any) -> Any:
    """Create the concrete test tables."""

    del transactional_db
    created_models = _create_missing_tables(RECORD_REF_TEST_MODELS)
    try:
        yield
    finally:
        _clear_model_tables(RECORD_REF_TEST_MODELS)
        with connection.schema_editor() as schema_editor:
            for model in reversed(created_models):
                schema_editor.delete_model(model)


def test_record_ref_for_instance_projects_identity_and_rebac_type(record_ref_tables: None) -> None:
    """A direct instance reference delegates ID and REBAC facts to their owners."""

    del record_ref_tables
    with system_context(reason="record-ref typed target"):
        typed = RecordRefTypedTarget.objects.create(name="typed")
    plain = RecordRefPlainTarget.objects.create(name="plain")

    assert record_ref_for(typed) == RecordRef(
        model_label="auth.RecordRefTypedTarget",
        object_id=typed.pk,
        public_id=typed.public_id,
        resource_type="tests/record-ref-target",
    )
    assert record_ref_for(plain) == RecordRef(
        model_label="auth.RecordRefPlainTarget",
        object_id=plain.pk,
        public_id=plain.sqid,
        resource_type="",
    )


def test_record_ref_mixin_projects_default_target_fields_with_cached_contenttype(
    record_ref_tables: None,
) -> None:
    """Default-prefix edges project target facts without a second ContentType query."""

    del record_ref_tables
    with system_context(reason="record-ref typed target"):
        target = RecordRefTypedTarget.objects.create(name="typed")
    edge = RecordRefTargetEdge.objects.create(
        content_type=ContentType.objects.get_for_model(RecordRefTypedTarget),
        object_id=target.pk,
    )
    edge = RecordRefTargetEdge.objects.get(pk=edge.pk)

    ContentType.objects.clear_cache()
    with CaptureQueriesContext(connection) as first_access:
        record_ref = edge.record_ref

    assert record_ref == RecordRef(
        model_label="auth.RecordRefTypedTarget",
        object_id=target.pk,
        public_id=target.public_id,
        resource_type="tests/record-ref-target",
    )
    assert any("django_content_type" in query["sql"] for query in first_access.captured_queries)

    with CaptureQueriesContext(connection) as second_access:
        assert edge.record_ref == record_ref

    assert len(second_access) == 0
    assert edge.record_model_label == "auth.RecordRefTypedTarget"
    assert edge.record_public_id == target.public_id


def test_record_ref_mixin_keeps_resource_type_empty_for_untyped_targets(record_ref_tables: None) -> None:
    """Targets without a REBAC type expose an empty resource type."""

    del record_ref_tables
    target = RecordRefPlainTarget.objects.create(name="plain")
    edge = RecordRefTargetEdge.objects.create(
        content_type=ContentType.objects.get_for_model(RecordRefPlainTarget),
        object_id=target.pk,
    )

    assert edge.record_ref.resource_type == ""


def test_record_ref_mixin_returns_empty_ref_for_unset_pointer(record_ref_tables: None) -> None:
    """An unset content type returns an empty reference while preserving object id."""

    del record_ref_tables
    edge = RecordRefNullableEdge.objects.create(content_type=None, object_id=42)

    assert edge.record_ref == RecordRef(model_label="", object_id=42, public_id="", resource_type="")


def test_record_ref_mixin_returns_empty_ref_for_stale_contenttype(record_ref_tables: None) -> None:
    """A content type whose model no longer exists returns the empty reference."""

    del record_ref_tables
    stale_content_type = ContentType.objects.create(app_label="missing", model="recordrefghost")
    edge = RecordRefTargetEdge.objects.create(content_type=stale_content_type, object_id=43)

    assert edge.record_ref == RecordRef(model_label="", object_id=43, public_id="", resource_type="")


def test_record_ref_mixin_supports_subject_field_prefix(record_ref_tables: None) -> None:
    """The field-prefix override reads subject_* columns instead of target columns."""

    del record_ref_tables
    with system_context(reason="record-ref typed target"):
        target = RecordRefTypedTarget.objects.create(name="typed")
    edge = RecordRefSubjectEdge.objects.create(
        subject_content_type=ContentType.objects.get_for_model(RecordRefTypedTarget),
        subject_object_id=target.pk,
    )

    assert edge.record_ref == RecordRef(
        model_label="auth.RecordRefTypedTarget",
        object_id=target.pk,
        public_id=target.public_id,
        resource_type="tests/record-ref-target",
    )
    assert edge.record_model_label == "auth.RecordRefTypedTarget"
    assert edge.record_public_id == target.public_id
    assert not hasattr(edge, "subject_model_label")
    assert not hasattr(edge, "subject_public_id")
