"""Ordinary native GraphQL CRUD and streaming without IAM/REBAC imports."""
from __future__ import annotations
import asyncio
import json
import sys
import pytest
from django.conf import settings
if settings.SETTINGS_MODULE != "tests.permissionless_graphql_settings":
    pytest.skip("Run with its documented minimal GraphQL settings.", allow_module_level=True)
import strawberry_django
from django.test import RequestFactory, override_settings
from angee.graphql.data.hasura import hasura_model_resource
from angee.graphql.data.extensions import ResourceExtensions, resource_extensions
from angee.graphql.events import ChangePayload
from angee.graphql.inputs import InputReference
from angee.graphql.schema import AngeeSchema, GraphQLSchemas, SchemaParts
from angee.graphql.node import AngeeNode
from tests.permissionless_app.models import PlainEntry

@strawberry_django.type(PlainEntry, fields=["name"])
class EntryType(AngeeNode):
    pass

@pytest.fixture
def api():
    resource = hasura_model_resource(EntryType, model=PlainEntry, name="entries",
        filterable=["name"], sortable=["name"], aggregatable=["id"], writable=["name"],
        public_id_field="id")
    registry = GraphQLSchemas(())
    registry.__dict__["parts"] = {"public": SchemaParts(query=(resource.query,), mutation=(resource.mutation,), types=tuple([EntryType, *resource.types]))}
    return registry

def test_neutral_graphql_import_closure_and_native_schema(api):
    assert isinstance(api.build(), AngeeSchema)
    assert not any(name == "rebac" or name.startswith("rebac.") or name == "angee.iam" or name.startswith("angee.iam.") for name in sys.modules)
    metadata = api.build().angee_resources[0]
    assert metadata.resource_type is None
    assert metadata.grantable == ()
    assert resource_extensions(PlainEntry) == ResourceExtensions()

@pytest.mark.django_db
def test_native_crud_reference_validation_and_http(api, monkeypatch):
    schema = api.build()
    created = schema.execute_sync('mutation { insert_entries_one(object: {name: "Created"}) { id name } }')
    assert not created.errors, created.errors
    pk = created.data["insert_entries_one"]["id"]
    updated = schema.execute_sync(f'mutation {{ update_entries_by_pk(pk: "{pk}", patch: {{name: "Updated"}}) {{ id name }} }}')
    assert not updated.errors, updated.errors
    rows = schema.execute_sync('{ entries { id name } entries_aggregate { aggregate { count } } }')
    assert not rows.errors, rows.errors
    assert rows.data["entries"] == [{"id": pk, "name": "Updated"}]
    assert InputReference("permissionless.PlainEntry").resolve(None, [pk])[0].name == "Updated"
    from angee.graphql import views
    monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(lambda cls: api))
    views._get_view.cache_clear()
    request = RequestFactory().post("/graphql/public/", data=json.dumps({"query": "{entries{name}}"}), content_type="application/json")
    response = views.graphql_endpoint(request, "public")
    assert response.status_code == 200
    assert json.loads(response.content)["data"]["entries"] == [{"name": "Updated"}]
    invalid = schema.execute_sync('mutation { insert_entries_one(object: {name: "' + "x"*81 + '"}) {id} }')
    assert invalid.errors[0].extensions["code"] == "VALIDATION"
    removed = schema.execute_sync(f'mutation {{ delete_entries_by_pk(pk: "{pk}") {{ id }} }}')
    assert not removed.errors, removed.errors
    assert not PlainEntry.objects.exists()

def test_neutral_stream_registers_and_emits_without_an_actor():
    from angee.graphql.subscriptions import changes
    from angee.graphql.publishing import change_channel_layer, change_group
    async def run():
        surface = changes(PlainEntry, field="entryChanged")
        definition = surface.__strawberry_definition__.fields[0]
        stream = definition.base_resolver.wrapped_func(None, None)
        ready = await asyncio.wait_for(anext(stream), 2)
        assert ready.action == "ready"
        change = ChangePayload(model=PlainEntry._meta.label, id="1", action="update")
        layer = change_channel_layer()
        await layer.group_send(change_group(PlainEntry), {"type": "model.change", **change.as_message()})
        event = await asyncio.wait_for(anext(stream), 2)
        assert (event.id, event.action) == ("1", "update")
        await stream.aclose()
    asyncio.run(run())

def test_singleton_constructor_collisions_fail(api):
    from django.core.exceptions import ImproperlyConfigured
    with override_settings(ANGEE_GRAPHQL_SCHEMA_CLASS=["one.Schema", "two.Schema"]), pytest.raises(ImproperlyConfigured, match="at most one"):
        api.build()
