"""A directory's contact readers read the contacts it links, and nothing else.

``parties/directory#read_contacts`` (the ``contact_reader`` grantees and the
integration's owner) lends identity read on the parties the directory's live
links name, and read on the handles, addresses and relationships those links
state in a current claim. Private fields, writes, handles only another source
asserted and everything of the directory itself (its integration, streams, links
and credential) stay with their own gates.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    SubjectRef,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.backends import backend
from rebac.models import active_relationship_model

from angee.graphql.testing.documents import execute_schema, result_data
from angee.integrate.states import StreamKind
from angee.integrate.testing.models import RecordLink
from angee.messaging.testing.models import (
    Address,
    Directory,
    Handle,
    Organization,
    Party,
    Person,
    Relationship,
    RelationshipKind,
)
from angee.parties.models import Directory as AbstractDirectory
from tests.conftest import create_platform_admin, current_stream, make_integration
from tests.iam_models import Group
from tests.test_parties_graphql import _schema as parties_graphql_schema

PERSON_PRIVATE: dict[str, Any] = {
    "notes": "Private notes",
    "first_met_note": "Met at a lecture",
    "nickname": "Ada",
    "birthday": date(1990, 2, 3),
    "anniversary": date(2020, 4, 5),
}
ORGANIZATION_PRIVATE: dict[str, Any] = {"notes": "Private notes", "first_met_note": "Introduced by a friend"}
PARTY_GATES = ("read__notes", "read__first_met_note", "read__handle_count")
PERSON_GATES = (*PARTY_GATES, "read__birthday", "read__anniversary", "read__nickname")
SCOPED_ACTIONS = ("read", "read_private", "write", "delete")


def _allowed(row: Any, actor: Any, action: str = "read") -> bool:
    return bool(
        backend().check_access(subject=to_subject_ref(actor), action=action, resource=to_object_ref(row)).allowed
    )


def _scoped(row: Any, actor: Any, action: str = "read") -> bool:
    return type(row).objects.with_actor(actor).with_action(action).filter(pk=row.pk).exists()


def _grant(row: Any, relation: str, subject: Any) -> None:
    subject_ref = subject if isinstance(subject, SubjectRef) else to_subject_ref(subject)
    write_relationships([RelationshipTuple(to_object_ref(row), relation, subject_ref)])


def _state(row: Any, aspect: str, *, source: Any, link: Any) -> None:
    """State ``row``'s current values for ``aspect`` through ``link``, as directory ingest does."""

    values = {name: getattr(row, name) for name in row.statements.model.statement_aspects[aspect].fields}
    row.statements.set(aspect=aspect, values=values, asserted_by=source, link=link)


@pytest.fixture
def contacts(db: None) -> Iterator[SimpleNamespace]:
    """One directory linking a person and an organization, stating an address, an edge and a handle."""

    del db
    users = get_user_model()
    with override_settings(REBAC_FIELD_READ_MODE="redact"):
        with system_context(reason="tests.directory_readers.seed"):
            directory = make_integration("shared-contacts", model=Directory)
            other = make_integration("other-contacts", model=Directory)
            owner = directory.owner
            reader = users.objects.create_user(username="contacts-reader")
            member = users.objects.create_user(username="contacts-group-member")
            integration_reader = users.objects.create_user(username="contacts-integration-reader")
            outsider = users.objects.create_user(username="contacts-outsider")
            group = Group.objects.create(name="Contact readers")
            group.add_member(str(SubjectRef(to_object_ref(member))))

            person = Person.objects.create(display_name="Ada Example", created_by=owner, **PERSON_PRIVATE)
            organization = Organization.objects.create(
                display_name="Example Ltd", created_by=owner, **ORGANIZATION_PRIVATE
            )
            unlinked = Person.objects.create(display_name="Grace Unlinked", created_by=owner, **PERSON_PRIVATE)
            stream = current_stream(directory, "contacts", kind=StreamKind.RECORD_REPLICA)
            person_link = RecordLink.objects.observe(stream, "ada", target=person)
            RecordLink.objects.observe(stream, "example-ltd", target=organization)
            source = stream.integration

            address = Address.objects.create(
                party_id=person.pk, street="1 Example Street", city="Springfield", country="US", created_by=owner
            )
            _state(address, "address", source=source, link=person_link)
            employee = RelationshipKind.objects.create(
                slug="employee",
                name="Employee",
                inverse_name="Employer",
                category="professional",
                other_party_kind="organization",
            )
            edge = Relationship.objects.create(
                party_id=person.pk, other_name="Example Ltd", kind=employee, title="Engineer", created_by=owner
            )
            _state(edge, "relationship", source=source, link=person_link)
            person_party = Party.objects.get(pk=person.pk)
            stated_handle = Handle.objects.create(platform="email", value="ada@example.com", created_by=owner)
            Party.objects.assert_handle(person_party, stated_handle, asserted_by=source, link=person_link)
            # A handle only the owner asserted, on the same shared person.
            own_handle = Handle.objects.create(platform="phone", value="+15550100", created_by=owner)
            Party.objects.assert_handle(person_party, own_handle, asserted_by=owner)

            _grant(directory, "contact_reader", reader)
            _grant(directory, "contact_reader", SubjectRef.of("auth/group", str(group.pk), "member"))
            _grant(directory.integration_ptr, "reader", integration_reader)
        admin = create_platform_admin("contacts-admin")
        yield SimpleNamespace(
            directory=directory,
            other=other,
            stream=stream,
            source=source,
            person_link=person_link,
            owner=owner,
            reader=reader,
            member=member,
            integration_reader=integration_reader,
            outsider=outsider,
            admin=admin,
            person=person,
            person_party=person_party,
            organization=organization,
            unlinked=unlinked,
            address=address,
            edge=edge,
            stated_handle=stated_handle,
            own_handle=own_handle,
        )


@pytest.fixture(scope="module")
def graphql() -> Any:
    return parties_graphql_schema("public")


def test_contact_readers_read_linked_identity_without_private_fields_or_writes(contacts: SimpleNamespace) -> None:
    """Owner, user and group grantees, the integration's reader, an outsider and an admin, per subject."""

    c = contacts
    actors = (c.owner, c.reader, c.member, c.integration_reader, c.outsider, c.admin)
    managers = {c.owner, c.admin}
    contact_readers = {c.reader, c.member}
    cases: list[tuple[Any, str, set[Any]]] = []
    for row, gates in ((c.person, PERSON_GATES), (c.person_party, PARTY_GATES), (c.organization, PARTY_GATES)):
        cases.append((row, "read", managers | contact_readers))
        cases += [(row, action, managers) for action in ("read_private", *gates, "write", "delete")]
    for row in (c.address, c.edge, c.stated_handle):
        cases.append((row, "read", managers | contact_readers))
        cases += [(row, action, managers) for action in ("write", "delete")]
    cases += [
        (c.unlinked, "read", managers),
        # A handle only the owner asserted keeps its own gates on the same shared person.
        (c.own_handle, "read", managers),
    ]
    for row, action, expected in cases:
        for actor in actors:
            allowed = actor in expected
            label = (type(row).__name__, action, actor.username)
            assert _allowed(row, actor, action) is allowed, label
            if action in SCOPED_ACTIONS:
                assert _scoped(row, actor, action) is allowed, label
    # Every reach above is backed by live rows: the grants on the directory are the only tuples.
    for kind in ("party", "person", "organization", "address", "handle"):
        resource_type = f"parties/{kind}"
        assert not active_relationship_model().objects.filter(resource_type=resource_type).exists()


def test_contact_reader_reads_nothing_of_the_directory_or_its_sync_evidence(contacts: SimpleNamespace) -> None:
    """The grant reaches no directory, integration, stream, link payload or credential row."""

    c = contacts
    evidence = (c.directory, c.directory.integration_ptr, c.stream, c.person_link)
    for actor in (c.reader, c.member):
        for row in (*evidence, c.directory.credential):
            assert not _allowed(row, actor), (type(row).__name__, actor.username)
            assert not _scoped(row, actor), (type(row).__name__, actor.username)
    # The integration's reader reads that evidence, and reads none of the contacts.
    for row in evidence:
        assert _allowed(row, c.integration_reader), type(row).__name__


def test_contact_reader_lists_and_opens_linked_contacts_through_graphql(
    contacts: SimpleNamespace, graphql: Any
) -> None:
    """The list and record roots return what the grant reads, with every private field redacted."""

    c = contacts
    query = """query Contacts($id: String!) {
      people { id display_name notes first_met_note nickname birthday anniversary }
      people_by_pk(id: $id) { id display_name notes birthday }
      organizations { id display_name notes first_met_note }
      addresses { id street }
      party_relationships { id title }
      handles { id }
      directories { id }
    }"""
    for actor in (c.reader, c.member):
        data = result_data(execute_schema(graphql, query, {"id": c.person.sqid}, user=actor))
        assert data["people"] == [
            {
                "id": c.person.sqid,
                "display_name": "Ada Example",
                **dict.fromkeys(("notes", "first_met_note", "nickname", "birthday", "anniversary")),
            }
        ]
        assert data["people_by_pk"] == {
            "id": c.person.sqid,
            "display_name": "Ada Example",
            "notes": None,
            "birthday": None,
        }
        assert data["organizations"] == [
            {"id": c.organization.sqid, "display_name": "Example Ltd", "notes": None, "first_met_note": None}
        ]
        assert data["addresses"] == [{"id": c.address.sqid, "street": "1 Example Street"}]
        assert data["party_relationships"] == [{"id": c.edge.sqid, "title": "Engineer"}]
        assert data["handles"] == [{"id": c.stated_handle.sqid}]
        assert data["directories"] == []
    owner = result_data(execute_schema(graphql, query, {"id": c.person.sqid}, user=c.owner))
    assert owner["people_by_pk"]["notes"] == PERSON_PRIVATE["notes"]
    assert {row["id"] for row in owner["people"]} == {c.person.sqid, c.unlinked.sqid}
    for actor in (c.outsider, c.integration_reader):
        data = result_data(execute_schema(graphql, query, {"id": c.person.sqid}, user=actor))
        assert data["people"] == data["organizations"] == data["addresses"] == data["party_relationships"] == []
        assert data["handles"] == []
        assert data["people_by_pk"] is None
    denied = execute_schema(
        graphql,
        """mutation Rename($id: String!) {
          update_people_by_pk(pk_columns: {id: $id}, _set: {family_name: "Changed"}) { id }
        }""",
        {"id": c.person.sqid},
        user=c.reader,
    )
    assert denied.errors or denied.data["update_people_by_pk"] is None
    with system_context(reason="tests.directory_readers.verify"):
        assert Person.objects.get(pk=c.person.pk).family_name == ""


def test_a_person_linked_by_two_directories_reads_through_either_until_its_link_is_tombstoned(
    contacts: SimpleNamespace,
) -> None:
    """Each directory lends the shared person; only what a directory stated follows its readers."""

    c = contacts
    other_reader = get_user_model().objects.create_user(username="other-contacts-reader")
    with system_context(reason="tests.directory_readers.second_directory"):
        other_stream = current_stream(c.other, "contacts", kind=StreamKind.RECORD_REPLICA)
        other_link = RecordLink.objects.observe(other_stream, "ada-elsewhere", target=c.person)
        other_handle = Handle.objects.create(platform="email", value="ada@elsewhere.example", created_by=c.owner)
        Party.objects.assert_handle(c.person_party, other_handle, asserted_by=c.other.integration_ptr, link=other_link)
        _grant(c.other, "contact_reader", other_reader)
    for actor in (c.reader, other_reader):
        for row in (c.person, c.person_party):
            assert _allowed(row, actor) and _scoped(row, actor), (type(row).__name__, actor.username)
    # Each directory lends the handle it asserted, and only that one.
    assert _allowed(other_handle, other_reader) and _scoped(other_handle, other_reader)
    assert not _allowed(other_handle, c.reader) and not _scoped(other_handle, c.reader)
    # The second directory stated no address, edge or other handle of the shared person.
    for row in (c.address, c.edge, c.stated_handle, c.own_handle, c.organization):
        assert not _allowed(row, other_reader), type(row).__name__
    with system_context(reason="tests.directory_readers.second_tombstone"):
        RecordLink.objects.tombstone(other_link)
    for row in (c.person, c.person_party, other_handle):
        assert not _allowed(row, other_reader) and not _scoped(row, other_reader), type(row).__name__
    for row in (c.person, c.person_party, c.stated_handle):
        assert _allowed(row, c.reader) and _scoped(row, c.reader), type(row).__name__


@pytest.mark.parametrize("removal", ("released", "withdrawn", "tombstoned"))
def test_a_removed_link_lends_nothing(contacts: SimpleNamespace, removal: str) -> None:
    """A link whose target was released or withdrawn, or that became a tombstone, stops lending read.

    Neither path withdraws the link's statements here, so the dependent rows stop
    lending through the link's own state, as the party does.
    """

    c = contacts
    with system_context(reason=f"tests.directory_readers.{removal}"):
        if removal == "released":
            RecordLink.objects.release_target(c.person_link)
        elif removal == "withdrawn":
            RecordLink.objects.observe(c.stream, "ada", target=None)
        else:
            RecordLink.objects.tombstone(c.person_link)
        assert c.address.statements.model._base_manager.filter(link=c.person_link, status="asserted").exists()
        assert c.person_party.statements.model._base_manager.filter(
            link=c.person_link, handle=c.stated_handle, status="asserted"
        ).exists()
    for actor in (c.reader, c.member):
        for row in (c.person, c.person_party, c.address, c.edge, c.stated_handle):
            assert not _allowed(row, actor), (removal, type(row).__name__, actor.username)
            assert not _scoped(row, actor), (removal, type(row).__name__, actor.username)
        assert _allowed(c.organization, actor), "The organization's own link still lends it"
    for row in (c.person, c.address, c.edge):
        assert _allowed(row, c.owner), (removal, type(row).__name__)


def test_a_withdrawn_claim_stops_lending_its_row_while_the_link_lends_the_person(contacts: SimpleNamespace) -> None:
    """A directory that no longer states an address stops lending it; the live link still lends the person."""

    c = contacts
    with system_context(reason="tests.directory_readers.withdraw_address"):
        c.address.statements.withdraw(c.source, link=c.person_link)
    assert not _allowed(c.address, c.reader) and not _scoped(c.address, c.reader)
    assert _allowed(c.edge, c.reader) and _allowed(c.person, c.reader) and _allowed(c.stated_handle, c.reader)
    with system_context(reason="tests.directory_readers.withdraw_handle"):
        c.person_party.statements.withdraw_unsupported(c.source, [], aspect="handle", link=c.person_link)
    assert not _allowed(c.stated_handle, c.reader) and not _scoped(c.stated_handle, c.reader)
    assert _allowed(c.person, c.reader) and _allowed(c.edge, c.reader)


def test_a_transferred_directory_lends_its_contacts_to_the_new_owner(contacts: SimpleNamespace) -> None:
    """The integration's owner reads the contacts it synced before, without private fields or writes."""

    c = contacts
    successor = get_user_model().objects.create_user(username="contacts-successor")
    with actor_context(c.admin):
        Directory.objects.with_actor(c.admin).get(pk=c.directory.pk).transfer_ownership(successor)
    assert _allowed(c.directory, successor, "write")
    for row in (c.person, c.person_party, c.organization, c.address, c.edge, c.stated_handle):
        assert _allowed(row, successor) and _scoped(row, successor), type(row).__name__
        for action in ("write", "delete"):
            assert not _allowed(row, successor, action), (type(row).__name__, action)
    for row in (c.person, c.person_party, c.organization):
        assert not _allowed(row, successor, "read_private"), type(row).__name__
    # The handle only the former owner asserted, and a person no link names, stay theirs.
    for row in (c.own_handle, c.unlinked):
        assert not _allowed(row, successor) and _allowed(row, c.owner), type(row).__name__


def test_the_share_surface_grants_contact_reader_under_the_directory_write(contacts: SimpleNamespace) -> None:
    """The directory declares ``contact_reader`` grantable; its writers share it, a contact reader cannot."""

    c = contacts
    assert Directory.get_rebac_grantable() == AbstractDirectory.rebac_grantable == {"contact_reader": "write"}
    assert not Directory._check_rebac_grantable()
    grantee = get_user_model().objects.create_user(username="contacts-shared")
    with actor_context(c.owner):
        Directory.objects.with_actor(c.owner).get(pk=c.directory.pk).grant_record_access("contact_reader", grantee)
        shared = Directory.objects.with_actor(c.owner).get(pk=c.directory.pk).direct_record_access()
    assert ("contact_reader", to_subject_ref(grantee)) in {(access.relation, access.subject) for access in shared}
    assert _allowed(c.person, grantee) and _allowed(c.stated_handle, grantee)
    with actor_context(c.reader), pytest.raises(PermissionDenied):
        Directory.objects.sudo(reason="tests.directory_readers.load").get(pk=c.directory.pk).with_actor(
            c.reader
        ).grant_record_access("contact_reader", c.outsider)
