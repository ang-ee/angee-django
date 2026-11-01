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
from tests.iam_only_models import APIEntry


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
