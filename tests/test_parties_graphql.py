"""Tests for the parties GraphQL data surfaces."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from rebac import app_settings, system_context
from rebac.roles import grant

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.parties.models import Address as AbstractAddress
from angee.parties.models import Affiliation as AbstractAffiliation
from angee.parties.models import Organization as AbstractOrganization
from angee.parties.models import PartyHandle as AbstractPartyHandle
from angee.parties.models import Person as AbstractPerson
from tests import test_messaging as messaging_models
from tests.conftest import (
    IAM_CONNECTION_TEST_MODELS,
    INTEGRATE_TEST_MODELS,
    SchemaAddon,
    _create_missing_tables,
    execute_schema,
)
from tests.conftest import result_data as _data

_AddressMeta = getattr(AbstractAddress, "Meta", object)
_AffiliationMeta = getattr(AbstractAffiliation, "Meta", object)
_OrganizationMeta = getattr(AbstractOrganization, "Meta", object)
_PartyHandleMeta = getattr(AbstractPartyHandle, "Meta", object)
_PersonMeta = getattr(AbstractPerson, "Meta", object)


class Address(AbstractAddress):
    """Concrete address model used to import the parties schema."""

    class Meta(_AddressMeta):
        abstract = False
        app_label = "parties"
        db_table = "test_parties_address"
        rebac_resource_type = "parties/address"
        rebac_id_attr = "sqid"


class Affiliation(AbstractAffiliation):
    """Concrete affiliation model used to import the parties schema."""

    class Meta(_AffiliationMeta):
        abstract = False
        app_label = "parties"
        db_table = "test_parties_affiliation"
        rebac_resource_type = "parties/affiliation"
        rebac_id_attr = "sqid"


class PartyHandle(AbstractPartyHandle):
    """Concrete party-handle model used to import the parties schema."""

    class Meta(_PartyHandleMeta):
        abstract = False
        app_label = "parties"
        db_table = "test_parties_party_handle"
        rebac_resource_type = "parties/party_handle"
        rebac_id_attr = "sqid"


class Person(messaging_models.Party, AbstractPerson):
    """Concrete person model matching the composer inheritance shape."""

    class Meta(_PersonMeta):
        abstract = False
        app_label = "parties"
        db_table = "test_parties_person"
        rebac_resource_type = "parties/person"
        rebac_id_attr = "sqid"


class Organization(messaging_models.Party, AbstractOrganization):
    """Concrete organization model matching the composer inheritance shape."""

    class Meta(_OrganizationMeta):
        abstract = False
        app_label = "parties"
        db_table = "test_parties_organization"
        rebac_resource_type = "parties/organization"
        rebac_id_attr = "sqid"


# Import after the concrete test models are registered; the source schema resolves
# the composer-emitted runtime models through Django's app registry.
parties_schema = importlib.import_module("angee.parties.schema")
User = get_user_model()
PARTIES_TEST_MODELS = (
    messaging_models.Directory,
    messaging_models.Folder,
    messaging_models.Party,
    Person,
    Organization,
    messaging_models.Handle,
    PartyHandle,
    Address,
    Affiliation,
)


def test_public_resource_metadata_declares_people_surface() -> None:
    """The composed public schema reports Person's Hasura resource contract."""

    schema = _schema("public")
    metadata = {
        item.model_label: item
        for item in schema.angee_resources
    }["parties.Person"]

    assert metadata.roots.list_name == "people"
    assert metadata.roots.detail_name == "people_by_pk"
    assert metadata.roots.aggregate_name == "people_aggregate"
    assert metadata.roots.group_name == "people_groups"
    assert metadata.roots.create_name == "insert_people_one"
    assert metadata.roots.update_name == "update_people_by_pk"
    assert metadata.roots.delete_name is None
    assert metadata.filter_fields == (
        "id",
        "display_name",
        "given_name",
        "family_name",
        "nickname",
        "folder",
        "birthday",
        "anniversary",
        "created_at",
        "updated_at",
    )
    assert metadata.order_fields == (
        "display_name",
        "given_name",
        "family_name",
        "folder",
        "created_at",
        "updated_at",
    )
    assert metadata.aggregate_fields == ("id",)
    assert metadata.group_by_fields == ("folder", "folder__name", "created_at")
    assert metadata.capabilities == (
        "list",
        "detail",
        "aggregate",
        "groups",
        "create",
        "update",
    )
    assert metadata.relation_axes[0].field == "folder"
    assert metadata.relation_axes[0].model_label == "parties.Folder"
    assert metadata.relation_axes[0].public_id_field == "sqid"
    assert metadata.relation_axes[0].label_axis == "folder__name"

    serialized = schema._schema.extensions["angee"]["resources"]
    person = {
        item["modelLabel"]: item
        for item in serialized
    }["parties.Person"]
    assert person["schemaName"] == "public"
    assert person["roots"]["list"] == "people"
    assert person["roots"]["detail"] == "people_by_pk"
    assert person["roots"]["aggregate"] == "people_aggregate"
    assert person["roots"]["groups"] == "people_groups"
    assert person["roots"]["create"] == "insert_people_one"
    assert person["roots"]["update"] == "update_people_by_pk"
    assert person["roots"]["delete"] is None
    assert person["groupByFields"] == ["folder", "folder__name", "created_at"]
    group_dimensions = {dimension["field"]: dimension for dimension in person["groupDimensions"]}
    assert {
        field: (
            dimension["input"],
            dimension["key"],
            dimension["kind"],
            dimension["scalar"],
        )
        for field, dimension in group_dimensions.items()
    } == {
        "folder": ("FOLDER", "folder_id", "relation", "ID"),
        "folder__name": ("FOLDER__NAME", "folder__name", "column", None),
        "created_at": ("CREATED_AT", "created_at", "column", "DateTime"),
    }
    created_at_extractions = {
        extraction["name"]: extraction
        for extraction in group_dimensions["created_at"]["extractions"]
    }
    assert created_at_extractions["month"] == {
        "name": "month",
        "input": "MONTH",
        "key": "created_at_month",
        "rangeKey": "created_at_month_range",
        "filter": {
            "kind": "range",
            "field": "created_at",
            "valueKey": "created_at_month",
            "rangeKey": "created_at_month_range",
            "lookup": None,
            "nullLookup": "isNull",
            "valueTransform": None,
            "valueMap": [],
        },
    }
    assert person["defaultMeasures"] == [{"op": "count", "field": None, "input": None}]
    assert person["aggregateMeasures"] == []
    assert person["createFields"] == [
        "display_name",
        "notes",
        "name_prefix",
        "given_name",
        "additional_name",
        "family_name",
        "name_suffix",
        "nickname",
        "birthday",
        "anniversary",
    ]
    assert person["requiredCreateFields"] == ["display_name"]
    assert person["updateFields"] == [
        "display_name",
        "notes",
        "name_prefix",
        "given_name",
        "additional_name",
        "family_name",
        "name_suffix",
        "nickname",
        "birthday",
        "anniversary",
    ]
    assert person["relationAxes"] == [
        {
            "field": "folder",
            "modelLabel": "parties.Folder",
            "publicIdField": "sqid",
            "labelAxis": "folder__name",
        }
    ]
    folder_field = {field["name"]: field for field in person["fields"]}["folder"]
    assert folder_field["kind"] == "relation"
    assert folder_field["widget"] == "many2one"
    assert folder_field["readable"] is True
    assert folder_field["relationModelLabel"] == "parties.Folder"
    assert folder_field["relationLabelAxis"] == "folder__name"
    display_name_field = {field["name"]: field for field in person["fields"]}["display_name"]
    assert display_name_field["creatable"] is True
    assert display_name_field["updatable"] is True
    assert display_name_field["requiredOnCreate"] is True


def test_public_resource_metadata_converts_related_parties_surfaces() -> None:
    """The related contacts roots are Hasura resources, not handwritten paginated fields."""

    resources = {item.model_label: item for item in _schema("public").angee_resources}

    address = resources["parties.Address"]
    assert address.roots.list_name == "addresses"
    assert address.roots.detail_name == "addresses_by_pk"
    assert address.roots.create_name == "insert_addresses_one"
    assert address.roots.update_name == "update_addresses_by_pk"
    assert address.roots.delete_name == "delete_addresses_by_pk"
    assert address.filter_fields == ("id", "party", "label", "created_at")
    assert address.create_fields[0] == "party"

    affiliation = resources["parties.Affiliation"]
    assert affiliation.roots.list_name == "affiliations"
    assert affiliation.roots.detail_name == "affiliations_by_pk"
    assert affiliation.create_fields[:2] == ("party", "organization")

    folder = resources["parties.Folder"]
    assert folder.roots.list_name == "contact_folders"
    assert folder.roots.detail_name == "contact_folders_by_pk"
    assert folder.capabilities == ("list", "detail", "aggregate")


def test_person_hasura_insert_and_update(parties_tables: None) -> None:
    """Person writes use generated Hasura mutation roots and model-owned fields."""

    admin = _platform_admin("party-hasura-admin")
    schema = _schema("public")

    created = _data(
        execute_schema(
            schema,
            """
            mutation CreatePerson {
              insert_people_one(object: {display_name: "Ada", given_name: "Ada"}) {
                id
                display_name
                given_name
                family_name
              }
            }
            """,
            user=admin,
        )
    )["insert_people_one"]
    assert created == {
        "id": created["id"],
        "display_name": "Ada",
        "given_name": "Ada",
        "family_name": "",
    }

    updated = _data(
        execute_schema(
            schema,
            """
            mutation UpdatePerson($id: String!) {
              update_people_by_pk(pk_columns: {id: $id}, _set: {family_name: "Lovelace"}) {
                display_name
                family_name
              }
            }
            """,
            {"id": created["id"]},
            user=admin,
        )
    )["update_people_by_pk"]
    assert updated == {"display_name": "Ada", "family_name": "Lovelace"}

    with system_context(reason="test.parties.hasura_person_write.verify"):
        person = Person.objects.get(sqid=created["id"])
    assert person.display_name == "Ada"
    assert person.family_name == "Lovelace"


@pytest.fixture()
def parties_tables(transactional_db: Any) -> Iterator[None]:
    """Create concrete parties tables and sync REBAC."""

    del transactional_db
    created_models = _create_missing_tables(IAM_CONNECTION_TEST_MODELS + INTEGRATE_TEST_MODELS + PARTIES_TEST_MODELS)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


def _schema(name: str) -> Any:
    parts = {
        key: tuple(parties_schema.schemas[name].get(key, ()))
        for key in SCHEMA_PART_KEYS
    }
    return GraphQLSchemas([SchemaAddon({name: parts})]).build(name)


def _platform_admin(username: str) -> Any:
    """Create a superuser holding the universal admin role."""

    admin = User.objects.create_superuser(username=username, email=f"{username}@example.com", password="admin")
    grant(actor=admin, role=app_settings.REBAC_UNIVERSAL_ADMIN_ROLE)
    return admin
