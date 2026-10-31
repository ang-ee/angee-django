"""Native write observation and finalized resource representation boundaries."""

import subprocess
import sys
import textwrap
from typing import Any

import pytest
import strawberry
import strawberry_django
from django.contrib.auth.models import Group
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
