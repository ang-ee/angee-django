"""Follower identity stays record-scoped while contact details remain private."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from graphql import parse, validate
from rebac import (
    ObjectRef,
    PermissionDenied,
    RelationshipTuple,
    actor_context,
    delete_relationships,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.backends import backend
from rebac.backends.local import LocalBackend
from rebac.backends.local_query import LocalQueryScope
from rebac.evaluator import evaluator_scope
from rebac.resources import model_resource_type
from rebac.types import RelationshipFilter

from angee.graphql.data import hasura_model_resource
from tests.conftest import execute_schema, result_data
from tests.messaging_models import Folder, Handle, Party, Thread, ThreadFollower
from tests.spaces_models import Group, Membership
from tests.test_messaging import Organization, Person, ThreadedTicket
from tests.test_nexus import Cadence, Tie
from tests.test_nexus import _schema as nexus_schema
from tests.test_parties_graphql import parties_schema
from tests.test_project_access import project_access_schema as project_access_schema

PARTY_PRIVATE: dict[str, Any] = {
    "notes": "Private notes",
    "first_met_note": "Private introduction",
    "handle_count": 7,
    "raw_vcard": "BEGIN:VCARD\nNOTE:Private carrier\nEND:VCARD",
    "extensions": {"X-PRIVATE": "Private extension"},
}
PERSON_PRIVATE: dict[str, Any] = {
    **PARTY_PRIVATE,
    "nickname": "Private nickname",
    "birthday": date(1990, 2, 3),
    "anniversary": date(2020, 4, 5),
}
PARTY_SELECTION = "id display_name notes first_met_note handle_count"
PERSON_SELECTION = f"{PARTY_SELECTION} nickname birthday anniversary"


@pytest.fixture(params=("denormalized", "registry"))
def follower_storage(request: pytest.FixtureRequest) -> Iterator[str]:
    """Select the store before the shared composition synchronizes its schema."""

    with override_settings(REBAC_LOCAL_BACKEND_STORAGE=request.param, REBAC_FIELD_READ_MODE="redact"):
        yield request.param


@pytest.fixture
def follower_schema(follower_storage: str, project_access_schema: Any) -> None:
    """Reuse the real composed permission fragments, including messaging and spaces."""


@pytest.fixture(scope="module")
def identity_graphql() -> Any:
    """Reuse the existing parties/messaging/nexus GraphQL composition."""

    return nexus_schema()


def _grant(row: Any, actor: Any, relation: str = "reader") -> None:
    write_relationships([RelationshipTuple(to_object_ref(row), relation, to_subject_ref(actor))])


def _allowed(row: Any, actor: Any, action: str = "read") -> bool:
    return bool(
        backend()
        .check_access(
            subject=to_subject_ref(actor),
            action=action,
            resource=to_object_ref(row),
        )
        .allowed
    )


@pytest.fixture
def followers(follower_schema: None) -> SimpleNamespace:
    """Seed explicit followers, another thread and an unfollowed identity."""

    user_model = get_user_model()
    owner = user_model.objects.create_user(username="identity-owner")
    reader = user_model.objects.create_user(username="identity-reader")
    other_reader = user_model.objects.create_user(username="other-thread-reader")
    outsider = user_model.objects.create_user(username="identity-outsider")
    private_reader = user_model.objects.create_user(username="private-reader")
    account = user_model.objects.create_user(username="follower-account")
    with system_context(reason="tests.follower_identity.seed"):
        person = Person.objects.create(
            display_name="Account follower",
            user=account,
            created_by=owner,
            updated_by=owner,
            **PERSON_PRIVATE,
        )
        accountless = Person.objects.create(display_name="Accountless follower", created_by=owner, **PERSON_PRIVATE)
        party = Party.objects.create(display_name="Party follower", created_by=owner, **PARTY_PRIVATE)
        unfollowed = Person.objects.create(display_name="Unfollowed", created_by=owner, **PERSON_PRIVATE)
        other = Person.objects.create(display_name="Other thread follower", created_by=owner, **PERSON_PRIVATE)
        thread = Thread.objects.create(owner=owner)
        other_thread = Thread.objects.create(owner=owner)
        for row in (person, accountless, party):
            ThreadFollower.objects.create(thread=thread, party=row, created_by=owner)
        ThreadFollower.objects.create(thread=other_thread, party=other, created_by=owner)
        parent = Party.objects.get(pk=person.pk)
        _grant(thread, reader)
        _grant(other_thread, other_reader)
        # Native direct grants are per resource definition, including MTI parents.
        _grant(person, private_reader)
        _grant(parent, private_reader)
    return SimpleNamespace(
        owner=owner,
        reader=reader,
        other_reader=other_reader,
        outsider=outsider,
        private_reader=private_reader,
        person=person,
        accountless=accountless,
        party=party,
        parent=parent,
        unfollowed=unfollowed,
        other=other,
        thread=thread,
        other_thread=other_thread,
    )


def test_scoped_queryset_agrees_with_checks_for_every_follower_audience(followers: SimpleNamespace) -> None:
    """Thread, unrelated-thread, anonymous, owner and private grants stay distinct."""

    f = followers
    cases: tuple[tuple[Any, set[int], set[int]], ...] = (
        (f.reader, {f.person.pk, f.accountless.pk, f.party.pk}, set()),
        (f.other_reader, {f.other.pk}, set()),
        (f.outsider, set(), set()),
        (AnonymousUser(), set(), set()),
        (
            f.owner,
            {f.person.pk, f.accountless.pk, f.party.pk, f.unfollowed.pk, f.other.pk},
            {f.person.pk, f.accountless.pk, f.party.pk, f.unfollowed.pk, f.other.pk},
        ),
        (f.private_reader, {f.person.pk}, {f.person.pk}),
    )
    for model in (Party, Person):
        with system_context(reason="tests.follower_identity.candidates"):
            rows = list(model.objects.all())
        candidates = {row.pk for row in rows}
        for actor, readable, private in cases:
            for action, expected in (("read", readable), ("read_private", private)):
                expected = expected & candidates
                scoped = set(model.objects.with_actor(actor).with_action(action).scoped().values_list("pk", flat=True))
                checked = {row.pk for row in rows if _allowed(row, actor, action)}
                assert scoped == checked == expected, (model.__name__, actor, action)


def test_follower_scopes_compile_without_enumeration_and_fetch_fifty_rows_in_one_query(
    followers: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Both MTI paths and private intersections compile; 2/50-row SQL costs stay fixed."""

    f = followers
    with system_context(reason="tests.follower_identity.page"):
        rows = [Person.objects.create(display_name=f"Page {index:02d}", created_by=f.owner) for index in range(50)]
        for row in rows:
            ThreadFollower.objects.create(thread=f.thread, party=row, created_by=f.owner)
        tie = Tie.objects.create(party_a=rows[0], party_b=rows[1])

    def no_enumeration(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("Follower SQL scoping must not enumerate accessible resource IDs")

    with actor_context(f.reader), evaluator_scope():
        local = backend()
        assert isinstance(local, LocalBackend)
        local.schema()
        monkeypatch.setattr(type(local), "accessible", no_enumeration)
        for model in (Party, Person, Tie):
            resource_type = model_resource_type(model)
            assert resource_type is not None
            for action in ("read", "read_private") if model != Tie else ("read",):
                predicate = LocalQueryScope(local, to_subject_ref(f.reader), "default").predicate(
                    model,
                    action,
                    resource_type,
                )
                for size in (2, 50):
                    ids = [row.pk for row in rows[:size]] if model != Tie else [tie.pk]
                    query = model._base_manager.filter(predicate, pk__in=ids).values_list("pk", flat=True)
                    sql, parameters = query.query.sql_with_params()
                    assert "SELECT" in sql and parameters
                    (tmp_path / f"{model.__name__}-{action}-{size}.sql").write_text(
                        f"{sql}\n-- parameters: {parameters!r}\n",
                        encoding="utf-8",
                    )
                    with CaptureQueriesContext(connection) as captured:
                        result = set(query)
                    assert len(captured) == 1, captured.captured_queries
                    assert result == (set(ids) if action == "read" and model != Tie else set())
        for model in (Party, Person):
            # Also exercise the native public queryset, with enumeration forbidden.
            assert set(model.objects.filter(pk__in=[row.pk for row in rows]).values_list("pk", flat=True)) == {
                row.pk for row in rows
            }


def test_thread_reader_gets_identity_but_every_private_native_field_is_null(followers: SimpleNamespace) -> None:
    """Lossless carriers and MTI-inherited fields cannot bypass GraphQL redaction."""

    f = followers
    for model, fields in ((Party, PARTY_PRIVATE), (Person, PERSON_PRIVATE)):
        row = model.objects.with_actor(f.reader).get(pk=f.person.pk)
        assert row.display_name == f.person.display_name
        assert {name: getattr(row, name) for name in fields} == dict.fromkeys(fields)
        assert row.created_by_id == f.owner.pk
        assert row.updated_by_id == f.owner.pk
        for actor in (f.owner, f.private_reader):
            row = model.objects.with_actor(actor).get(pk=f.person.pk)
            assert {name: getattr(row, name) for name in fields} == fields


def test_parties_and_people_roots_redact_private_fields_but_preserve_owner_values(
    followers: SimpleNamespace,
    identity_graphql: Any,
) -> None:
    """Root lists preserve readable identity and attribution without private contact data."""

    f = followers
    query = f"""query Identity($id: String!) {{
      parties(where: {{id: {{_eq: $id}}}}) {{
        {PARTY_SELECTION} created_by updated_by created_by_label updated_by_label
      }}
      people(where: {{id: {{_eq: $id}}}}) {{ {PERSON_SELECTION} }}
    }}"""
    for actor in (f.reader, f.owner, f.private_reader):
        data = result_data(execute_schema(identity_graphql, query, {"id": f.person.sqid}, user=actor))
        for root, fields in (
            ("parties", ("notes", "first_met_note", "handle_count")),
            ("people", ("notes", "first_met_note", "handle_count", "nickname", "birthday", "anniversary")),
        ):
            assert len(data[root]) == 1
            row = data[root][0]
            assert row["id"] == f.person.sqid
            assert row["display_name"] == f.person.display_name
            expected = {
                name: (
                    PERSON_PRIVATE[name].isoformat() if isinstance(PERSON_PRIVATE[name], date) else PERSON_PRIVATE[name]
                )
                for name in fields
            }
            assert {name: row[name] for name in fields} == (dict.fromkeys(fields) if actor == f.reader else expected)
        assert data["parties"][0]["created_by"] is not None
        assert data["parties"][0]["updated_by"] is not None
        assert data["parties"][0]["created_by_label"] == f.owner.username
        assert data["parties"][0]["updated_by_label"] == f.owner.username


def test_hidden_folder_and_introducer_are_null_without_losing_people_or_parties(
    followers: SimpleNamespace,
    identity_graphql: Any,
) -> None:
    """One unreadable to-one target cannot fail the list or hide its readable neighbors."""

    f = followers
    with system_context(reason="tests.follower_identity.relations"):
        hidden_folder = Folder.objects.create(name="Hidden", created_by=f.owner)
        visible_folder = Folder.objects.create(name="Visible", created_by=f.owner)
        for person, folder, introducer in (
            (f.person, hidden_folder, f.unfollowed),
            (f.accountless, visible_folder, f.party),
        ):
            person.folder = folder
            person.introduced_by = introducer
            person.save(update_fields=["folder", "introduced_by"])
        _grant(visible_folder, f.reader)
    data = result_data(
        execute_schema(
            identity_graphql,
            """{
      people { id folder { id name } introduced_by { id } }
      parties { id introduced_by { id } }
    }""",
            user=f.reader,
        )
    )
    people = {row["id"]: row for row in data["people"]}
    assert people == {
        f.person.sqid: {"id": f.person.sqid, "folder": None, "introduced_by": None},
        f.accountless.sqid: {
            "id": f.accountless.sqid,
            "folder": {"id": visible_folder.sqid, "name": "Visible"},
            "introduced_by": {"id": f.party.sqid},
        },
    }
    parties = {row["id"]: row for row in data["parties"]}
    assert parties[f.person.sqid]["introduced_by"] is None
    assert parties[f.accountless.sqid]["introduced_by"] == {"id": f.party.sqid}


def test_organization_inherits_the_guarded_introducer(followers: SimpleNamespace, identity_graphql: Any) -> None:
    """An independently readable organization cannot expose its hidden introducer."""

    f = followers
    with system_context(reason="tests.follower_identity.organization"):
        organization = Organization.objects.create(
            display_name="Organization",
            created_by=f.reader,
            introduced_by=f.unfollowed,
        )
    data = result_data(execute_schema(identity_graphql, "{ organizations { id introduced_by { id } } }", user=f.reader))
    assert data["organizations"] == [{"id": organization.sqid, "introduced_by": None}]


def test_embedded_party_projections_keep_private_fields_redacted(
    followers: SimpleNamespace, identity_graphql: Any
) -> None:
    """A readable handle supplies no extra authority over its followed party."""

    f = followers
    with system_context(reason="tests.follower_identity.embedded"):
        for index, party in enumerate((f.person, f.accountless, f.party, f.unfollowed)):
            Handle.objects.create(
                platform="email", value=f"identity-{index}@example.test", party=party, created_by=f.reader
            )
    data = result_data(
        execute_schema(identity_graphql, f"{{ handles {{ party {{ {PARTY_SELECTION} }} }} }}", user=f.reader)
    )
    rows = [row["party"] for row in data["handles"]]
    assert rows.count(None) == 1
    assert {row["id"] for row in rows if row} == {f.person.sqid, f.accountless.sqid, f.party.sqid}
    for row in rows:
        if row is not None:
            assert row["display_name"]
            assert {name: row[name] for name in ("notes", "first_met_note", "handle_count")} == {
                "notes": None,
                "first_met_note": None,
                "handle_count": None,
            }


@pytest.mark.parametrize("axis", ("filterable", "sortable", "groupable", "aggregatable"))
def test_gated_contact_fields_are_refused_as_query_axes(axis: str) -> None:
    """Consumer resource declarations cannot expose private columns as query axes."""

    for model, node, fields in (
        (Party, parties_schema.PartyType, PARTY_PRIVATE),
        (Person, parties_schema.PersonType, PERSON_PRIVATE),
    ):
        for field in fields:
            axes: dict[str, Any] = {"filterable": (), "sortable": (), "groupable": (), "aggregatable": ()}
            axes[axis] = (field,)
            with pytest.raises(ImproperlyConfigured, match="field-gated reads"):
                hasura_model_resource(node, model=model, **axes)


def test_shipped_query_inputs_and_metadata_have_no_private_axes(identity_graphql: Any) -> None:
    """The public contract omits forbidden filters, sorts, groups and aggregates."""

    resources = {resource.model_label: resource for resource in identity_graphql.angee_resources}
    for label, private in (("parties.Party", PARTY_PRIVATE), ("parties.Person", PERSON_PRIVATE)):
        resource = resources[label]
        for type_name in (resource.type_names.filter, resource.type_names.order):
            assert set(private).isdisjoint(identity_graphql._schema.get_type(type_name).fields)
        assert set(private).isdisjoint(resource.query.axes)
        assert set(private).isdisjoint(resource.aggregate_fields)
    for name in ("PartyType", "PersonType"):
        assert {"raw_vcard", "extensions"}.isdisjoint(identity_graphql._schema.get_type(name).fields)


def test_graphql_refuses_private_filter_sort_and_group_operations(identity_graphql: Any) -> None:
    """Wire-level operations cannot infer a gated value through an input or group key."""

    for root, fields in (
        ("parties", ("notes", "first_met_note", "handle_count")),
        ("people", ("notes", "first_met_note", "handle_count", "nickname", "birthday", "anniversary")),
    ):
        assert not validate(identity_graphql._schema, parse(f"{{ {root} {{ id display_name }} }}"))
        for field in fields:
            for operation, rejected in (
                (f"{{ {root}(where: {{{field}: {{_is_null: false}}}}) {{ id }} }}", field),
                (f"{{ {root}(order_by: {{{field}: asc}}) {{ id }} }}", field),
                (
                    f"{{ {root}_groups(group_by: [{{field: {field.upper()}}}]) {{ aggregate {{ count }} }} }}",
                    field.upper(),
                ),
            ):
                errors = validate(identity_graphql._schema, parse(operation))
                assert errors and any(rejected in error.message for error in errors), operation


def test_unfollow_revokes_identity_immediately_but_preserves_other_threads_and_private_grants(
    followers: SimpleNamespace,
    identity_graphql: Any,
) -> None:
    """Deleting the sole FK edge revokes both parent and child without a sync."""

    f = followers
    with system_context(reason="tests.follower_identity.second_path"):
        _grant(f.other_thread, f.reader)
        ThreadFollower.objects.create(thread=f.other_thread, party=f.accountless, created_by=f.owner)
    query = "{ parties { id } people { id } }"
    before = result_data(execute_schema(identity_graphql, query, user=f.reader))
    assert f.person.sqid in {row["id"] for row in before["people"]}
    with system_context(reason="tests.follower_identity.unfollow"):
        ThreadFollower.objects.filter(thread=f.thread, party_id__in=(f.person.pk, f.accountless.pk)).delete()
    for model in (Party, Person):
        assert not model.objects.with_actor(f.reader).filter(pk=f.person.pk).exists()
        assert model.objects.with_actor(f.reader).filter(pk=f.accountless.pk).exists()
        assert model.objects.with_actor(f.private_reader).filter(pk=f.person.pk).exists()
    assert not _allowed(f.parent, f.reader)
    assert not _allowed(f.person, f.reader)
    assert _allowed(f.accountless, f.reader)
    assert _allowed(f.parent, f.private_reader, "read_private")
    after = result_data(execute_schema(identity_graphql, query, user=f.reader))
    for root in ("parties", "people"):
        assert f.person.sqid not in {row["id"] for row in after[root]}
        assert f.accountless.sqid in {row["id"] for row in after[root]}


def test_thread_reader_revocation_removes_follower_identity_without_a_sync(followers: SimpleNamespace) -> None:
    """Revoking thread read removes the inherited identity arm on the next read."""

    f = followers
    assert _allowed(f.person, f.reader)
    delete_relationships(
        RelationshipFilter(
            resource_type="messaging/thread",
            resource_id=str(f.thread.pk),
            relation="reader",
            subject_type="auth/user",
            subject_id=str(f.reader.pk),
        )
    )
    for model in (Party, Person):
        assert not model.objects.with_actor(f.reader).filter(pk=f.person.pk).exists()
    assert not _allowed(f.person, f.reader)
    assert not _allowed(f.parent, f.reader)
    assert _allowed(f.parent, f.private_reader)


def test_directory_access_alone_grants_no_party_identity_or_private_values(followers: SimpleNamespace) -> None:
    """Account directory access must not revive the withdrawn account-wide arm."""

    f = followers
    write_relationships([RelationshipTuple(ObjectRef("iam/directory", "main"), "reader", to_subject_ref(f.outsider))])
    assert get_user_model().objects.with_actor(f.outsider).filter(pk=f.person.user_id).exists()
    for model, row in ((Party, f.parent), (Person, f.person)):
        for action in ("read", "read_private"):
            assert not _allowed(row, f.outsider, action)
            assert not model.objects.with_actor(f.outsider).with_action(action).filter(pk=row.pk).exists()


def test_ties_and_cadences_require_private_access_to_followed_parties(
    followers: SimpleNamespace,
    identity_graphql: Any,
) -> None:
    """Identity-only readers cannot read a pair edge or create/read/write/delete a cadence."""

    f = followers
    with system_context(reason="tests.follower_identity.nexus"):
        tie = Tie.objects.create(party_a=f.person, party_b=f.accountless)
        cadence = Cadence.objects.create(user=f.reader, party=f.person, cadence_days=14)
    assert not _allowed(tie, f.reader)
    assert not Tie.objects.with_actor(f.reader).filter(pk=tie.pk).exists()
    for action in ("create", "read", "write", "delete"):
        assert not _allowed(cadence, f.reader, action)
        assert not Cadence.objects.with_actor(f.reader).with_action(action).filter(pk=cadence.pk).exists()
    with actor_context(f.reader), pytest.raises(PermissionDenied):
        Cadence.objects.create(party=f.accountless, cadence_days=10)
    with actor_context(f.reader), pytest.raises(PermissionDenied):
        Tie.objects.create(party_a=f.person, party_b=f.party)
    with actor_context(f.reader), pytest.raises(PermissionDenied):
        cadence.cadence_days = 99
        cadence.save(update_fields=["cadence_days"])
    with actor_context(f.reader), pytest.raises(PermissionDenied):
        cadence.delete()
    payload = result_data(
        execute_schema(
            identity_graphql,
            """query Private($party: String!) {
      ties { id } cadences { id }
      parties_by_pk(id: $party) { cadence { id } }
    }""",
            {"party": f.person.sqid},
            user=f.reader,
        )
    )
    assert payload == {"ties": [], "cadences": [], "parties_by_pk": {"cadence": None}}
    denied = execute_schema(
        identity_graphql,
        """mutation Create($party: ID!) {
      insert_cadences_one(object: {party: $party, cadence_days: 10}) { id }
    }""",
        {"party": f.accountless.sqid},
        user=f.reader,
    )
    assert denied.errors
    with system_context(reason="tests.follower_identity.nexus_verify"):
        assert not Cadence.objects.filter(user=f.reader, party=f.accountless).exists()
        assert Cadence.objects.get(pk=cadence.pk).cadence_days == 14
        _grant(f.parent, f.reader)
    assert not _allowed(tie, f.reader), "One private endpoint must not reveal an edge"
    assert _allowed(cadence, f.reader, "read")
    with system_context(reason="tests.follower_identity.second_private_endpoint"):
        _grant(Party.objects.get(pk=f.accountless.pk), f.reader)
    assert _allowed(tie, f.reader)


def test_nexus_graph_omits_redacted_handle_count_and_private_edges(
    followers: SimpleNamespace,
    identity_graphql: Any,
) -> None:
    """Identity graph nodes omit count metadata; readable zero is distinct from hidden."""

    f = followers
    with system_context(reason="tests.follower_identity.graph"):
        zero = Party.objects.create(display_name="Zero handles", handle_count=0, created_by=f.owner)
        Tie.objects.create(party_a=f.person, party_b=zero)
    query = "query Graph($id: ID!) { party_graph(root_id: $id) { nodes edges } }"
    graph = result_data(execute_schema(identity_graphql, query, {"id": f.person.sqid}, user=f.reader))["party_graph"]
    assert graph["edges"] == []
    assert len(graph["nodes"]) == 1
    node = graph["nodes"][0]
    assert node["id"] == f.person.sqid
    assert "detail" not in node
    assert "handle_count" not in node["meta"]
    graph = result_data(execute_schema(identity_graphql, query, {"id": f.person.sqid}, user=f.owner))["party_graph"]
    nodes = {node["id"]: node for node in graph["nodes"]}
    assert nodes[f.person.sqid]["meta"]["handle_count"] == 7
    assert nodes[f.person.sqid]["detail"] == "7 handles"
    assert nodes[zero.sqid]["meta"]["handle_count"] == 0
    assert nodes[zero.sqid]["detail"] == "0 handles"


def test_public_group_thread_exposes_follower_identity_only_to_signed_in_strangers(
    followers: SimpleNamespace,
    identity_graphql: Any,
) -> None:
    """A public group's signed-in read audience reaches explicit followers, never anonymous."""

    f = followers
    with system_context(reason="tests.follower_identity.public_group"):
        group = Group.objects.create(name="Public group", owner=f.owner, visibility="public")
        f.thread.groups.add(group)
    assert not Membership._base_manager.filter(group=group, party__person__user=f.outsider).exists()
    for actor, allowed in ((f.outsider, True), (AnonymousUser(), False)):
        assert _allowed(f.thread, actor) is allowed
        for model, row in ((Party, f.parent), (Person, f.person)):
            assert _allowed(row, actor) is allowed
            assert model.objects.with_actor(actor).filter(pk=row.pk).exists() is allowed
            assert not _allowed(row, actor, "read_private")
        data = result_data(execute_schema(identity_graphql, f"{{ people {{ {PERSON_SELECTION} }} }}", user=actor))
        if allowed:
            assert {row["id"] for row in data["people"]} == {f.person.sqid, f.accountless.sqid}
            for row in data["people"]:
                assert all(
                    row[name] is None
                    for name in ("notes", "first_met_note", "handle_count", "nickname", "birthday", "anniversary")
                )
        else:
            assert data["people"] == []


def test_record_followers_project_explicit_account_and_accountless_identities_only(
    followers: SimpleNamespace,
    identity_graphql: Any,
) -> None:
    """The guarded follower list names explicit parties without disclosing private data or roster members."""

    f = followers
    with system_context(reason="tests.follower_identity.record"):
        ticket = ThreadedTicket.objects.create(title="Followed record")
        thread = ticket.message_thread(create=False)
        assert thread is not None
        _grant(thread, f.reader)
        ticket.message_subscribe(party=f.person)
        ticket.message_subscribe(party=f.accountless)
        group = Group.objects.create(name="Record audience", owner=f.owner)
        thread.groups.add(group)
        Membership.objects.create(group=group, party=f.unfollowed, role="member", is_confirmed=True)
        assert [member.party_id for member in group.thread_audience()] == [f.unfollowed.pk]
        assert not ThreadFollower.objects.filter(thread=thread, party=f.unfollowed).exists()
    payload = result_data(
        execute_schema(
            identity_graphql,
            f"""query Followers($id: ID!) {{
      record_thread(input: {{model_label: "messaging.ThreadedTicket", record_id: $id}}) {{
        error_code follower_count followers {{ party {{ {PARTY_SELECTION} }} user {{ id }} }}
      }}
    }}""",
            {"id": ticket.sqid},
            user=f.reader,
        )
    )["record_thread"]
    assert payload["error_code"] is None
    assert payload["follower_count"] == 2
    assert {row["party"]["id"] for row in payload["followers"]} == {f.person.sqid, f.accountless.sqid}
    for row in payload["followers"]:
        assert row["party"]["display_name"]
        assert row["party"]["notes"] is None
        assert row["party"]["first_met_note"] is None
        assert row["party"]["handle_count"] is None
        assert row["user"] is None, "Follower identity must not grant account-directory access"
