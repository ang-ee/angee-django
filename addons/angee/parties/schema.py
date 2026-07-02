"""Strawberry-Django schema contributions for parties.

``Party`` is the unified contact list (the multi-table-inheritance parent);
``Person`` and ``Organization`` are the concrete kinds you create and edit. A
contact is created as a person or an organisation and deleted through the party
(the parent delete cascades to the child row).
"""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.db import transaction
from rebac import system_context
from strawberry import auto

from angee.graphql.data import (
    AngeeHasuraWriteBackend,
    aggregate_queryset,
    hasura_model_resource,
    public_pk_decoder,
)
from angee.graphql.ids import optional_public_id
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
from angee.iam.audit import AuthoredRefMixin
from angee.iam.identity import user_public_id
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES, session_user
from angee.integrate.schema import IntegrationLabelMixin

Party = apps.get_model("parties", "Party")
Person = apps.get_model("parties", "Person")
Organization = apps.get_model("parties", "Organization")
Handle = apps.get_model("parties", "Handle")
PartyHandle = apps.get_model("parties", "PartyHandle")
Address = apps.get_model("parties", "Address")
Affiliation = apps.get_model("parties", "Affiliation")
Directory = apps.get_model("parties", "Directory")
Folder = apps.get_model("parties", "Folder")


@strawberry_django.type(Party)
class PartyType(AuthoredRefMixin, AngeeNode):
    """GraphQL projection of a party (the unified contact)."""

    display_name: auto
    notes: auto
    handle_count: auto
    created_at: auto
    updated_at: auto

    handles: list["HandleType"]
    party_handles: list["PartyHandleType"]
    addresses: list["AddressType"]
    affiliations: list["AffiliationType"]


@strawberry_django.type(Person)
class PersonType(AngeeNode):
    """GraphQL projection of a person."""

    display_name: auto
    notes: auto
    name_prefix: auto
    given_name: auto
    additional_name: auto
    family_name: auto
    name_suffix: auto
    nickname: auto
    birthday: auto
    anniversary: auto
    folder: "ContactFolderType | None"
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["user_id"])
    def user(self) -> strawberry.ID | None:
        """Return the linked platform user's public id, when this person is one."""

        return optional_public_id(user_public_id(cast(Any, self).user_id))


@strawberry_django.type(Organization)
class OrganizationType(AngeeNode):
    """GraphQL projection of an organisation."""

    display_name: auto
    notes: auto
    legal_name: auto
    domain: auto
    created_at: auto
    updated_at: auto


@strawberry_django.type(Handle)
class HandleType(AngeeNode):
    """GraphQL projection of a handle (a party's reachable address)."""

    platform: auto
    value: auto
    external_id: auto
    display_name: auto
    label: auto
    is_preferred: auto
    is_own: auto
    is_verified: auto
    party: PartyType | None
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["party_id"], prefetch_related="party_links")
    def confidence(self) -> float | None:
        """Confidence of the link that resolved this handle's owner (null if unowned).

        The rule lives on the model; the prefetch keeps it a single query per page.
        """

        return cast("Any", self).resolved_confidence


@strawberry_django.type(PartyHandle)
class PartyHandleType(AngeeNode):
    """GraphQL projection of a confidence-bearing party↔handle link."""

    party: PartyType | None
    handle: HandleType | None
    confidence: auto
    source: auto
    is_confirmed: auto
    is_dismissed: auto
    created_at: auto
    updated_at: auto


@strawberry_django.type(Address)
class AddressType(AngeeNode):
    """GraphQL projection of a physical/postal address."""

    party: PartyType | None
    label: auto
    po_box: auto
    extended: auto
    street: auto
    city: auto
    region: auto
    postal_code: auto
    country: auto
    latitude: auto
    longitude: auto
    is_primary: auto


@strawberry_django.type(Affiliation)
class AffiliationType(AngeeNode):
    """GraphQL projection of an organisation affiliation."""

    party: PartyType | None
    organization: PartyType | None
    organization_name: auto
    role: auto
    title: auto
    department: auto
    started_at: auto
    ended_at: auto
    is_primary: auto


@strawberry_django.type(Folder)
class ContactFolderType(AngeeNode):
    """GraphQL projection of a contact folder (a synced address book's parties).

    Named distinctly from storage's file ``FolderType`` — a different concept.
    """

    name: auto
    directory: "DirectoryType | None"
    source_href: auto
    ctag: auto
    created_at: auto
    updated_at: auto


@strawberry_django.type(Directory)
class DirectoryType(IntegrationLabelMixin, AngeeNode):
    """GraphQL projection of a connected contacts directory (e.g. a CardDAV source)."""

    backend_class: auto
    status: auto
    config: strawberry.scalars.JSON
    poll_interval: auto
    last_sync_status: auto
    last_sync_completed_at: auto
    last_sync_items: auto
    created_at: auto
    updated_at: auto


@strawberry.type
class PartiesDirectoryMutation:
    """Connect and manage contacts directories."""

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def connect_card_dav_directory(
        self,
        info: strawberry.Info,
        name: str,
        server_url: str,
        username: str,
        password: str,
    ) -> DirectoryType:
        """Create a Basic-auth credential and an active CardDAV directory to sync.

        ``server_url`` is the account/server URL — discovery finds the address
        books, so no exact collection URL is needed. The directory is created
        ``active`` and owned by the calling admin; ``syncIntegration`` then pulls its
        contacts into one :class:`~angee.parties.models.Folder` per address book.
        """

        user = session_user(info)
        credential_model = apps.get_model("integrate", "Credential")
        vendor_model = apps.get_model("integrate", "Vendor")
        # Credential creation, the directory, and the connection probe share one
        # transaction so a probe failure rolls all of it back — no orphan credential.
        with system_context(reason="parties.graphql.connect_carddav"), transaction.atomic():
            credential = credential_model.objects.create_local_credential(
                user,
                kind="basic_auth",
                name=f"CardDAV — {name}",
                material={"username": username, "password": password},
            )
            vendor, _created = vendor_model.objects.get_or_create(slug="carddav", defaults={"display_name": "CardDAV"})
            directory = Directory.objects.create(
                vendor=vendor,
                owner=user,
                credential=credential,
                backend_class="carddav",
                display_name=name,
                config={"server_url": server_url},
                status="active",
                created_by_id=user.pk,
            )
            # Validate the URL + credentials before the directory persists, so a bad
            # connection surfaces here instead of as a silent first-sync failure.
            directory.backend.probe()
        return cast(DirectoryType, directory)


_PARTY_RESOURCE = hasura_model_resource(
    PartyType,
    model=Party,
    name="parties",
    filterable=["id", "display_name", "created_at", "updated_at"],
    sortable=["display_name", "handle_count", "created_at", "updated_at"],
    aggregatable=["id", "handle_count"],
    groupable=["created_at"],
    insert=False,
    updatable=["display_name", "notes"],
)
_PERSON_RESOURCE = hasura_model_resource(
    PersonType,
    model=Person,
    name="people",
    filterable=[
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
    ],
    sortable=["display_name", "given_name", "family_name", "folder", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["folder", "folder__name", "created_at"],
    insertable=[
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
    ],
    updatable=[
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
    ],
    delete=False,
    field_id_decode={"folder": public_pk_decoder(Folder)},
)
_ORGANIZATION_RESOURCE = hasura_model_resource(
    OrganizationType,
    model=Organization,
    name="organizations",
    filterable=["id", "display_name", "legal_name", "domain", "created_at", "updated_at"],
    sortable=["display_name", "legal_name", "domain", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["domain", "created_at"],
    insertable=["display_name", "notes", "legal_name", "domain"],
    updatable=["display_name", "notes", "legal_name", "domain"],
    delete=False,
)
_HANDLE_RESOURCE = hasura_model_resource(
    HandleType,
    model=Handle,
    name="handles",
    filterable=[
        "id",
        "value",
        "display_name",
        "party",
        "platform",
        "is_own",
        "is_verified",
        "is_preferred",
        "created_at",
    ],
    sortable=["platform", "value", "created_at"],
    aggregatable=["id"],
    groupable=["party", "party__display_name"],
    insertable=["value", "platform", "external_id", "display_name", "label", "is_preferred", "party"],
    updatable=["value", "platform", "display_name", "label", "is_preferred", "party"],
    field_id_decode={"party": public_pk_decoder(Party)},
    get_aggregate_queryset=lambda info: aggregate_queryset(Handle.objects.filter(party__isnull=False)),
    write_backend=AngeeHasuraWriteBackend(Handle, public_id_fields=("party",)),
)
_ADDRESS_RESOURCE = hasura_model_resource(
    AddressType,
    model=Address,
    name="addresses",
    filterable=["id", "party", "label", "created_at"],
    sortable=["party", "label", "created_at"],
    aggregatable=["id"],
    insertable=[
        "party",
        "label",
        "po_box",
        "extended",
        "street",
        "city",
        "region",
        "postal_code",
        "country",
        "is_primary",
    ],
    updatable=[
        "label",
        "po_box",
        "extended",
        "street",
        "city",
        "region",
        "postal_code",
        "country",
        "is_primary",
    ],
    field_id_decode={"party": public_pk_decoder(Party)},
    write_backend=AngeeHasuraWriteBackend(Address, public_id_fields=("party",)),
)
_AFFILIATION_RESOURCE = hasura_model_resource(
    AffiliationType,
    model=Affiliation,
    name="affiliations",
    filterable=["id", "party", "organization", "created_at"],
    sortable=["party", "organization", "created_at"],
    aggregatable=["id"],
    insertable=[
        "party",
        "organization",
        "organization_name",
        "role",
        "title",
        "department",
        "is_primary",
    ],
    updatable=[
        "organization",
        "organization_name",
        "role",
        "title",
        "department",
        "is_primary",
    ],
    field_id_decode={
        "party": public_pk_decoder(Party),
        "organization": public_pk_decoder(Party),
    },
    write_backend=AngeeHasuraWriteBackend(Affiliation, public_id_fields=("party", "organization")),
)
_CONTACT_FOLDER_RESOURCE = hasura_model_resource(
    ContactFolderType,
    model=Folder,
    name="contact_folders",
    filterable=["id", "directory", "name", "source_href", "updated_at"],
    sortable=["directory", "name", "source_href", "created_at", "updated_at"],
    aggregatable=["id"],
    insert=False,
    update=False,
    delete=False,
    field_id_decode={"directory": public_pk_decoder(Directory)},
)
_DIRECTORY_RESOURCE = hasura_model_resource(
    DirectoryType,
    model=Directory,
    name="directories",
    filterable=[
        "id",
        "display_name",
        "backend_class",
        "status",
        "last_sync_status",
        "last_sync_completed_at",
        "updated_at",
    ],
    sortable=["display_name", "backend_class", "status", "last_sync_completed_at", "updated_at"],
    aggregatable=["id", "last_sync_items"],
    groupable=["backend_class", "status", "last_sync_status"],
    insert=False,
    update=False,
    delete=False,
)


_RESOURCE_TYPES = [
    *_PARTY_RESOURCE.types,
    *_PERSON_RESOURCE.types,
    *_ORGANIZATION_RESOURCE.types,
    *_HANDLE_RESOURCE.types,
    *_ADDRESS_RESOURCE.types,
    *_AFFILIATION_RESOURCE.types,
    *_CONTACT_FOLDER_RESOURCE.types,
    *_DIRECTORY_RESOURCE.types,
]


_PARTIES_SCHEMA_BUCKET = {
    "query": [
        _PARTY_RESOURCE.query,
        _PERSON_RESOURCE.query,
        _ORGANIZATION_RESOURCE.query,
        _HANDLE_RESOURCE.query,
        _ADDRESS_RESOURCE.query,
        _AFFILIATION_RESOURCE.query,
        _CONTACT_FOLDER_RESOURCE.query,
        _DIRECTORY_RESOURCE.query,
    ],
    "mutation": [
        PartiesDirectoryMutation,
        _PARTY_RESOURCE.mutation,
        _PERSON_RESOURCE.mutation,
        _ORGANIZATION_RESOURCE.mutation,
        _HANDLE_RESOURCE.mutation,
        _ADDRESS_RESOURCE.mutation,
        _AFFILIATION_RESOURCE.mutation,
        _CONTACT_FOLDER_RESOURCE.mutation,
        _DIRECTORY_RESOURCE.mutation,
    ],
    "types": [
        PartyType,
        PersonType,
        OrganizationType,
        HandleType,
        PartyHandleType,
        AddressType,
        AffiliationType,
        DirectoryType,
        ContactFolderType,
        *_RESOURCE_TYPES,
    ],
}


schemas = {
    "public": {
        **_PARTIES_SCHEMA_BUCKET,
    },
    "console": {
        **_PARTIES_SCHEMA_BUCKET,
        "subscription": [
            changes(Party, field="partyChanged"),
            changes(Handle, field="handleChanged"),
        ],
    },
}
