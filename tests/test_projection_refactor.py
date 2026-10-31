"""Native write observation and finalized resource representation boundaries."""

import subprocess
import sys
import textwrap
from typing import Any

import pytest
import strawberry
import strawberry_django
from django.contrib.auth.models import Group
from django.core.exceptions import ImproperlyConfigured
from django.db import models, transaction
from django.test.utils import isolate_apps
from graphql import GraphQLInputObjectType
from rebac import PermissionDenied, actor_context, system_context
from strawberry.types import get_object_definition

from angee.base import signals
from angee.base.models.children import OwnedChildrenMixin
from angee.base.refs import RecordRef
from angee.graphql import events, publishing
from angee.graphql.data.hasura import hasura_model_resource
from angee.graphql.events import ChangePayload, ChangeRelatedRecord
from angee.graphql.schema import GraphQLSchemas
from tests.conftest import create_user, make_addon
from tests.linesdemo.models import Document, DocumentLine
from tests.tables import model_tables


def test_local_write_observation_has_no_transport_imports() -> None:
    """A plain Django model can emit real observations without enabling GraphQL."""
    script = textwrap.dedent("""
        import sys
        from django.conf import settings
        settings.configure(INSTALLED_APPS=[], SECRET_KEY='probe')
        import django
        django.setup()
        from django.db import models
        from angee.base.signals import change_published, publish_change
        class Row(models.Model):
            name = models.CharField(max_length=20)
            class Meta:
                app_label = 'probe'
        observed = []
        def observe(sender, payload, **kwargs):
            observed.append(payload)
        change_published.connect(observe, weak=False)
        row = Row(pk=7, name='saved')
        publish_change(row, action='update', update_fields=('name',))
        assert observed[0].id == '7'
        assert observed[0].changed_values == {'name': 'saved'}
        assert not any(name.startswith(('angee.graphql', 'strawberry', 'channels')) for name in sys.modules)
    """)
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.django_db(transaction=True)
def test_local_commit_effects_precede_transport_and_cannot_change_captured_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The completion boundary preserves occurrence, snapshot and native commit ordering."""
    calls: list[str] = []
    observations: list[signals.ModelChange] = []
    messages: list[dict[str, Any]] = []

    def observe(sender: type[models.Model], payload: signals.ModelChange, **kwargs: Any) -> None:
        calls.append("local")
        observations.append(payload)
        transaction.on_commit(lambda: calls.append("local committed"), using=payload.using)
        payload.instance.name = "changed by observer"

    def broadcast(model: type[models.Model], message: dict[str, Any]) -> None:
        calls.append("transport committed")
        messages.append(message)

    monkeypatch.setattr(publishing, "_broadcast", broadcast)
    signals.change_published.connect(observe, sender=Group, weak=False)
    try:
        with transaction.atomic():
            publishing.publish_change(Group(pk=7, name="saved"), action="update",
                                      update_fields=("name",), readable_fields=("name",))
            assert calls == ["local"]
        assert calls == ["local", "local committed", "transport committed"]
        assert messages[0]["changed_values"] == {"name": "saved"}
        assert messages[0]["occurrence_id"] == observations[0].occurrence_id
    finally:
        signals.change_published.disconnect(observe, sender=Group)


@isolate_apps()
@pytest.mark.parametrize("target_first", [False, True])
def test_related_labels_use_finalized_resource_policy(target_first: bool) -> None:
    """An explicit non-default label applies regardless of resource contribution order."""
    class Handle(models.Model):
        value = models.CharField(max_length=40)
        display_name = models.CharField(max_length=40)

        class Meta:
            app_label = "projection_test"

    class Entry(models.Model):
        handle = models.ForeignKey(Handle, on_delete=models.CASCADE)

        class Meta:
            app_label = "projection_test"

    @strawberry_django.type(Handle)
    class HandleType:
        id: strawberry.auto
        value: strawberry.auto = strawberry_django.field(name="caption")
        display_name: strawberry.auto

    @strawberry_django.type(Entry)
    class EntryType:
        id: strawberry.auto
        handle: HandleType

    target = hasura_model_resource(
        HandleType, model=Handle, name="projection_handles", filterable=("value",), sortable=("value",),
        aggregatable=(), insert=False, update=False, delete=False, public_id_field="id",
        record_representation="value",
    )
    source = hasura_model_resource(
        EntryType, model=Entry, name="projection_entries", filterable=("handle",), sortable=("id",),
        aggregatable=(), insert=False, update=False, delete=False, public_id_field="id",
    )
    resources = (target, source) if target_first else (source, target)
    schemas = GraphQLSchemas([make_addon(schemas={"public": {
        "query": tuple(resource.query for resource in resources),
        "types": (HandleType, EntryType, *(kind for resource in resources for kind in resource.types)),
    }})])
    metadata = next(item for item in schemas.resources("public") if item.model is Entry)
    assert metadata.query.fields["handle"].relation.label_path == "handle.caption"
    assert metadata.query.fields["handle"].relation.identity_path == "handle.id"


@isolate_apps()
def test_authored_empty_insert_and_distinct_update_surfaces_are_authoritative() -> None:
    """A native input donor can extend an empty insert list without exposing columns."""
    class Row(models.Model):
        title = models.CharField(max_length=40)
        secret = models.CharField(max_length=40)
        hasura_readable_fields = ("secret",)
        hasura_insertable_fields = ("secret",)
        hasura_updatable_fields = ("secret",)

        class Meta:
            app_label = "projection_test"

    @strawberry_django.type(Row)
    class RowType:
        id: strawberry.auto
        title: strawberry.auto

    resource = hasura_model_resource(
        RowType, model=Row, name="projection_rows", filterable=("title",), sortable=("title",),
        aggregatable=("id",), writable=("title", "secret"), insertable=(), updatable=("title",),
        public_id_field="id", delete=False,
    )

    @strawberry.input(name=get_object_definition(resource.insert_input_type, strict=True).name, extend=True)
    class InsertExtension:
        client_value: str | None = strawberry.UNSET

    schemas = GraphQLSchemas([make_addon(schemas={"public": {
        "query": (resource.query,), "mutation": (resource.mutation,),
        "types": (RowType, *resource.types), "input_extensions": (InsertExtension,),
    }})])
    schema = schemas.build("public")._schema
    insert_input = schema.get_type(get_object_definition(resource.insert_input_type, strict=True).name)
    update_input = schema.get_type(get_object_definition(resource.set_input_type, strict=True).name)
    assert isinstance(insert_input, GraphQLInputObjectType)
    assert isinstance(update_input, GraphQLInputObjectType)
    assert set(insert_input.fields) == {"client_value"}
    assert set(update_input.fields) == {"title"}
    assert "secret" not in schema.get_type("RowType").fields


@pytest.mark.django_db(transaction=True)
@isolate_apps("django.contrib.auth", "django.contrib.contenttypes")
def test_neutral_parent_replaces_children_without_a_permission_api() -> None:
    """Ordinary Django parents compose desired-set persistence without fake policy."""
    class NeutralParent(OwnedChildrenMixin):
        class Meta:
            app_label = "auth"

    class NeutralChild(models.Model):
        parent = models.ForeignKey(NeutralParent, on_delete=models.CASCADE, related_name="children")
        label = models.CharField(max_length=40)

        class Meta:
            app_label = "auth"

    def create(values: dict[str, Any]) -> models.Model:
        return NeutralChild.objects.create(**values)

    def update(child: models.Model, values: dict[str, Any], validate: Any) -> None:
        NeutralChild.objects.filter(pk=child.pk).update(**values)

    with model_tables((NeutralParent, NeutralChild)):
        parent = NeutralParent.objects.create()
        assert not hasattr(parent, "require_access")
        parent.save_children("children", [(None, {"label": "first"}, {})], create=create, update=update)
        first = parent.children.get()
        parent.save_children("children", [(None, {"label": "second"}, {})],
                             create=create, update=update, replace=False)
        assert parent.children.count() == 2
        parent.save_children("children", [(str(first.pk), {"label": "kept"}, {})], create=create, update=update)
        assert list(parent.children.values_list("label", flat=True)) == ["kept"]


def test_protected_parent_admission_precedes_child_elevation(composed_tables: None) -> None:
    """Calling the parent owner directly cannot bypass a reader's missing write grant."""
    reader = create_user("projection_reader")
    with system_context(reason="test.projection.seed"):
        parent = Document.objects.create(title="Protected")
        DocumentLine.objects.create(document=parent, label="Kept")
    with actor_context(reader), pytest.raises(PermissionDenied):
        parent.save_children(
            "lines", [], create=lambda values: pytest.fail("created before admission"),
            update=lambda *args: pytest.fail("updated before admission"),
        )
    with system_context(reason="test.projection.verify"):
        assert parent.lines.count() == 1


@isolate_apps()
@pytest.mark.parametrize("legacy", [False, True])
def test_native_related_reference_projects_without_implicit_view_expansion(
    monkeypatch: pytest.MonkeyPatch, legacy: bool,
) -> None:
    """Wire projection preserves exactly the views declared by the model owner."""
    reference = RecordRef("external.Record", 7, "external_7", "")

    class ReferencingRow(models.Model):
        class Meta:
            app_label = "projection_test"

        def change_related_records(self):
            return (ChangeRelatedRecord(reference.model_label, reference.public_id),) if legacy else (reference,)

    monkeypatch.setattr(events, "record_view_refs", lambda *args, **kwargs: pytest.fail("widened declared references"))
    payload = ChangePayload.from_instance(ReferencingRow(pk=8), action="update", update_fields=None)
    assert payload.related_records == (ChangeRelatedRecord("external.Record", "external_7"),)


@isolate_apps()
def test_resource_axes_delegate_explicitly_without_widening_authored_operations() -> None:
    """Model contributions reach native inputs/filters only for delegated axes."""
    class Donor(models.Model):
        hasura_filterable_fields = ("secret",)
        hasura_sortable_fields = ("secret",)
        hasura_insertable_fields = ("secret",)
        hasura_updatable_fields = ("title",)
        class Meta:
            abstract = True

    class Row(Donor):
        title = models.CharField(max_length=40)
        secret = models.CharField(max_length=40)
        hasura_filterable_fields = ("title",)
        class Meta:
            app_label = "projection_test"

    @strawberry_django.type(Row)
    class DelegatedRowType:
        id: strawberry.auto
        title: strawberry.auto
        secret: strawberry.auto

    resource = hasura_model_resource(
        DelegatedRowType, model=Row, name="delegated_rows", filterable=..., sortable=...,
        aggregatable=..., groupable=..., insertable=..., updatable=(), public_id_field="id",
    )
    update_name = get_object_definition(resource.set_input_type, strict=True).name
    assert get_object_definition(resource.set_input_type, strict=True).fields == []

    @strawberry.input(name=update_name, extend=True)
    class UpdateExtension:
        client_value: str | None = strawberry.UNSET

    schemas = GraphQLSchemas([make_addon(schemas={"public": {
        "query": (resource.query,), "mutation": (resource.mutation,),
        "types": (DelegatedRowType, *resource.types), "input_extensions": (UpdateExtension,),
    }})])
    schema = schemas.build("public")._schema
    insert_type = get_object_definition(resource.insert_input_type, strict=True).name
    assert set(schema.get_type(insert_type).fields) == {"secret"}
    assert set(schema.get_type(update_name).fields) == {"client_value"}
    filter_name = get_object_definition(resource.filter_type, strict=True).name
    order_name = get_object_definition(resource.order_by_type, strict=True).name
    assert {"title", "secret"} <= set(schema.get_type(filter_name).fields)
    assert "secret" in schema.get_type(order_name).fields



@isolate_apps()
def test_scalar_fk_projection_preserves_identity_nullability_and_only_hint() -> None:
    from angee.graphql.ids import public_id_field, require_public_id

    class Target(models.Model):
        class Meta:
            app_label = "projection_test"

    class Row(models.Model):
        target = models.ForeignKey(Target, null=True, on_delete=models.CASCADE, db_column="target_ref")
        class Meta:
            app_label = "projection_test"

    @strawberry_django.type(Row)
    class ScalarReferenceType:
        target: strawberry.ID | None = public_id_field("target", nullable=True)
        required: strawberry.ID = public_id_field("target")

    @strawberry.type
    class Query:
        @strawberry.field
        def row(self) -> ScalarReferenceType:
            return Row(target_id=7)

    schema = strawberry.Schema(query=Query)
    result = schema.execute_sync("{ row { target required } }")
    assert result.errors is None
    expected = str(require_public_id(Target, 7))
    assert result.data == {"row": {"target": expected, "required": expected}}
    fields = get_object_definition(ScalarReferenceType, strict=True).fields
    assert all(field.store.only == ["target"] for field in fields)
    # Native Django resolves the relation name to the same FK column as its
    # attname, including a custom database-column spelling; no target join/load.
    assert str(Row.objects.only("target").query) == str(Row.objects.only("target_id").query)
    assert '"target_ref"' in str(Row.objects.only("target").query)
    optional = next(field for field in fields if field.python_name == "target")
    required = next(field for field in fields if field.python_name == "required")
    assert optional.base_resolver(Row(target_id=None)) is None
    with pytest.raises(ValueError, match="unexpectedly unset"):
        required.base_resolver(Row(target_id=None))


@isolate_apps()
@pytest.mark.parametrize("nullable", [False, True])
def test_scalar_fk_projection_rejects_non_primary_target_field(nullable: bool) -> None:
    from angee.graphql.ids import public_id_field

    class Target(models.Model):
        code = models.CharField(max_length=40, unique=True)
        class Meta:
            app_label = "projection_test"

    class Row(models.Model):
        target = models.ForeignKey(Target, to_field="code", null=True, on_delete=models.CASCADE)
        class Meta:
            app_label = "projection_test"

    @strawberry_django.type(Row)
    class NaturalKeyReferenceType:
        target: strawberry.ID | None = public_id_field("target", nullable=nullable)

    field = get_object_definition(NaturalKeyReferenceType, strict=True).fields[0]
    with pytest.raises(ImproperlyConfigured, match="must target a primary key"):
        field.base_resolver(Row(target_id="natural-key"))


@isolate_apps()
@pytest.mark.parametrize("axis", ["writable", "insertable", "updatable"])
@pytest.mark.parametrize("selection,expected", [(None, {"title", "secret"}), ((), set()), (..., {"secret"})])
def test_write_axis_native_default_empty_and_delegated_admission(
    axis: str, selection: Any, expected: set[str],
) -> None:
    class Row(models.Model):
        title = models.CharField(max_length=40)
        secret = models.CharField(max_length=40)
        hasura_writable_fields = ("secret",)
        hasura_insertable_fields = ("secret",)
        hasura_updatable_fields = ("secret",)
        class Meta:
            app_label = "projection_test"

    @strawberry_django.type(Row)
    class AdmissionRowType:
        id: strawberry.auto
        title: strawberry.auto
        secret: strawberry.auto

    resource = hasura_model_resource(
        AdmissionRowType, model=Row, name="admission_rows", filterable=("title",), sortable=("title",),
        aggregatable=(), public_id_field="id", **{axis: selection},
    )
    surface = resource.set_input_type if axis == "updatable" else resource.insert_input_type
    assert {field.python_name for field in get_object_definition(surface, strict=True).fields} == expected


@isolate_apps()
def test_delegated_missing_write_declarations_deny_all_fields() -> None:
    class Row(models.Model):
        title = models.CharField(max_length=40)
        class Meta:
            app_label = "projection_test"

    @strawberry_django.type(Row)
    class UndeclaredRowType:
        id: strawberry.auto
        title: strawberry.auto

    resource = hasura_model_resource(
        UndeclaredRowType, model=Row, name="undeclared_rows", filterable=("title",), sortable=("title",),
        aggregatable=(), insertable=..., updatable=..., public_id_field="id",
    )
    assert get_object_definition(resource.insert_input_type, strict=True).fields == []
    assert get_object_definition(resource.set_input_type, strict=True).fields == []


def test_tag_legacy_readable_donor_still_contributes_filter_and_group_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tag retains its historical readable-to-query extension contract locally."""
    import importlib.util

    from angee.graphql.data import hasura
    from angee.tags import schema as tag_schema

    declared = hasura.declared_hasura_resource_fields

    def legacy_declarations(model: type[models.Model], attribute: str) -> tuple[str, ...]:
        fields = declared(model, attribute)
        # A third-party donor declares a readable ORM field, without the newer
        # explicit filter/group declarations. Color is absent from both base axes.
        return (*fields, "color") if model is tag_schema.Tag and attribute == "hasura_readable_fields" else fields

    monkeypatch.setattr(hasura, "declared_hasura_resource_fields", legacy_declarations)
    module_name = "angee.tags._legacy_projection_test"
    spec = importlib.util.spec_from_file_location(module_name, tag_schema.__file__)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    resource = module._TAG_RESOURCE
    filter_fields = get_object_definition(resource.filter_type, strict=True).fields
    group_fields = get_object_definition(resource.group_key_type, strict=True).fields
    assert "color" in {field.python_name for field in filter_fields}
    assert "color" in {field.python_name for field in group_fields}
    assert resource.insertable_fields == tag_schema._TAG_RESOURCE.insertable_fields
    assert resource.updatable_fields == tag_schema._TAG_RESOURCE.updatable_fields
