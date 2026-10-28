"""Strawberry-Django schema contributions for parties.

``Party`` is the unified contact list (the multi-table-inheritance parent);
``Person`` and ``Organization`` are the concrete kinds you create and edit.
Asserted subjects leave active use through the Party's trash owner.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.db import transaction
from django.db.models import Exists, OuterRef, Prefetch
from rebac import system_context
from rebac.graphql.strawberry_django import optimize
from strawberry import auto
from strawberry_django.fields.field import StrawberryDjangoField
from strawberry_django.queryset import run_type_get_queryset

from angee.graphql.actions import ActionResult, action_guard, authorized_action_target
from angee.graphql.data.hasura import AngeeHasuraWriteBackend, declared_hasura_resource_fields, hasura_model_resource, public_pk_decoder
from angee.graphql.ids import PublicID, optional_public_id, require_instance_for_id
from angee.graphql.node import AngeeNode
from angee.graphql.relations import actor_scoped_to_one
from angee.graphql.subscriptions import changes
from angee.iam.audit import AuthoredRefMixin
from angee.iam.identity import user_public_id
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES, session_user
from angee.base.identity import public_subject_ref
from angee.integrate.schema import BridgeTypeMixin
from angee.parties.managers import DisplayAvatarPrefetches
from angee.parties.models import Party as PartyModel
from angee.parties.models import RelationshipKind as RelationshipKindModel
from angee.storage.schema import FileType
from angee.tags.schema import TaggedNode, tags_input_extensions

Party = apps.get_model("parties", "Party")
Person = apps.get_model("parties", "Person")
Organization = apps.get_model("parties", "Organization")
Handle = apps.get_model("parties", "Handle")
PartyStatement = apps.get_model("parties", "PartyStatement")
Address = apps.get_model("parties", "Address")
Directory = apps.get_model("parties", "Directory")
Folder = apps.get_model("parties", "Folder")
Circle = apps.get_model("parties", "Circle")
CircleMember = apps.get_model("parties", "CircleMember")
SameAsStatement = Party.same_as.model.statements.model
RelationshipKind = apps.get_model("parties", "RelationshipKind")
strawberry.enum(cast(Any, RelationshipKind.PartyKind))
Relationship = apps.get_model("parties", "Relationship")

_PARTY_EXTENSION_UPDATE_FIELDS = declared_hasura_resource_fields(Party, "hasura_updatable_fields")
_PARTY_EXTENSION_PUBLIC_ID_FIELDS = tuple(
    name for name in _PARTY_EXTENSION_UPDATE_FIELDS if Party._meta.get_field(name).is_relation
)

_PERSON_EXTENSION_INSERT_FIELDS = declared_hasura_resource_fields(Person, "hasura_insertable_fields")
_PERSON_EXTENSION_UPDATE_FIELDS = declared_hasura_resource_fields(Person, "hasura_updatable_fields")
_PERSON_EXTENSION_WRITE_FIELDS = tuple(
    dict.fromkeys((*_PERSON_EXTENSION_INSERT_FIELDS, *_PERSON_EXTENSION_UPDATE_FIELDS))
)
_PERSON_EXTENSION_PUBLIC_ID_FIELDS = tuple(
    name for name in _PERSON_EXTENSION_WRITE_FIELDS if Person._meta.get_field(name).is_relation
)

_ORGANIZATION_EXTENSION_INSERT_FIELDS = declared_hasura_resource_fields(Organization, "hasura_insertable_fields")
_ORGANIZATION_EXTENSION_UPDATE_FIELDS = declared_hasura_resource_fields(Organization, "hasura_updatable_fields")
_ORGANIZATION_EXTENSION_WRITE_FIELDS = tuple(
    dict.fromkeys((*_ORGANIZATION_EXTENSION_INSERT_FIELDS, *_ORGANIZATION_EXTENSION_UPDATE_FIELDS))
)
_ORGANIZATION_EXTENSION_PUBLIC_ID_FIELDS = tuple(
    name for name in _ORGANIZATION_EXTENSION_WRITE_FIELDS if Organization._meta.get_field(name).is_relation
)


def _display_avatar_prefetch(name: str) -> Callable[[strawberry.Info], Prefetch]:
    """Select the named read :meth:`PartyManager.display_avatar_prefetches` batches for a page.

    Avatars load through the actor's readable files, optimized for the selected
    ``FileType`` fields.
    """

    def prefetch(info: strawberry.Info) -> Prefetch:
        actor = session_user(info)
        field = cast(StrawberryDjangoField, info._field)
        files = apps.get_model("storage", "File").objects.with_actor(actor)
        avatars = optimize(run_type_get_queryset(files, field.django_type, info), info)
        return cast(Prefetch, getattr(Party.objects.display_avatar_prefetches(actor, avatars=avatars), name))

    return prefetch


@strawberry_django.type(Party)
class PartyType(AuthoredRefMixin, AngeeNode):
    """GraphQL projection of a party (the unified contact)."""

    display_name: auto
    notes: str | None
    handle_count: int | None
    first_met_note: str | None
    introduced_by: "PartyType | None" = actor_scoped_to_one("introduced_by")
    avatar: FileType | None = actor_scoped_to_one("avatar")
    created_at: auto
    updated_at: auto

    @strawberry_django.field(
        only=["avatar_id"],
        prefetch_related=[_display_avatar_prefetch(name) for name in DisplayAvatarPrefetches._fields],
    )
    def display_avatar(self, info: strawberry.Info) -> FileType | None:
        """Return the avatar the party shows: its own, else its strongest handle's."""

        return cast(Any, Party.objects.display_avatar(cast(Any, self), actor=session_user(info)))

    @strawberry.field
    def concrete_kind(parent: strawberry.Parent[PartyModel]) -> RelationshipKindModel.PartyKind | None:
        """Return the native MTI subtype used for canonical Party navigation."""

        return parent.concrete_kind

    handles: list["HandleType"]

    @strawberry_django.field(
        only=["id"],
        prefetch_related=lambda info: Prefetch(
            "partystatement_set",
            queryset=Party.objects.handle_statements()
            .for_reader(session_user(info))
            .select_related("subject", "handle"),
            to_attr="_readable_handle_statements",
        ),
    )
    def statements(self, info: strawberry.Info) -> list["PartyStatementType"]:
        return cast(Any, self)._readable_handle_statements

    addresses: list["AddressType"]
    circle_members: list["CircleMemberType"]
    relationships: list["PartyRelationshipType"]
    inbound_relationships: list["PartyRelationshipType"]


@strawberry_django.type(Person)
class PersonType(PartyType):
    """GraphQL projection of a person."""

    name_prefix: auto
    given_name: auto
    additional_name: auto
    family_name: auto
    name_suffix: auto
    nickname: str | None
    birthday: auto
    anniversary: auto

    @strawberry_django.field(only=["id"])
    def circle_names(self) -> list[str]:
        """Return manager-prefetched, actor-visible circle names as chip values."""

        return cast("list[str]", Person.objects.circle_names_for(cast(Any, self)))

    @strawberry_django.field(only=["user_id"])
    def user(self) -> strawberry.ID | None:
        """Return the linked platform user's public id, when this person is one."""

        return optional_public_id(user_public_id(cast(Any, self).user_id))


@strawberry_django.type(Organization)
class OrganizationType(PartyType):
    """GraphQL projection of an organisation."""

    legal_name: auto
    domain: auto


@strawberry_django.type(Handle)
class HandleType(AngeeNode):
    """GraphQL projection of a handle (a party's reachable address)."""

    platform: auto
    value: auto
    normalized_value: auto
    external_id: auto
    display_name: auto
    label: auto
    is_preferred: auto
    is_verified: auto
    party_link_confirmed: auto
    created_at: auto
    updated_at: auto

    party: PartyType | None = actor_scoped_to_one("party")
    avatar: FileType | None = actor_scoped_to_one("avatar")

    @strawberry_django.field(only=["controller_id"])
    def controller(self) -> strawberry.ID | None:
        """Return the controlling user's public id, when a user has claimed this handle."""

        return optional_public_id(user_public_id(cast(Any, self).controller_id))

    @strawberry_django.field(
        only=["party_id"],
        annotate={
            "_resolved_confidence": lambda info: Handle.objects.with_actor(
                session_user(info)
            ).resolved_confidence_expression()
        },
    )
    def confidence(self) -> float | None:
        """Batch the subject-authorized resolution confidence."""
        return cast(Any, self).resolved_confidence


@strawberry.type
class StatementEvidenceType:
    """One independently readable origin."""

    model: str
    id: strawberry.ID
    pointer: str


@strawberry_django.type(PartyStatement)
class PartyStatementType(AngeeNode):
    """Subject-authorized handle assertion; the member retains its read gate."""

    subject: PartyType | None = actor_scoped_to_one("subject")
    handle: HandleType | None = actor_scoped_to_one("handle")
    confidence: auto
    source_kind: auto
    rank: auto
    snak: auto
    status: auto
    applied: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["asserted_by", "source_kind"])
    def asserted_by(self) -> str:
        """The asserting principal when the reader may read it; otherwise only its source kind.

        A statement read never widens what the party reader sees: the principal
        resolves through its own read gate, like ``handle`` and ``subject``.
        """

        row = cast(Any, self)
        source = row.readable_asserting_source
        if source is not None:
            return str(public_subject_ref(source))
        return str(row.source_kind)

    @strawberry_django.field(only=["rank", "snak"])
    def is_confirmed(self) -> bool:
        return bool(cast(Any, self).is_confirmed)

    @strawberry_django.field(only=["rank", "snak"])
    def is_dismissed(self) -> bool:
        return bool(cast(Any, self).is_dismissed)

    @strawberry_django.field(only=["subject_id", "evidence"])
    def evidence(self, info: strawberry.Info) -> list[StatementEvidenceType]:
        row = cast(Any, self)
        refs = row.__dict__.get("_readable_evidence")
        if refs is None:
            refs = row.readable_evidence(session_user(info))
        return [StatementEvidenceType(model=ref.model, id=strawberry.ID(ref.id), pointer=ref.pointer) for ref in refs]

    @strawberry_django.field(only=["subject_id", "evidence"])
    def evidence_truncated(self, info: strawberry.Info) -> bool:
        return bool(cast(Any, self).evidence_truncated)


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


@strawberry_django.type(Folder)
class ContactFolderType(AngeeNode):
    """GraphQL projection of a contact folder (a synced address book's parties).

    Named distinctly from storage's file ``FolderType`` — a different concept.
    """

    name: auto
    directory: "DirectoryType | None"
    source_href: auto
    created_at: auto
    updated_at: auto


@strawberry_django.type(Directory)
class DirectoryType(BridgeTypeMixin, AngeeNode):
    """GraphQL projection of a connected contacts directory (e.g. a CardDAV source)."""

    poll_interval: auto


@strawberry_django.type(Circle)
class CircleType(AngeeNode):
    """GraphQL projection of a circle (a private, overlapping grouping of parties)."""

    name: auto
    description: auto
    color: auto
    icon: auto
    position: auto
    parent: "CircleType | None"
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["id"])
    def member_count(self) -> int:
        """Return the manager-annotated distinct subtree member count."""

        return int(getattr(self, "_member_count", 0))


@strawberry_django.type(CircleMember)
class CircleMemberType(AngeeNode):
    """GraphQL projection of a party's circle membership."""

    circle: CircleType | None
    party: PartyType | None
    confidence: auto
    is_confirmed: auto
    is_dismissed: auto
    created_at: auto


@strawberry_django.type(SameAsStatement)
class SameAsStatementType(AngeeNode):
    """A symmetric identity statement, admitted and read through its endpoints."""

    snak: auto
    rank: auto
    status: auto


@strawberry_django.type(RelationshipKind)
class RelationshipKindType(AngeeNode):
    """GraphQL projection of a relationship-vocabulary kind."""

    slug: auto
    name: auto
    inverse_name: auto
    category: auto
    party_kind: auto
    other_party_kind: auto


@strawberry_django.type(Relationship)
class PartyRelationshipType(AngeeNode):
    """GraphQL projection of a typed party edge (anchor viewpoint)."""

    party: PartyType | None
    other_party: PartyType | None
    other_name: auto
    kind: RelationshipKindType | None
    title: auto
    started_at: auto
    ended_at: auto
    notes: auto
    created_at: auto


@strawberry.type
class SameAsPairType:
    """Two actor-visible parties sharing one deterministic normalized handle."""

    left: PartyType
    right: PartyType
    normalized_value: str


@strawberry.enum
class PeopleWorkbenchScope(StrEnum):
    """The bounded smart-view vocabulary for the People workbench."""

    ALL = "all"
    UNASSIGNED = "unassigned"
    TO_REVIEW = "to_review"
    CIRCLE = "circle"


@strawberry.type
class PeopleWorkbenchType:
    """Circle navigation, smart counts, and the selected people row scope."""

    circles: list[CircleType]
    all_count: int
    unassigned_count: int
    to_review_count: int
    filtered_ids: list[strawberry.ID] | None
    truncated: bool


@strawberry.type
class PartiesReviewQuery:
    """Bounded read surfaces for party search and human review queues."""

    @strawberry.field
    def same_as_pairs(self, limit: int = 50) -> list[SameAsPairType]:
        """Return resolved identity statements awaiting a survivor choice."""

        return cast(
            "list[SameAsPairType]",
            Party.objects.all().same_as_pairs(limit=limit),
        )

    @strawberry.field
    def search_parties(self, query: str, limit: int = 20) -> list[PersonType]:
        """Return actor-visible people whose display name contains ``query``."""

        return cast("list[PersonType]", Person.objects.search_display_name(query, limit=limit))

    @strawberry.field
    def people_workbench(
        self,
        scope: PeopleWorkbenchScope = PeopleWorkbenchScope.ALL,
        circle: strawberry.ID | None = None,
        limit: int = 1000,
    ) -> PeopleWorkbenchType:
        """Return actor-scoped People navigation and the selected scope's ids."""

        people = Person.objects.all().scoped_for_aggregate().canonical()
        filtered = None
        if scope is PeopleWorkbenchScope.UNASSIGNED:
            filtered = people.unassigned()
        elif scope is PeopleWorkbenchScope.TO_REVIEW:
            filtered = people.to_review()
        elif scope is PeopleWorkbenchScope.CIRCLE:
            selected = require_instance_for_id(
                Circle,
                circle or "",
                queryset=Circle.objects.all(),
                not_found="circle not found",
            )
            filtered = people.in_circle(selected)

        bounded_limit = max(1, min(int(limit), 1000))
        filtered_values = (
            list(filtered.values_list("sqid", flat=True)[: bounded_limit + 1]) if filtered is not None else []
        )
        return PeopleWorkbenchType(
            circles=cast(
                "list[CircleType]",
                list(Circle.objects.all().with_member_counts().select_related("parent")),
            ),
            all_count=people.count(),
            unassigned_count=people.unassigned().count(),
            to_review_count=people.to_review().count(),
            filtered_ids=(
                [strawberry.ID(str(value)) for value in filtered_values[:bounded_limit]]
                if filtered is not None
                else None
            ),
            truncated=len(filtered_values) > bounded_limit,
        )


@strawberry.type
class PartiesIdentityMutation:
    """Human decisions on party statements and same_as pairs."""

    @strawberry.mutation
    @action_guard("Could not add this contact.")
    def propose_manual_contact(
        self,
        info: strawberry.Info,
        party_id: PublicID,
        platform: str,
        value: str,
        label: str,
    ) -> ActionResult:
        """Add an unconfirmed email or phone claim to a writable party."""

        actor = session_user(info)
        party = authorized_action_target(info, Party, party_id, "write")
        statement = Party.objects.propose_manual_contact(
            party,
            platform=platform,
            value=value,
            label=label,
            actor=actor,
        )
        if statement.is_dismissed:
            return ActionResult(
                ok=False,
                message=("This contact remains dismissed. Confirm it in Identity if it should be restored."),
            )
        return ActionResult(ok=True, message="Contact is available in Identity for review.", id=statement.sqid)

    @strawberry.mutation
    def confirm_party_statement(self, info: strawberry.Info, id: strawberry.ID) -> PartyStatementType:
        """Confirm a handle statement (the review queue's accept)."""

        link = require_instance_for_id(
            PartyStatement,
            id,
            queryset=Party.objects.handle_statements().for_reader(session_user(info)),
            not_found="party statement not found",
        )
        return cast(PartyStatementType, link.with_actor(session_user(info)).confirm())

    @strawberry.mutation
    def dismiss_party_statement(self, info: strawberry.Info, id: strawberry.ID) -> PartyStatementType:
        """Dismiss a handle statement — the durable anti-link (the review queue's reject)."""

        link = require_instance_for_id(
            PartyStatement,
            id,
            queryset=Party.objects.handle_statements().for_reader(session_user(info)),
            not_found="party statement not found",
        )
        return cast(PartyStatementType, link.with_actor(session_user(info)).dismiss())

    @strawberry.mutation
    def merge_parties(
        self,
        into_id: strawberry.ID,
        from_id: strawberry.ID,
        field_overrides: strawberry.scalars.JSON | None = None,
    ) -> PartyType:
        """Merge one writable party into another through the model-owned transaction."""

        survivor = Party.objects.all().from_public_id(str(into_id))
        source = Party.objects.all().from_public_id(str(from_id))
        if survivor is None or source is None:
            raise ValueError("party not found")
        return cast(
            PartyType,
            survivor.merge(records=[source.public_id], field_overrides=field_overrides),
        )

    @strawberry.mutation
    def dismiss_same_as(self, a_id: strawberry.ID, b_id: strawberry.ID) -> SameAsStatementType:
        """Persist the durable keep-separate decision for two writable parties."""

        party_a = Party.objects.all().from_public_id(str(a_id))
        party_b = Party.objects.all().from_public_id(str(b_id))
        if party_a is None or party_b is None:
            raise ValueError("party not found")
        return cast(SameAsStatementType, Party.same_as.for_pair(party_a, party_b).dismiss(aspect="same_as"))


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
        """Create a Basic-auth credential and a connected CardDAV directory to sync.

        ``server_url`` is the account/server URL — discovery finds the address
        books, so no exact collection URL is needed. The directory is created
        ``connected`` and owned by the calling admin; ``syncIntegration`` then pulls its
        contacts into one :class:`~angee.parties.models.Folder` per address book.
        """

        user = session_user(info)
        credential_model = apps.get_model("integrate", "Credential")
        vendor_model = apps.get_model("integrate", "Vendor")
        credential_name = f"CardDAV — {name}"
        credential_material = {"username": username, "password": password}
        directory_values = {
            "owner": user,
            "backend_class": "carddav",
            "display_name": name,
            "config": {"server_url": server_url},
            "lifecycle": "connected",
            "created_by_id": user.pk,
        }

        # Complete validation and network discovery before opening the database
        # transaction. Both rows are intentionally unsaved probe inputs.
        probe_credential = credential_model.objects.prepare_local_credential(
            user,
            kind="basic_auth",
            name=credential_name,
            material=credential_material,
        )
        Directory(credential=probe_credential, **directory_values).backend.probe()

        # Persist every owned row in one write-only transaction after the probe.
        with system_context(reason="parties.graphql.connect_carddav"), transaction.atomic():
            credential = credential_model.objects.create_local_credential(
                user,
                kind="basic_auth",
                name=credential_name,
                material=credential_material,
            )
            vendor, _created = vendor_model.objects.get_or_create(slug="carddav", defaults={"display_name": "CardDAV"})
            directory = Directory.objects.create(
                vendor=vendor,
                credential=credential,
                **directory_values,
            )
            # The connected account's own login address is the admin's own handle:
            # claim it (control + normal person identity statement). Synced
            # *contacts'* handles get neither fact — only this connected account's.
            if username.strip():
                Handle.objects.claim_control(
                    user,
                    platform=Handle.Platform.for_value(username),
                    value=username.strip(),
                    asserted_by=user,
                    evidence=[directory],
                    display_name=name,
                )
        return cast(DirectoryType, directory)


_PARTY_RESOURCE = hasura_model_resource(
    PartyType,
    model=Party,
    name="parties",
    filterable=list(
        dict.fromkeys(
            [
                "id",
                "display_name",
                "created_at",
                "updated_at",
                *declared_hasura_resource_fields(Party, "hasura_filterable_fields"),
            ]
        )
    ),
    sortable=[
        "display_name",
        "created_at",
        "updated_at",
        *declared_hasura_resource_fields(Party, "hasura_sortable_fields"),
    ],
    aggregatable=["id"],
    groupable=list(
        dict.fromkeys(
            [
                "created_at",
                *declared_hasura_resource_fields(Party, "hasura_groupable_fields"),
            ]
        )
    ),
    insert=False,
    updatable=["display_name", "notes", *_PARTY_EXTENSION_UPDATE_FIELDS],
    write_backend=AngeeHasuraWriteBackend(Party, public_id_fields=_PARTY_EXTENSION_PUBLIC_ID_FIELDS),
)
_PERSON_RESOURCE = hasura_model_resource(
    PersonType,
    model=Person,
    name="people",
    filterable=list(
        dict.fromkeys(
            [
                "id",
                "display_name",
                "given_name",
                "family_name",
                "created_at",
                "updated_at",
                *declared_hasura_resource_fields(Person, "hasura_filterable_fields"),
            ]
        )
    ),
    sortable=[
        "display_name",
        "given_name",
        "family_name",
        "created_at",
        "updated_at",
        *declared_hasura_resource_fields(Person, "hasura_sortable_fields"),
    ],
    aggregatable=["id"],
    groupable=list(
        dict.fromkeys(
            [
                "created_at",
                *declared_hasura_resource_fields(Person, "hasura_groupable_fields"),
            ]
        )
    ),
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
        *_PERSON_EXTENSION_INSERT_FIELDS,
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
        *_PERSON_EXTENSION_UPDATE_FIELDS,
    ],
    delete=False,
    write_backend=AngeeHasuraWriteBackend(
        Person,
        public_id_fields=_PERSON_EXTENSION_PUBLIC_ID_FIELDS,
    ),
    get_queryset=lambda info: Person.objects.all().with_circle_names(),
)
_ORGANIZATION_RESOURCE = hasura_model_resource(
    OrganizationType,
    model=Organization,
    name="organizations",
    filterable=list(
        dict.fromkeys(
            [
                "id",
                "display_name",
                "legal_name",
                "domain",
                "created_at",
                "updated_at",
                *declared_hasura_resource_fields(Organization, "hasura_filterable_fields"),
            ]
        )
    ),
    sortable=[
        "display_name",
        "legal_name",
        "domain",
        "created_at",
        "updated_at",
        *declared_hasura_resource_fields(Organization, "hasura_sortable_fields"),
    ],
    aggregatable=["id"],
    groupable=list(
        dict.fromkeys(
            [
                "domain",
                "created_at",
                *declared_hasura_resource_fields(Organization, "hasura_groupable_fields"),
            ]
        )
    ),
    insertable=[
        "display_name",
        "notes",
        "legal_name",
        "domain",
        *_ORGANIZATION_EXTENSION_INSERT_FIELDS,
    ],
    updatable=[
        "display_name",
        "notes",
        "legal_name",
        "domain",
        *_ORGANIZATION_EXTENSION_UPDATE_FIELDS,
    ],
    delete=False,
    write_backend=AngeeHasuraWriteBackend(
        Organization,
        public_id_fields=_ORGANIZATION_EXTENSION_PUBLIC_ID_FIELDS,
    ),
)
_HANDLE_RESOURCE = hasura_model_resource(
    HandleType,
    model=Handle,
    name="handles",
    get_queryset=lambda info: Handle.objects.canonical(),
    filterable=[
        "id",
        "value",
        "display_name",
        "party",
        "platform",
        "is_verified",
        "is_preferred",
        "created_at",
    ],
    sortable=["platform", "value", "created_at"],
    aggregatable=["id"],
    groupable=["party", "party__display_name"],
    # Handle identity and its resolved Party projection are maintained by
    # ingestion, connection, and PartyStatement owners, never generic CRUD.
    insert=False,
    update=False,
    delete=False,
    record_representation="value",
)
_ADDRESS_RESOURCE = hasura_model_resource(
    AddressType,
    model=Address,
    name="addresses",
    filterable=["id", "party", "label", "created_at"],
    sortable=["party", "label", "is_primary", "created_at", "updated_at"],
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
    get_queryset=lambda info: Address.objects.canonical(),
)
_PARTY_STATEMENT_RESOURCE = hasura_model_resource(
    PartyStatementType,
    model=PartyStatement,
    name="party_statements",
    filterable=[
        "id",
        "subject",
        "handle",
        "confidence",
        "asserted_by",
        "source_kind",
        "rank",
        "snak",
        "status",
        "applied",
        "created_at",
        "to_review",
    ],
    filter_expressions={
        "to_review": Exists(Party.statements.to_review().filter(pk=OuterRef("pk"))),
    },
    sortable=["confidence", "rank", "created_at", "updated_at"],
    aggregatable=["id", "confidence"],
    # No handle-field axis: a group key would read handle rows the party reader may
    # not read; ``handle`` resolves through its own gate.
    groupable=["handle", "subject", "subject__display_name", "source_kind"],
    insert=False,
    update=False,
    delete=False,
    field_id_decode={"subject": public_pk_decoder(Party), "handle": public_pk_decoder(Handle)},
    get_queryset=lambda info: (
        Party.objects.handle_statements()
        .for_reader(session_user(info))
        .with_readable_records(session_user(info))
        .members()
        .select_related("subject", "handle")
    ),
)
_CIRCLE_RESOURCE = hasura_model_resource(
    CircleType,
    model=Circle,
    name="circles",
    filterable=["id", "name", "parent", "created_at", "updated_at"],
    sortable=["position", "name", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["parent", "parent__name"],
    insertable=["name", "description", "color", "icon", "position", "parent"],
    updatable=["name", "description", "color", "icon", "position", "parent"],
    field_id_decode={"parent": public_pk_decoder(Circle)},
    write_backend=AngeeHasuraWriteBackend(Circle, public_id_fields=("parent",)),
    get_queryset=lambda info: Circle.objects.all().with_member_counts(),
)
_CIRCLE_MEMBER_RESOURCE = hasura_model_resource(
    CircleMemberType,
    model=CircleMember,
    name="circle_members",
    filterable=["id", "circle", "party", "confidence", "created_at"],
    sortable=["circle", "confidence", "created_at"],
    aggregatable=["id"],
    groupable=["circle", "circle__name", "party", "party__display_name"],
    # A human names the membership pair; its score and decision flags are
    # server-owned.
    insertable=["circle", "party"],
    update=False,
    field_id_decode={
        "circle": public_pk_decoder(Circle),
        "party": public_pk_decoder(Party),
    },
    write_backend=AngeeHasuraWriteBackend(CircleMember, public_id_fields=("circle", "party")),
)
_RELATIONSHIP_KIND_RESOURCE = hasura_model_resource(
    RelationshipKindType,
    model=RelationshipKind,
    name="relationship_kinds",
    filterable=["id", "slug", "name", "category"],
    sortable=["slug", "name", "category"],
    aggregatable=["id"],
    groupable=["category"],
    insertable=["slug", "name", "inverse_name", "category", "party_kind", "other_party_kind"],
    updatable=["name", "inverse_name", "category", "party_kind", "other_party_kind"],
)
_RELATIONSHIP_RESOURCE = hasura_model_resource(
    PartyRelationshipType,
    model=Relationship,
    name="party_relationships",
    filterable=["id", "party", "other_party", "kind", "started_at", "ended_at", "created_at"],
    sortable=["kind", "started_at", "created_at"],
    aggregatable=["id"],
    groupable=["kind", "kind__name"],
    insertable=["party", "other_party", "other_name", "kind", "title", "started_at", "ended_at", "notes"],
    updatable=["other_party", "other_name", "kind", "title", "started_at", "ended_at", "notes"],
    field_id_decode={
        "party": public_pk_decoder(Party),
        "other_party": public_pk_decoder(Party),
        "kind": public_pk_decoder(RelationshipKind),
    },
    write_backend=AngeeHasuraWriteBackend(Relationship, public_id_fields=("party", "other_party", "kind")),
    get_queryset=lambda info: Relationship.objects.canonical(),
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
        "lifecycle",
        "runtime_status",
        "sync_stage",
        "last_sync_completed_at",
        "updated_at",
    ],
    sortable=["display_name", "backend_class", "lifecycle", "runtime_status", "last_sync_completed_at", "updated_at"],
    aggregatable=["id", "last_sync_items"],
    groupable=["backend_class", "lifecycle", "runtime_status", "sync_stage"],
    insert=False,
    update=False,
    delete=False,
)


@strawberry_django.type(Party, name="PartyType", extend=True)
class PartyTags(TaggedNode):
    """Console-only tags on a party; the public party node stays unchanged."""


@strawberry_django.type(Person, name="PersonType", extend=True)
class PersonTags(TaggedNode):
    """Console-only tags on a person, which its party carries."""


@strawberry_django.type(Organization, name="OrganizationType", extend=True)
class OrganizationTags(TaggedNode):
    """Console-only tags on an organization, which its party carries."""


@strawberry_django.type(Handle, name="HandleType", extend=True)
class HandleTags(TaggedNode):
    """Console-only tags on a handle; its resource takes no writes, so the ``tag`` verbs write them."""


_RESOURCE_TYPES = [
    *_PARTY_RESOURCE.types,
    *_PERSON_RESOURCE.types,
    *_ORGANIZATION_RESOURCE.types,
    *_HANDLE_RESOURCE.types,
    *_PARTY_STATEMENT_RESOURCE.types,
    *_ADDRESS_RESOURCE.types,
    *_CIRCLE_RESOURCE.types,
    *_CIRCLE_MEMBER_RESOURCE.types,
    *_RELATIONSHIP_KIND_RESOURCE.types,
    *_RELATIONSHIP_RESOURCE.types,
    *_CONTACT_FOLDER_RESOURCE.types,
    *_DIRECTORY_RESOURCE.types,
]


_PARTIES_SCHEMA_BUCKET = {
    "query": [
        PartiesReviewQuery,
        _PARTY_RESOURCE.query,
        _PERSON_RESOURCE.query,
        _ORGANIZATION_RESOURCE.query,
        _HANDLE_RESOURCE.query,
        _PARTY_STATEMENT_RESOURCE.query,
        _ADDRESS_RESOURCE.query,
        _CIRCLE_RESOURCE.query,
        _CIRCLE_MEMBER_RESOURCE.query,
        _RELATIONSHIP_KIND_RESOURCE.query,
        _RELATIONSHIP_RESOURCE.query,
        _CONTACT_FOLDER_RESOURCE.query,
        _DIRECTORY_RESOURCE.query,
    ],
    "mutation": [
        PartiesDirectoryMutation,
        PartiesIdentityMutation,
        _PARTY_RESOURCE.mutation,
        _PERSON_RESOURCE.mutation,
        _ORGANIZATION_RESOURCE.mutation,
        _HANDLE_RESOURCE.mutation,
        _PARTY_STATEMENT_RESOURCE.mutation,
        _ADDRESS_RESOURCE.mutation,
        _CIRCLE_RESOURCE.mutation,
        _CIRCLE_MEMBER_RESOURCE.mutation,
        _RELATIONSHIP_KIND_RESOURCE.mutation,
        _RELATIONSHIP_RESOURCE.mutation,
        _CONTACT_FOLDER_RESOURCE.mutation,
        _DIRECTORY_RESOURCE.mutation,
    ],
    "types": [
        PartyType,
        PersonType,
        OrganizationType,
        HandleType,
        PartyStatementType,
        AddressType,
        CircleType,
        CircleMemberType,
        SameAsStatementType,
        SameAsPairType,
        PeopleWorkbenchScope,
        PeopleWorkbenchType,
        RelationshipKindType,
        PartyRelationshipType,
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
            changes(Directory, field="directoryChanged"),
            changes(Circle, field="circleChanged"),
            changes(CircleMember, field="circleMemberChanged"),
            changes(Relationship, field="relationshipChanged"),
        ],
        "type_extensions": [PartyTags, PersonTags, OrganizationTags, HandleTags],
        "input_extensions": [
            *tags_input_extensions(_PARTY_RESOURCE),
            *tags_input_extensions(_PERSON_RESOURCE),
            *tags_input_extensions(_ORGANIZATION_RESOURCE),
        ],
    },
}
