"""Strawberry-Django schema contributions for parties.

``Party`` is the unified contact list (the multi-table-inheritance parent);
``Person`` and ``Organization`` are the concrete kinds you create and edit. A
contact is created as a person or an organisation and deleted through the party
(the parent delete cascades to the child row).
"""

from __future__ import annotations

from datetime import date
from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.db import transaction
from rebac import system_context
from strawberry import auto
from strawberry_django.pagination import OffsetPaginated

from angee.graphql.crud import crud
from angee.graphql.data import data_query
from angee.graphql.ids import PublicID
from angee.graphql.node import AngeeNode, detail
from angee.graphql.subscriptions import changes
from angee.iam.identity import user_display_label, user_public_id
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES, session_user

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
class PartyType(AngeeNode):
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

    @strawberry_django.field(only=["created_by_id"])
    def created_by(self) -> strawberry.ID | None:
        """Return the creator's public id without exposing the user object."""

        return cast("strawberry.ID | None", user_public_id(cast(Any, self).created_by_id))

    @strawberry_django.field(only=["created_by_id"])
    def created_by_label(self) -> str | None:
        """Return the creator's display label - no user object exposed."""

        return user_display_label(cast(Any, self).created_by_id)


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

        return cast("strawberry.ID | None", user_public_id(cast(Any, self).user_id))


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
class DirectoryType(AngeeNode):
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

    @strawberry_django.field(only=["display_name", "vendor", "status"])
    def display_name(self) -> str:
        """Return the operator-given directory name (the connect flow sets it)."""

        return cast(Any, self).display_label


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
            vendor, _created = vendor_model.objects.get_or_create(
                slug="carddav", defaults={"display_name": "CardDAV"}
            )
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


@strawberry.input
class PartyPatch:
    """Fields accepted when updating a party's common identity."""

    id: PublicID
    display_name: str | None = strawberry.UNSET
    notes: str | None = strawberry.UNSET


@strawberry.input
class PersonInput:
    """Fields accepted when creating a person."""

    display_name: str
    notes: str = ""
    name_prefix: str = ""
    given_name: str = ""
    additional_name: str = ""
    family_name: str = ""
    name_suffix: str = ""
    nickname: str = ""
    birthday: date | None = None
    anniversary: date | None = None


@strawberry.input
class PersonPatch:
    """Fields accepted when updating a person."""

    id: PublicID
    display_name: str | None = strawberry.UNSET
    notes: str | None = strawberry.UNSET
    name_prefix: str | None = strawberry.UNSET
    given_name: str | None = strawberry.UNSET
    additional_name: str | None = strawberry.UNSET
    family_name: str | None = strawberry.UNSET
    name_suffix: str | None = strawberry.UNSET
    nickname: str | None = strawberry.UNSET
    birthday: date | None = strawberry.UNSET
    anniversary: date | None = strawberry.UNSET


@strawberry.input
class OrganizationInput:
    """Fields accepted when creating an organisation."""

    display_name: str
    notes: str = ""
    legal_name: str = ""
    domain: str = ""


@strawberry.input
class OrganizationPatch:
    """Fields accepted when updating an organisation."""

    id: PublicID
    display_name: str | None = strawberry.UNSET
    notes: str | None = strawberry.UNSET
    legal_name: str | None = strawberry.UNSET
    domain: str | None = strawberry.UNSET


@strawberry.input
class HandleInput:
    """Fields accepted when creating a handle."""

    value: str
    platform: str = "email"
    external_id: str = ""
    display_name: str = ""
    label: str = ""
    is_preferred: bool = False
    party: PublicID | None = None


@strawberry.input
class HandlePatch:
    """Fields accepted when updating a handle."""

    id: PublicID
    value: str | None = strawberry.UNSET
    platform: str | None = strawberry.UNSET
    display_name: str | None = strawberry.UNSET
    label: str | None = strawberry.UNSET
    is_preferred: bool | None = strawberry.UNSET
    party: PublicID | None = strawberry.UNSET


@strawberry.input
class AddressInput:
    """Fields accepted when creating an address."""

    party: PublicID
    label: str = ""
    po_box: str = ""
    extended: str = ""
    street: str = ""
    city: str = ""
    region: str = ""
    postal_code: str = ""
    country: str = ""
    is_primary: bool = False


@strawberry.input
class AddressPatch:
    """Fields accepted when updating an address."""

    id: PublicID
    label: str | None = strawberry.UNSET
    po_box: str | None = strawberry.UNSET
    extended: str | None = strawberry.UNSET
    street: str | None = strawberry.UNSET
    city: str | None = strawberry.UNSET
    region: str | None = strawberry.UNSET
    postal_code: str | None = strawberry.UNSET
    country: str | None = strawberry.UNSET
    is_primary: bool | None = strawberry.UNSET


@strawberry.input
class AffiliationInput:
    """Fields accepted when creating an affiliation."""

    party: PublicID
    organization: PublicID | None = None
    organization_name: str = ""
    role: str = ""
    title: str = ""
    department: str = ""
    is_primary: bool = False


@strawberry.input
class AffiliationPatch:
    """Fields accepted when updating an affiliation."""

    id: PublicID
    organization: PublicID | None = strawberry.UNSET
    organization_name: str | None = strawberry.UNSET
    role: str | None = strawberry.UNSET
    title: str | None = strawberry.UNSET
    department: str | None = strawberry.UNSET
    is_primary: bool | None = strawberry.UNSET


@strawberry_django.filter_type(Party, lookups=True)
class PartyFilter:
    """Field lookups accepted when filtering the parties connection."""

    display_name: auto
    created_at: auto
    updated_at: auto


@strawberry_django.order_type(Party)
class PartyOrder:
    """Orderings accepted by the parties connection."""

    display_name: auto
    handle_count: auto
    created_at: auto
    updated_at: auto


@strawberry_django.filter_type(Person, lookups=True)
class PersonFilter:
    """Field lookups accepted when filtering the people list (incl. the folder facet)."""

    display_name: auto
    given_name: auto
    family_name: auto
    nickname: auto
    folder: auto
    birthday: auto
    anniversary: auto
    created_at: auto
    updated_at: auto


@strawberry_django.order_type(Person)
class PersonOrder:
    """Orderings accepted by the people connection."""

    display_name: auto
    given_name: auto
    family_name: auto
    folder: auto
    created_at: auto
    updated_at: auto


@strawberry_django.filter_type(Handle, lookups=True)
class HandleFilter:
    """Field lookups accepted when filtering the handles connection.

    ``value`` and ``display_name`` are the searchable text columns: the toolbar's
    free-text search targets the record representation (``display_name``), and
    ``value`` is the handle itself (the email/phone/handle string).
    """

    value: auto
    display_name: auto
    party: auto
    platform: auto
    is_own: auto
    is_verified: auto
    is_preferred: auto
    created_at: auto


@strawberry_django.order_type(Handle)
class HandleOrder:
    """Orderings accepted by the handles connection."""

    platform: auto
    value: auto
    created_at: auto


@strawberry_django.filter_type(Address, lookups=True)
class AddressFilter:
    """Field lookups accepted when filtering the addresses connection (incl. by party)."""

    party: auto
    label: auto
    created_at: auto


@strawberry_django.filter_type(Affiliation, lookups=True)
class AffiliationFilter:
    """Field lookups accepted when filtering the affiliations connection (incl. by party)."""

    party: auto
    organization: auto
    created_at: auto


# Count parties and sum their handle_count, grouped by created_at. Both are
# non-gated read fields; kind is not an axis because it is the concrete child
# type, not a column.
PartyDataQuery, _PARTY_DATA_TYPES = data_query(
    PartyType,
    type_name="PartyDataQuery",
    filters=PartyFilter,
    order=PartyOrder,
    list_name="parties",
    detail_name="party",
    aggregate_name="party_aggregate",
    group_name="party_groups",
    aggregate_fields=["id", "handle_count"],
    group_by_fields=["created_at"],
    enable_filter_echo=True,
    aggregate_kwargs={"pagination_style": "offset"},
)

# People is the editable concrete contact surface. Keep the public roots
# (`people` / `person`) identical to the old plain paginated fields, but let the
# shared data-query owner advertise filters, ordering, aggregate, and group-by.
PersonDataQuery, _PERSON_DATA_TYPES = data_query(
    PersonType,
    type_name="PersonDataQuery",
    filters=PersonFilter,
    order=PersonOrder,
    list_name="people",
    detail_name="person",
    aggregate_name="person_aggregate",
    group_name="person_groups",
    aggregate_fields=["id"],
    group_by_fields=["folder", "folder__name", "created_at"],
    enable_filter_echo=True,
    aggregate_kwargs={"pagination_style": "offset"},
)

# Group handles by their resolved contact. Following Odoo's read_group: group by
# the party id (which owns the drill-down filter) and carry `party.display_name`
# in each bucket so the header shows the contact's name, not the raw id. The two
# axes yield the same buckets (id → name is 1:1). Scoped to owned handles: the
# relation filter input exposes only the public id (no `is_null`), so the
# per-bucket drill-down cannot express the null-party bucket; unowned handles stay
# in the flat, ungrouped list.
HandleDataQuery, _HANDLE_DATA_TYPES = data_query(
    HandleType,
    type_name="HandleDataQuery",
    filters=HandleFilter,
    order=HandleOrder,
    list_name="handles",
    detail_name="handle",
    aggregate_name="handle_aggregate",
    group_name="handle_groups",
    aggregate_fields=["id"],
    group_by_fields=["party", "party__display_name"],
    enable_filter_echo=True,
    aggregate_kwargs={
        "queryset": Handle.objects.filter(party__isnull=False),
        "pagination_style": "offset",
    },
)


@strawberry.type
class PartiesQuery:
    """Public parties queries."""

    organizations: OffsetPaginated[OrganizationType] = strawberry_django.offset_paginated()
    organization: OrganizationType | None = detail(OrganizationType)
    addresses: OffsetPaginated[AddressType] = strawberry_django.offset_paginated(
        filters=AddressFilter,
    )
    address: AddressType | None = detail(AddressType)
    affiliations: OffsetPaginated[AffiliationType] = strawberry_django.offset_paginated(
        filters=AffiliationFilter,
    )
    affiliation: AffiliationType | None = detail(AffiliationType)
    directories: OffsetPaginated[DirectoryType] = strawberry_django.offset_paginated()
    directory: DirectoryType | None = detail(DirectoryType)
    contact_folders: OffsetPaginated[ContactFolderType] = strawberry_django.offset_paginated()
    contact_folder: ContactFolderType | None = detail(ContactFolderType)


_DATA_TYPES = [*_PARTY_DATA_TYPES, *_PERSON_DATA_TYPES, *_HANDLE_DATA_TYPES]


_PARTIES_SCHEMA_BUCKET = {
    "query": [PartiesQuery, PartyDataQuery, PersonDataQuery, HandleDataQuery],
    "mutation": [
        PartiesDirectoryMutation,
        crud(PartyType, update=PartyPatch, delete=True),
        crud(PersonType, create=PersonInput, update=PersonPatch),
        crud(OrganizationType, create=OrganizationInput, update=OrganizationPatch),
        crud(HandleType, create=HandleInput, update=HandlePatch, delete=True),
        crud(AddressType, create=AddressInput, update=AddressPatch, delete=True),
        crud(AffiliationType, create=AffiliationInput, update=AffiliationPatch, delete=True),
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
        *_DATA_TYPES,
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
