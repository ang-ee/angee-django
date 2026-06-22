"""Tests for the parties GraphQL data surfaces."""

from __future__ import annotations

import importlib
from typing import Any

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.parties.models import Address as AbstractAddress
from angee.parties.models import Affiliation as AbstractAffiliation
from angee.parties.models import Organization as AbstractOrganization
from angee.parties.models import PartyHandle as AbstractPartyHandle
from angee.parties.models import Person as AbstractPerson
from tests import test_messaging as messaging_models
from tests.conftest import SchemaAddon

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


def test_public_data_query_metadata_declares_people_surface() -> None:
    """The composed public schema reports Person's data-query contract."""

    schema = _schema("public")
    metadata = {
        item.model_label: item
        for item in schema.angee_data_queries
    }["parties.Person"]

    assert metadata.roots.list_name == "people"
    assert metadata.roots.detail_name == "person"
    assert metadata.roots.aggregate_name == "person_aggregate"
    assert metadata.roots.group_name == "person_groups"
    assert metadata.filter_fields == (
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
        "filterEcho",
    )
    assert metadata.relation_axes[0].field == "folder"
    assert metadata.relation_axes[0].model_label == "parties.Folder"
    assert metadata.relation_axes[0].public_id_field == "sqid"
    assert metadata.relation_axes[0].label_axis == "folder__name"

    serialized = schema._schema.extensions["angee"]["dataQueries"]
    person = {
        item["modelLabel"]: item
        for item in serialized
    }["parties.Person"]
    assert person["roots"] == {
        "listName": "people",
        "detailName": "person",
        "aggregateName": "personAggregate",
        "groupName": "personGroups",
    }
    assert person["groupByFields"] == ["folder", "folder_Name", "createdAt"]
    assert person["relationAxes"] == [
        {
            "field": "folder",
            "modelLabel": "parties.Folder",
            "publicIdField": "sqid",
            "labelAxis": "folder_Name",
        }
    ]


def _schema(name: str) -> Any:
    parts = {
        key: tuple(parties_schema.schemas[name].get(key, ()))
        for key in SCHEMA_PART_KEYS
    }
    return GraphQLSchemas([SchemaAddon({name: parts})]).build(name)
