"""Native IAM contributions retain protected query/write/error/stream contracts."""

from __future__ import annotations

import pytest
from django.conf import settings

if settings.SETTINGS_MODULE != "tests.permissioned_graphql_settings":
    pytest.skip("Run with its documented minimal protected GraphQL settings.", allow_module_level=True)
import strawberry_django
from rebac import RelationshipTuple, SubjectRef, actor_context, system_context, to_object_ref, write_relationships
from rebac.backends import backend, reset_backend
from rebac.schema import parse_zed

from angee.graphql.data.hasura import hasura_model_resource
from angee.graphql.events import ChangePayload
from angee.graphql.schema import GraphQLSchemas, SchemaParts
from angee.iam.graphql.node import ProtectedNode
from angee.iam.graphql.schema import IAMSchema
from tests.iam_only_models import APIEntry, NativeAPIEntry, NeutralAPIRoot


@strawberry_django.type(APIEntry, fields=["name"])
class EntryType(ProtectedNode):
    secret: str | None


@pytest.fixture
def api():
    reset_backend()
    backend().set_schema(
        parse_zed("""definition auth/user {}
    definition probe/api {
      relation reader: auth/user
      relation editor: auth/user
      permission read = reader
      permission write = editor
      permission delete = editor
      permission read__secret = editor
    }
    definition probe/native_api {
      relation reader: auth/user
      relation editor: auth/user
      permission read = reader
      permission write = editor
      permission delete = editor
      permission read__secret = editor
    }""")
    )
    resource = hasura_model_resource(
        EntryType,
        model=APIEntry,
        name="entries",
        filterable=["name"],
        sortable=["name"],
        aggregatable=["id"],
        writable=["name", "secret"],
    )
    registry = GraphQLSchemas(())
    registry.__dict__["parts"] = {
        "public": SchemaParts(
            query=(resource.query,), mutation=(resource.mutation,), types=tuple([EntryType, *resource.types])
        )
    }
    yield registry
    reset_backend()


@pytest.mark.django_db
def test_protected_schema_scopes_rows_fields_writes_and_missing_actor(api):
    schema = api.build()
    assert isinstance(schema, IAMSchema)
    actor = SubjectRef.of("auth/user", "reader")
    with system_context(reason="minimal protected API fixture"):
        visible = APIEntry.objects.create(name="Visible", secret="hidden")
        APIEntry.objects.create(name="Hidden")
        write_relationships([RelationshipTuple(to_object_ref(visible), "reader", actor)])
    with actor_context(actor):
        result = schema.execute_sync("{entries{id name secret redacted_fields}}")
        assert not result.errors, result.errors
        assert result.data["entries"] == [
            {"id": visible.public_id, "name": "Visible", "secret": None, "redacted_fields": ["secret"]}
        ]
        denied = schema.execute_sync(
            f'mutation {{ update_entries_by_pk(pk_columns: {{id: "{visible.public_id}"}}, '
            '_set: {name: "Denied"}) {id} }'
        )
        assert denied.errors[0].extensions["code"] == "PERMISSION_DENIED"
    missing = schema.execute_sync("{entries{id}}")
    assert missing.errors[0].extensions["code"] == "UNAUTHENTICATED"
    with system_context(reason="minimal protected unchanged state"):
        visible.refresh_from_db()
        assert visible.name == "Visible"


@pytest.mark.django_db
def test_protected_change_projection_and_live_revocation(api):
    from angee.iam.graphql.changes import filter_event

    actor = SubjectRef.of("auth/user", "stream-reader")
    with system_context(reason="minimal protected event fixture"):
        row = APIEntry.objects.create(name="Event", secret="hidden")
        grants = [RelationshipTuple(to_object_ref(row), "reader", actor)]
        write_relationships(grants)
        payload = ChangePayload.from_instance(
            row, action="update", update_fields=["name", "secret"], readable_fields=["name", "secret"]
        )
    assert payload.resource_id == str(row.pk)
    with actor_context(actor):
        receive = filter_event(APIEntry, None, None)
        delivered = receive(payload)
        assert delivered.changed_values == {"name": "Event"}
        from rebac import delete_relationship

        with system_context(reason="minimal protected event revoke"):
            delete_relationship(grants[0])
        assert receive(payload) is None


@pytest.mark.django_db
def test_lines_only_save_checks_the_parent_before_any_child_diff(api):
    from rebac import PermissionDenied

    from angee.graphql.data.hasura import HasuraLines
    from angee.iam.graphql.resources import ProtectedHasuraWriteBackend
    from tests.iam_only_models import APILine

    actor = SubjectRef.of("auth/user", "lines-reader")
    with system_context(reason="minimal protected lines fixture"):
        row = APIEntry.objects.create(name="Parent")
        write_relationships([RelationshipTuple(to_object_ref(row), "reader", actor)])
    writer = ProtectedHasuraWriteBackend(APIEntry, lines=HasuraLines(field="lines", model=APILine, position_field=""))
    with actor_context(actor), pytest.raises(PermissionDenied):
        writer.save(None, row.public_id, {}, [])
    assert not APILine._base_manager.exists()


@strawberry_django.type(NativeAPIEntry, fields=["id", "name"])
class NativeEntryType:
    secret: str | None


@strawberry_django.type(NeutralAPIRoot, fields=["id", "label"])
class NeutralRootType:
    pass


def native_api(resource, node, *, mutation=True):
    registry = GraphQLSchemas(())
    registry.__dict__["parts"] = {
        "public": SchemaParts(
            query=(resource.query,),
            mutation=(resource.mutation,) if mutation else (),
            types=tuple([node, *resource.types]),
        )
    }
    return registry.build()


@pytest.mark.django_db
def test_native_rebac_models_keep_query_field_aggregate_and_delete_policy(api):
    from rebac import PermissionDenied

    from angee.graphql.data.extensions import resource_extensions
    from angee.graphql.deletion import DeletePreview, delete_by_public_id
    from angee.iam.models.base import ProtectedModel
    from tests.iam_only_models import NativeAPIEntry, NeutralAPIRoot

    assert not issubclass(NativeAPIEntry, ProtectedModel)
    actor = SubjectRef.of("auth/user", "native-reader")
    with system_context(reason="native REBAC API fixture"):
        visible = NativeAPIEntry.objects.create(name="Visible", secret="hidden")
        NativeAPIEntry.objects.create(name="Hidden", secret="hidden")
        root = NeutralAPIRoot.objects.create(label="cascade-root", target=visible)
        hidden_child = NativeAPIEntry.objects.create(name="Hidden cascade", parent=visible)
        write_relationships([RelationshipTuple(to_object_ref(visible), "reader", actor)])
    resource = hasura_model_resource(
        NativeEntryType,
        model=NativeAPIEntry,
        name="native_entries",
        filterable=["name"],
        sortable=["name"],
        aggregatable=["id"],
        writable=["name"],
        public_id_field="id",
    )
    schema = native_api(resource, NativeEntryType)
    assert resource_extensions(NativeAPIEntry).write_backend_factory is not None
    with actor_context(actor):
        result = schema.execute_sync("{native_entries{id name secret} native_entries_aggregate{aggregate{count}}}")
        assert not result.errors, result.errors
        assert result.data["native_entries"] == [{"id": str(visible.pk), "name": "Visible", "secret": None}]
        assert result.data["native_entries_aggregate"]["aggregate"]["count"] == 1
        with pytest.raises(PermissionDenied):
            delete_by_public_id(NativeAPIEntry, str(visible.pk), confirm=True)
        preview = DeletePreview.from_instance(visible)
        assert preview.total_deleted_count >= 2

        # Unreadable native children are counted but never disclosed as preview leaves.
        def disclosed_ids(node):
            return {node.object_id} | set().union(*(disclosed_ids(child) for child in node.children))

        assert str(hidden_child.pk) not in disclosed_ids(preview.root)
    assert NeutralAPIRoot.objects.filter(pk=root.pk).exists()


@pytest.mark.django_db
def test_neutral_root_keeps_protected_relation_null_buckets_and_axis_refusal(api, monkeypatch):
    from django.core.exceptions import ImproperlyConfigured
    from rebac.resources import model_resource_type

    from angee.iam.graphql import resources as iam_resources
    from tests.iam_only_models import NativeAPIEntry, NeutralAPIRoot

    actor = SubjectRef.of("auth/user", "neutral-root-reader")
    with system_context(reason="neutral root protected relation fixture"):
        visible = NativeAPIEntry.objects.create(name="Visible target", secret="owner-only")
        hidden = NativeAPIEntry.objects.create(name="Hidden target", secret="owner-only")
        NeutralAPIRoot.objects.create(label="visible", target=visible)
        NeutralAPIRoot.objects.create(label="hidden", target=hidden)
        write_relationships([RelationshipTuple(to_object_ref(visible), "reader", actor)])
    monkeypatch.setattr(
        iam_resources,
        "declared_rebac_definition",
        lambda model: backend().schema().get_definition(model_resource_type(model)),
    )
    with pytest.raises(ImproperlyConfigured, match="field-gated reads"):
        hasura_model_resource(
            NeutralRootType,
            model=NeutralAPIRoot,
            name="unsafe_roots",
            public_id_field="id",
            filterable=["target__secret"],
            sortable=[],
            aggregatable=["id"],
            insert=False,
            update=False,
            delete=False,
        )
    resource = hasura_model_resource(
        NeutralRootType,
        model=NeutralAPIRoot,
        name="neutral_roots",
        public_id_field="id",
        filterable=["target__name"],
        sortable=["target__name"],
        groupable=["target", "target__name"],
        aggregatable=["id"],
        insert=False,
        update=False,
        delete=False,
    )
    schema = native_api(resource, NeutralRootType, mutation=False)
    with actor_context(actor):
        result = schema.execute_sync("""{
            neutral_roots_groups(group_by: [{field: TARGET}, {field: TARGET__NAME}]) {
                key{target__name} aggregate{count}
            }
            neutral_roots(where: {target__name: {_eq: "Hidden target"}}) {label}
        }""")
    assert not result.errors, result.errors
    assert result.data["neutral_roots"] == []
    assert sorted(
        (item["key"]["target__name"] or "<null>", item["aggregate"]["count"])
        for item in result.data["neutral_roots_groups"]
    ) == [("<null>", 1), ("Visible target", 1)]


@pytest.mark.django_db
@pytest.mark.parametrize("native", [False, True])
def test_explicit_native_backend_denies_lines_only_before_diff(api, monkeypatch, native):
    from rebac import PermissionDenied

    from angee.graphql.data.hasura import HasuraLines
    from angee.iam.graphql.resources import ProtectedHasuraWriteBackend
    from tests.iam_only_models import APILine, NativeAPIEntry, NativeAPILine

    model, child = (NativeAPIEntry, NativeAPILine) if native else (APIEntry, APILine)
    node = NativeEntryType if native else EntryType
    actor = SubjectRef.of("auth/user", "explicit-lines-reader")
    with system_context(reason="explicit native lines fixture"):
        row = model.objects.create(name="Parent")
        write_relationships([RelationshipTuple(to_object_ref(row), "reader", actor)])
    lines = HasuraLines(field="lines", model=child, writable=["text"], position_field="position" if native else "")
    writer = ProtectedHasuraWriteBackend(model, public_id_fields=(), lines=lines)
    calls = []
    monkeypatch.setattr(writer, "_apply_line_diff", lambda *args, **kwargs: calls.append("diff"))
    hasura_model_resource(
        node,
        model=model,
        name="explicit_lines",
        public_id_field="id" if native else "sqid",
        filterable=["name"],
        sortable=[],
        aggregatable=["id"],
        writable=["name"],
        lines=lines,
        write_backend=writer,
    )
    with actor_context(actor), pytest.raises(PermissionDenied):
        writer.save(None, str(row.pk) if native else row.public_id, {}, [])
    assert calls == []


@pytest.mark.django_db
def test_mixed_relation_defaults_follow_each_target_owner(api):
    from angee.graphql.impl import public_impl_defaults
    from tests.iam_only_models import MixedDefaultSource
    from tests.permissionless_app.models import PlainDefaultTarget

    actor = SubjectRef.of("auth/user", "defaults-reader")
    ordinary = PlainDefaultTarget.objects.create(slug="ordinary-default")
    with system_context(reason="mixed defaults fixture"):
        visible = NativeAPIEntry.objects.create(name="Visible default", slug="visible-default")
        NativeAPIEntry.objects.create(name="Hidden default", slug="hidden-default")
        write_relationships([RelationshipTuple(to_object_ref(visible), "reader", actor)])
    with actor_context(actor):
        assert public_impl_defaults(
            MixedDefaultSource, {"plain": "ordinary-default", "protected": "visible-default"}
        ) == {"plain": str(ordinary.pk), "protected": str(visible.pk)}
        assert public_impl_defaults(
            MixedDefaultSource, {"plain": "ordinary-default", "protected": "hidden-default"}
        ) == {"plain": str(ordinary.pk)}
