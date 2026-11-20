"""Lifecycle resources project model capabilities and dispatch trash to their owner."""

import importlib

import pytest
import strawberry
import strawberry_django
from django.core.exceptions import ImproperlyConfigured
from rebac import RelationshipTuple, system_context, to_object_ref, to_subject_ref, write_relationships
from rebac.schema import parse_zed
from strawberry.types import get_object_definition

from angee.graphql import records
from angee.graphql.data.hasura import hasura_model_resource
from angee.graphql.node import AngeeNode
from angee.graphql.testing.documents import execute_schema, result_data
from angee.messaging.testing.models import Organization, Party, Person
from angee.parties.managers import PartyQuerySet
from tests.conftest import Backend, Drive, addon_schema, create_user
from tests.scopedemo.models import Scope


@pytest.mark.parametrize(("addon", "labels"), [
    ("parties", ("Party", "Person", "Organization", "Address", "Relationship")),
    ("projects", ("Task",)),
])
@pytest.mark.parametrize("schema_name", ("public", "console"))
def test_addon_resources_expose_inherited_trash_flags(addon, labels, schema_name):
    schema = addon_schema(importlib.import_module(f"angee.{addon}.schema").schemas, schema_name)
    resources = {resource.model_label: resource for resource in schema.angee_resources}
    for label in labels:
        resource = resources[f"{addon}.{label}"]
        flags = [field for field in resource.fields if field.name == "is_trashed"]
        assert len(flags) == 1
        assert flags[0].trashable and not flags[0].archivable
        assert "is_trashed" not in resource.create_fields
        assert "is_trashed" not in resource.update_fields
        assert str(schema._schema.get_type(resource.type_names.node).fields["is_trashed"].type) == "Boolean!"


@strawberry_django.type(Drive)
class LifecycleDriveType(AngeeNode):
    """An addon node that leaves lifecycle projection to the resource."""

    name: strawberry.auto


@strawberry_django.type(Drive)
class DeclaredLifecycleDriveType(LifecycleDriveType):
    """An explicit declaration must not be contributed a second time."""

    is_archived: strawberry.auto


def drive_resource(*, declared=False, writable=("name",)):
    return hasura_model_resource(
        DeclaredLifecycleDriveType if declared else LifecycleDriveType,
        model=Drive, name="lifecycle_drives", filterable=("is_archived",) if declared else (),
        sortable=("name",), aggregatable=("id",), writable=writable, delete=False,
    )


@pytest.mark.django_db
@pytest.mark.parametrize("declared", (False, True))
def test_archive_flag_is_readable_filterable_and_keeps_explicit_write_policy(declared):
    owner = create_user("lifecycle-drive-owner")
    with system_context(reason="test lifecycle drive fixtures"):
        backend = Backend.objects.create(slug="lifecycle", label="Lifecycle", backend_class="local")
        active, archived = [
            Drive.objects.create(
                backend=backend, slug=name, prefix=name, name=name, created_by=owner, is_archived=flag,
            )
            for name, flag in (("Active", False), ("Archived", True))
        ]
    resource = drive_resource(declared=declared)
    schema = addon_schema({"public": {
        "query": [resource.query], "mutation": [resource.mutation], "types": resource.types,
    }}, "public")
    metadata = schema.angee_resources[0]
    flags = [field for field in metadata.fields if field.name == "is_archived"]
    assert len(flags) == 1 and flags[0].archivable and not flags[0].trashable
    assert metadata.query.fields["is_archived"].filter is not None
    for surface in (resource.insert_input_type, resource.set_input_type):
        assert "is_archived" not in {field.python_name for field in get_object_definition(surface, strict=True).fields}
    query = """query($archived: Boolean!) {
      lifecycle_drives(where: {is_archived: {_eq: $archived}}) { id is_archived }
    }"""
    for row, flag in ((active, False), (archived, True)):
        assert result_data(execute_schema(schema, query, {"archived": flag}, user=owner)) == {
            "lifecycle_drives": [{"id": row.sqid, "is_archived": flag}],
        }
    assert "is_archived" not in {
        field.python_name for field in get_object_definition(LifecycleDriveType, strict=True).fields
    }


def test_explicit_archive_writes_remain_available():
    resource = drive_resource(writable=("name", "is_archived"))
    for surface in (resource.insert_input_type, resource.set_input_type):
        assert "is_archived" in {field.python_name for field in get_object_definition(surface, strict=True).fields}


@pytest.mark.parametrize(("model", "flag"), [(Drive, "is_archived"), (Party, "is_trashed")])
def test_automatic_flag_projection_refuses_read_gates(monkeypatch, model, flag):
    declaration = parse_zed(f"""
        definition auth/user {{}}
        definition lifecycle/row {{
            relation reader: auth/user
            permission read = reader
            permission read__{flag} = reader
        }}
    """).get_definition("lifecycle/row")
    monkeypatch.setattr("angee.graphql.data.hasura.declared_rebac_definition", lambda model: declaration)
    node = strawberry_django.type(model)(type("GatedLifecycleType", (AngeeNode,), {}))
    with pytest.raises(ImproperlyConfigured, match=f"{flag}.*field-gated reads"):
        hasura_model_resource(
            node, model=model, filterable=(), sortable=(), aggregatable=(),
            insert=False, update=False, delete=False,
        )


def test_automatic_archive_filter_refuses_an_explicit_read_gated_flag(monkeypatch):
    declaration = parse_zed("""
        definition auth/user {}
        definition lifecycle/row {
            relation reader: auth/user
            permission read = reader
            permission read__is_archived = reader
        }
    """).get_definition("lifecycle/row")
    monkeypatch.setattr("angee.graphql.data.hasura.declared_rebac_definition", lambda model: declaration)
    with pytest.raises(ImproperlyConfigured, match="filterable axis.*is_archived.*field-gated reads"):
        hasura_model_resource(
            DeclaredLifecycleDriveType, model=Drive, filterable=(), sortable=(), aggregatable=(),
            insert=False, update=False, delete=False,
        )


def test_model_without_lifecycle_mixins_gains_no_flags():
    @strawberry_django.type(Scope)
    class LifecycleScopeType(AngeeNode):
        name: strawberry.auto

    resource = hasura_model_resource(
        LifecycleScopeType, model=Scope, name="lifecycle_scopes", filterable=("name",),
        sortable=("name",), aggregatable=("id",), writable=("name",), delete=False,
    )
    schema = addon_schema({"public": {
        "query": [resource.query], "mutation": [resource.mutation], "types": resource.types,
    }}, "public")
    metadata = schema.angee_resources[0]
    assert not any(field.trashable or field.archivable for field in metadata.fields)
    for surface in (LifecycleScopeType, resource.filter_type, resource.insert_input_type, resource.set_input_type):
        fields = {field.python_name for field in get_object_definition(surface, strict=True).fields}
        assert not fields & {"is_trashed", "is_archived"}


@pytest.fixture
def trash_schema():
    return addon_schema(importlib.import_module("angee.parties.schema").schemas, "public", records.schemas)


def run_trash_verb(schema, verb, row, actor, *, target_type="parties/person"):
    return result_data(execute_schema(schema, f"""
        mutation($id: ID!, $type: String!) {{
          {verb}(target_type: $type, target_id: $id, confirm: true) {{ ok validation_errors }}
        }}
    """, {"id": row.sqid, "type": target_type}, user=actor))[verb]


@pytest.mark.django_db
@pytest.mark.parametrize("model", (Person, Organization))
def test_child_trash_and_restore_act_on_the_party_with_delete_authority(trash_schema, model):
    owner, editor = create_user("child-trash-owner"), create_user("child-trash-editor")
    with system_context(reason="test child trash fixture"):
        child = model.objects.create(display_name="Shared party", created_by=owner)
    party = Party._base_manager.get(pk=child.pk)
    write_relationships([RelationshipTuple(to_object_ref(party), "editor", to_subject_ref(editor))])
    assert party.with_actor(editor).has_access("write")
    assert not party.with_actor(editor).has_access("delete")
    assert model.trash_model() is Party and Party.trash_model() is Party
    assert not model.stores_trash() and Party.stores_trash()
    target_type = f"parties/{model._meta.model_name}"
    for verb, expected in (("trash_record", True), ("restore_record", False)):
        assert not run_trash_verb(trash_schema, verb, child, editor, target_type=target_type)["ok"]
        party.refresh_from_db()
        assert party.is_trashed is not expected
        assert run_trash_verb(trash_schema, verb, child, owner, target_type=target_type)["ok"]
        party.refresh_from_db()
        child.refresh_from_db()
        assert party.is_trashed is child.is_trashed is expected
        assert party.trashed_by_id == (owner.pk if expected else None)


@pytest.mark.django_db
@pytest.mark.parametrize("verb", ("trash_record", "restore_record"))
def test_child_trash_verbs_keep_the_storing_models_target_scope(trash_schema, monkeypatch, verb):
    owner = create_user("narrow-child-trash-owner")
    trashed = verb == "restore_record"
    with system_context(reason="test narrowed child trash fixture"):
        child = Person.objects.create(display_name="Excluded party", created_by=owner, is_trashed=trashed)
    narrowed_models = []

    def trash_targets(rows):
        narrowed_models.append(rows.model)
        return rows.exclude(pk=child.pk)

    monkeypatch.setattr(PartyQuerySet, "trash_targets", trash_targets)
    assert not run_trash_verb(trash_schema, verb, child, owner)["ok"]
    assert narrowed_models == [Party]
    child.refresh_from_db()
    assert child.is_trashed is trashed


@pytest.mark.django_db
def test_shared_trash_refuses_a_model_without_the_mixin(trash_schema):
    owner = create_user("non-trashable-owner")
    result = run_trash_verb(trash_schema, "trash_record", owner, owner, target_type="auth/user")
    assert not result["ok"] and "targetType" in result["validation_errors"]
