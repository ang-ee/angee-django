"""Tests for generic contenttypes record references."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import connection, models
from django.test.utils import CaptureQueriesContext, isolate_apps
from django.utils.module_loading import import_string
from rebac import ObjectRef, generic_target, system_context
from rebac.resources import model_resource_type

from angee.base.mixins import SqidMixin
from angee.base.models import AngeeModel
from angee.base.refs import (
    RecordRef,
    RecordRefMixin,
    ancestor_object_refs,
    concrete_child,
    concrete_child_accessor,
    concrete_child_models,
    generic_pointer_model,
    generic_pointer_target,
    is_record_target_model,
    record_ref_for,
)
from angee.projects.testing.models import Link
from tests.conftest import FileAttachment
from tests.mtidemo.models import (
    MtiChild,
    MtiChildProxy,
    MtiParent,
    MtiParentProxy,
)
from tests.tables import model_tables


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
    """Concrete target edge used by record-ref tests."""

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey("content_type", "object_id")

    class Meta:
        """Django model options for the target edge."""

        app_label = "auth"
        db_table = "test_record_ref_target_edge"


class RecordRefSubjectEdge(RecordRefMixin, models.Model):
    """Concrete subject edge used by record-ref tests."""

    subject_content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    subject_object_id = models.PositiveBigIntegerField()
    subject = GenericForeignKey("subject_content_type", "subject_object_id")

    class Meta:
        """Django model options for the subject edge."""

        app_label = "auth"
        db_table = "test_record_ref_subject_edge"


class RecordRefCustomEdge(RecordRefMixin, models.Model):
    """Concrete reference with independently named relation fields and columns."""

    kind = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+", db_column="kind_column")
    key = models.PositiveBigIntegerField(db_column="key_column")
    linked = GenericForeignKey("kind", "key")

    class Meta:
        """Django model options for the custom reference edge."""

        app_label = "auth"
        db_table = "test_record_ref_custom_edge"


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
    RecordRefCustomEdge,
    RecordRefNullableEdge,
)


@pytest.fixture()
def record_ref_tables(transactional_db: Any) -> Any:
    """Create the concrete test tables."""

    del transactional_db
    with model_tables(RECORD_REF_TEST_MODELS):
        yield


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
    """Target edges project reference facts without a second ContentType query."""

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


def test_record_ref_mixin_uses_the_declared_subject_relation(record_ref_tables: None) -> None:
    """The generic foreign key owns the subject pointer's field names."""

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


def test_record_ref_mixin_uses_arbitrary_field_and_column_names(record_ref_tables: None) -> None:
    """Neither the relation name nor SQL columns dictate the pointer's attributes."""

    del record_ref_tables
    with system_context(reason="record-ref custom target"):
        target = RecordRefTypedTarget.objects.create(name="typed")
    edge = RecordRefCustomEdge.objects.create(linked=target)
    edge = RecordRefCustomEdge.objects.get(pk=edge.pk)

    with CaptureQueriesContext(connection) as queries:
        assert edge.record_ref == record_ref_for(target)

    assert len(queries) == 0


@pytest.mark.parametrize(
    ("source_path", "reference_name"),
    [
        ("angee.integrate.models.RecordLink", "target"),
        ("angee.knowledge.models.RecordBinding", "target"),
        ("angee.messaging.models.ThreadAttachment", "target"),
        ("angee.money.models.CurrencyRate", "context"),
        ("angee.portfolio.models.Update", "target"),
        ("angee.projects.models.ProjectBinding", "target"),
        ("angee.projects.models.Link", "target"),
        ("angee.storage.models.FileAttachment", "target"),
        ("angee.tags.models.TagAssignment", "target"),
        ("angee.workflows.models.WorkflowRun", "subject"),
    ],
)
@isolate_apps()
def test_record_ref_mixin_projects_each_source_addon_reference(
    record_ref_tables: None, source_path: str, reference_name: str,
) -> None:
    """Each source relation projects correctly after Django clones its abstract fields."""

    del record_ref_tables
    source_model = import_string(source_path)

    class ConcreteReference(source_model):
        """Materialize the source's native reference fields for this projection probe."""

        class Meta:
            """Keep each probe in its own isolated app registry."""

            app_label = "auth"

    target = RecordRefPlainTarget.objects.create(name="plain")
    # Unrelated FKs need no composed targets for this unsaved reference probe.
    unrelated_relations = {field.attname: None for field in ConcreteReference._meta.fields if field.is_relation}
    edge = ConcreteReference(**unrelated_relations, **{reference_name: target})

    assert edge.record_ref == record_ref_for(target)


@isolate_apps()
def test_record_ref_mixin_rejects_a_missing_generic_foreign_key() -> None:
    """A missing relation is an invalid declaration, not an empty record pointer."""

    class MissingReference(RecordRefMixin):
        """Invalid reference model without a generic relation."""

        class Meta:
            """Keep this declaration in the isolated app registry."""

            app_label = "auth"

    errors = MissingReference.check()
    assert [(error.id, error.msg) for error in errors if error.id == "angee.E029"] == [
        ("angee.E029", "auth.MissingReference must declare exactly one GenericForeignKey for RecordRefMixin; found 0.")
    ]


@isolate_apps()
def test_record_ref_mixin_rejects_ambiguous_generic_foreign_keys() -> None:
    """Two generic relations cannot silently choose which record to project."""

    class AmbiguousReference(RecordRefMixin):
        """Invalid reference model with two otherwise valid generic relations."""

        content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
        object_id = models.PositiveBigIntegerField()
        first = GenericForeignKey()
        second = GenericForeignKey()

        class Meta:
            """Keep this declaration in the isolated app registry."""

            app_label = "auth"

    errors = AmbiguousReference.check()
    assert [(error.id, error.msg) for error in errors if error.id == "angee.E029"] == [
        (
            "angee.E029",
            "auth.AmbiguousReference must declare exactly one GenericForeignKey for RecordRefMixin; found 2.",
        )
    ]


@isolate_apps()
def test_record_ref_mixin_rejects_obsolete_prefix_declarations() -> None:
    """A stale consumer declaration fails startup instead of silently doing nothing."""

    class StaleReference(RecordRefMixin):
        """A valid pointer with an obsolete independent naming declaration."""

        record_ref_field_prefix = "subject_"
        content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
        object_id = models.PositiveBigIntegerField()
        target = GenericForeignKey()

        class Meta:
            app_label = "auth"

    errors = StaleReference.check()
    assert [error.id for error in errors if error.id.startswith("angee.")] == ["angee.E030"]


def test_record_ref_mixin_accepts_its_declared_generic_pointer() -> None:
    """Valid relation declarations have no reference-specific startup error."""

    assert not [error for error in RecordRefCustomEdge.check() if error.id.startswith("angee.")]


def test_record_target_models_are_the_rebac_typed_canonical_models() -> None:
    """Only rows a polymorphic edge can name need delete-time care for their edges."""

    assert is_record_target_model(MtiChild) and is_record_target_model(MtiChildProxy)
    assert is_record_target_model(RecordRefTypedTarget)
    assert not is_record_target_model(RecordRefPlainTarget)


def test_declared_target_models_read_the_schema_target_relations(composed_permissions: None) -> None:
    """An edge's accepted target types are the schema's `target`-backed relations, by label."""

    del composed_permissions
    assert set(Link.declared_target_models()) == {"projects.project", "projects.task"}
    assert Link.declared_target_model("projects.Task") is Link._meta.apps.get_model("projects", "Task")
    with pytest.raises(ValidationError, match="Link may target only: project, task"):
        Link.declared_target_model("knowledge.vault")
    assert {"projects.task", "storage.drive", "mtidemo.mtiparent"} <= set(FileAttachment.declared_target_models())


def test_concrete_child_uses_parent_link_and_prefetched_child(record_ref_tables: None) -> None:
    """One owner resolves MTI children from a parent row and honors Django's relation cache."""

    del record_ref_tables
    with system_context(reason="concrete child lookup"):
        child = MtiChild.objects.create(title="Parent", detail="Child")
        parent = MtiParent.objects.get(pk=child.pk)
        with CaptureQueriesContext(connection) as uncached:
            found = concrete_child(parent, MtiChild)
        assert found is not None and found.detail == "Child"
        assert len(uncached) == 1
        prefetched = MtiParent.objects.select_related("mtichild").get(pk=child.pk)
        with CaptureQueriesContext(connection) as cached:
            assert concrete_child(prefetched, MtiChild) is prefetched.mtichild
        assert len(cached) == 0
        assert concrete_child(prefetched, MtiChild, queryset=MtiChild.objects.none()) is None
    assert concrete_child_models(MtiParent) == (MtiChild,)
    assert concrete_child_accessor(MtiParent, MtiChild) == "mtichild"


def test_generic_target_keys_every_edge_on_the_typed_canonical_row(record_ref_tables: None) -> None:
    """The library's identity names a child, a proxy and a typed leaf by one canonical model.

    A proxy resolves to its concrete model first, then the MTI walk runs: an untyped
    proxy over a typed concrete row keys on the typed ancestor, never the proxy's own
    content type. An untyped row is refused.
    """

    del record_ref_tables
    with system_context(reason="generic target identity"):
        child = MtiChild.objects.create(title="Acme", detail="org")
        parent = MtiParent.objects.create(title="Plain")
        child_proxy = MtiChildProxy.objects.create(title="Proxy child", detail="org")
        parent_proxy = MtiParentProxy.objects.create(title="Proxy parent")
        typed = RecordRefTypedTarget.objects.create(name="typed")
    parent_content_type = ContentType.objects.get_for_model(MtiParent)
    for row in (child, parent, child_proxy, parent_proxy):
        assert (generic_target(row).content_type, generic_target(row).object_id) == (parent_content_type, row.pk)
    assert generic_target(child_proxy).content_type != ContentType.objects.get_for_model(
        MtiChildProxy, for_concrete_model=False
    )
    assert generic_target(typed).content_type == ContentType.objects.get_for_model(RecordRefTypedTarget)
    plain = RecordRefPlainTarget.objects.create(name="plain")
    with pytest.raises(ValueError, match="no resource type"):
        generic_target(plain)
    # An edge that admits ungated rows shares the gated identity and keeps Django's own for the rest.
    for row in (child, parent, child_proxy, parent_proxy, typed):
        assert generic_pointer_target(row) == (generic_target(row).content_type, row.pk)
    assert generic_pointer_target(plain) == (ContentType.objects.get_for_model(RecordRefPlainTarget), plain.pk)
    assert generic_pointer_model(MtiChildProxy) is MtiParent
    assert generic_pointer_model(RecordRefPlainTarget) is RecordRefPlainTarget


def test_ancestor_object_refs_fans_out_every_rebac_ancestor(record_ref_tables: None) -> None:
    """The read/grant fan-out yields the row's own identity then each typed ancestor."""

    del record_ref_tables
    with system_context(reason="ancestor object refs mti"):
        child = MtiChild.objects.create(title="Acme", detail="org")
        parent = MtiParent.objects.create(title="Plain")

    # Every identity shares the row's REBAC id; only the type varies down the chain.
    assert ancestor_object_refs(child) == (
        ObjectRef(model_resource_type(MtiChild), str(child.pk)),
        ObjectRef(model_resource_type(MtiParent), str(child.pk)),
    )
    # A row with no typed ancestor yields exactly its own identity.
    assert ancestor_object_refs(parent) == (ObjectRef(model_resource_type(MtiParent), str(parent.pk)),)


def test_ancestor_object_refs_fails_fast_at_the_call_on_an_untyped_row(record_ref_tables: None) -> None:
    """A row whose model declares no REBAC type has no identity to fan out — the tuple call raises."""

    del record_ref_tables
    plain = RecordRefPlainTarget.objects.create(name="plain")

    with pytest.raises(TypeError):
        ancestor_object_refs(plain)
