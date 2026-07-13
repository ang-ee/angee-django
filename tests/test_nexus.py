"""Tests for derived nexus party edges, user cadences, and party timelines."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.db import IntegrityError, connection
from rebac import (
    RelationshipTuple,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.nexus.models import Cadence as AbstractCadence
from angee.nexus.models import Tie as AbstractTie
from tests import test_messaging_graphql
from tests.conftest import (
    SchemaAddon,
    _clear_model_tables,
    _create_missing_tables,
    execute_schema,
)
from tests.conftest import result_data as _data
from tests.test_messaging import (
    MESSAGING_TEST_MODELS,
    Handle,
    Message,
    MessageEdge,
    Participant,
    Party,
    Person,
    Thread,
    ThreadAttachment,
    ThreadedTicket,
)


class Tie(AbstractTie):
    """Concrete tie model used by nexus tests."""

    class Meta(AbstractTie.Meta):
        """Django model options for the canonical test tie."""

        abstract = False
        app_label = "nexus"
        db_table = "test_nexus_tie"
        rebac_resource_type = "nexus/tie"
        rebac_id_attr = "sqid"


class Cadence(AbstractCadence):
    """Concrete cadence model used by nexus tests."""

    class Meta(AbstractCadence.Meta):
        """Django model options for the canonical test cadence."""

        abstract = False
        app_label = "nexus"
        db_table = "test_nexus_cadence"
        rebac_resource_type = "nexus/cadence"
        rebac_id_attr = "sqid"


nexus_schema = __import__("angee.nexus.schema", fromlist=["schemas"])
NEXUS_TEST_MODELS = (*MESSAGING_TEST_MODELS, Tie, Cadence)
User = get_user_model()
_T0 = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def nexus_tables(transactional_db: Any) -> Iterator[None]:
    """Create the concrete nexus and upstream test tables."""

    del transactional_db
    created_models = _create_missing_tables(NEXUS_TEST_MODELS)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        _clear_model_tables(NEXUS_TEST_MODELS)
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


def _party(name: str) -> Any:
    return Party._base_manager.create(display_name=name)


def _handle(party: Any | None, value: str, *, platform: str = "email") -> Any:
    return Handle._base_manager.create(platform=platform, value=value, party=party)


def _message(
    *,
    sender: Any,
    thread: Any,
    sent_at: datetime,
    external_id: str,
    parent: Any | None = None,
    platform: str = "email",
) -> Any:
    return Message._base_manager.create(
        thread=thread,
        sender=sender,
        sent_at=sent_at,
        external_id=external_id,
        parent=parent,
        platform=platform,
    )


def _address(message: Any, handle: Any, role: str = "to") -> Any:
    return Participant._base_manager.create(
        message=message,
        thread=message.thread,
        handle=handle,
        role=role,
    )


def _schema() -> Any:
    """Build the composed console schema used by nexus."""

    modules = (
        test_messaging_graphql.parties_schema,
        test_messaging_graphql.messaging_schema,
        nexus_schema,
    )
    addons = [
        SchemaAddon({"console": {key: tuple(module.schemas["console"].get(key, ())) for key in SCHEMA_PART_KEYS}})
        for module in modules
    ]
    return GraphQLSchemas(addons).build("console")


def _grant(resource: Any, relation: str, user: Any) -> None:
    """Grant one direct relation on a REBAC resource."""

    write_relationships(
        [
            RelationshipTuple(
                resource=to_object_ref(resource),
                relation=relation,
                subject=to_subject_ref(user),
            )
        ]
    )


def test_tie_declares_a_canonical_party_pair_with_no_human_columns() -> None:
    """Tie stores only the recomputable pair-edge contract."""

    fields = {field.name for field in AbstractTie._meta.fields}
    assert {
        "party_a",
        "party_b",
        "a_to_b_count",
        "b_to_a_count",
        "message_count",
        "thread_count",
        "platforms",
        "first_interaction_at",
        "last_interaction_at",
        "gravity",
        "is_fading",
    } <= fields
    assert {"party", "outbound_count", "inbound_count", "cadence_days", "touch_due_at"}.isdisjoint(fields)


def test_cadence_declares_the_only_human_fact() -> None:
    """Cadence carries one user-party intent and a server-owned due date."""

    fields = {field.name: field for field in AbstractCadence._meta.fields}
    assert {"user", "party", "cadence_days", "touch_due_at"} <= fields.keys()
    assert fields["user"].remote_field.on_delete.__name__ == "CASCADE"
    assert fields["party"].remote_field.on_delete.__name__ == "CASCADE"
    assert fields["touch_due_at"].editable is False


def test_gravity_uses_the_directional_pair_for_reciprocity() -> None:
    """One-way traffic scores zero; reciprocal fresh traffic scores positively."""

    assert Tie.compute_gravity(
        message_count=40,
        a_to_b_count=40,
        b_to_a_count=0,
        last_at=_T0,
        platform_count=1,
        now=_T0,
    ) == 0.0
    reciprocal = Tie.compute_gravity(
        message_count=40,
        a_to_b_count=20,
        b_to_a_count=20,
        last_at=_T0,
        platform_count=1,
        now=_T0,
    )
    stale = Tie.compute_gravity(
        message_count=40,
        a_to_b_count=20,
        b_to_a_count=20,
        last_at=_T0 - timedelta(days=90),
        platform_count=1,
        now=_T0,
    )
    diverse = Tie.compute_gravity(
        message_count=40,
        a_to_b_count=20,
        b_to_a_count=20,
        last_at=_T0,
        platform_count=2,
        now=_T0,
    )
    assert reciprocal > stale > 0.0
    assert diverse == pytest.approx(reciprocal * 1.1)


def test_fading_adapts_to_the_edge_rhythm() -> None:
    """The silence threshold is eight average intervals with a sixty-day floor."""

    first = _T0 - timedelta(days=170)
    last = _T0 - timedelta(days=80)
    assert not Tie.check_fading(message_count=10, first_at=first, last_at=last, now=last + timedelta(days=79))
    assert Tie.check_fading(message_count=10, first_at=first, last_at=last, now=last + timedelta(days=81))
    assert not Tie.check_fading(message_count=1, first_at=_T0, last_at=_T0, now=_T0 + timedelta(days=400))


@pytest.mark.django_db(transaction=True)
def test_tie_save_canonicalizes_the_pair_and_the_database_rejects_duplicates(nexus_tables: None) -> None:
    """Save orders both ends while the unique constraint closes alternate write paths."""

    del nexus_tables
    with system_context(reason="test nexus canonical pair"):
        first = _party("First")
        second = _party("Second")
        tie = Tie.objects.create(party_a=second, party_b=first)

        assert (tie.party_a_id, tie.party_b_id) == (first.pk, second.pk)
        with pytest.raises(IntegrityError):
            Tie.objects.create(party_a=first, party_b=second)


@pytest.mark.django_db(transaction=True)
def test_recompute_derives_addressed_reply_and_mention_edges_once(nexus_tables: None) -> None:
    """The three deliberate sources converge without double-counting one message/pair."""

    del nexus_tables
    with system_context(reason="test nexus deliberate interactions"):
        alice = _party("Alice")
        bob = _party("Bob")
        alice_handle = _handle(alice, "alice@example.com")
        bob_handle = _handle(bob, "bob@example.com")
        thread = Thread._base_manager.create(platform="email")
        parent = _message(
            sender=bob_handle,
            thread=thread,
            sent_at=_T0,
            external_id="parent",
        )
        reply = _message(
            sender=alice_handle,
            thread=thread,
            sent_at=_T0 + timedelta(days=1),
            external_id="reply",
            parent=parent,
        )
        _address(reply, bob_handle, "to")
        MessageEdge._base_manager.create(
            src=reply,
            dst=parent,
            kind=MessageEdge.EdgeKind.MENTION,
        )

        assert Tie.objects.recompute(now=_T0 + timedelta(days=2)) == 1
        tie = Tie._base_manager.get()
        assert (tie.party_a_id, tie.party_b_id) == tuple(sorted((alice.pk, bob.pk)))
        assert tie.message_count == 1
        assert tie.thread_count == 1
        assert tie.platforms == ["email"]
        assert tie.first_interaction_at == reply.sent_at
        assert tie.last_interaction_at == reply.sent_at
        assert (tie.a_to_b_count, tie.b_to_a_count) == (
            (1, 0) if alice.pk < bob.pk else (0, 1)
        )


@pytest.mark.django_db(transaction=True)
def test_recompute_counts_to_and_cc_but_not_bcc_or_unresolved_handles(nexus_tables: None) -> None:
    """Only resolved TO/CC envelope targets are deliberate addressed interactions."""

    del nexus_tables
    with system_context(reason="test nexus envelope roles"):
        alice = _party("Alice")
        bob = _party("Bob")
        carol = _party("Carol")
        dave = _party("Dave")
        alice_handle = _handle(alice, "alice@example.com")
        bob_handle = _handle(bob, "bob@example.com")
        carol_handle = _handle(carol, "carol@example.com")
        dave_handle = _handle(dave, "dave@example.com")
        unresolved = _handle(None, "unknown@example.com")
        thread = Thread._base_manager.create(platform="email")
        message = _message(sender=alice_handle, thread=thread, sent_at=_T0, external_id="envelope")
        _address(message, bob_handle, "to")
        _address(message, carol_handle, "cc")
        _address(message, dave_handle, "bcc")
        _address(message, unresolved, "to")

        assert Tie.objects.recompute(now=_T0) == 2
        pairs = set(Tie._base_manager.values_list("party_a_id", "party_b_id"))
        assert pairs == {
            tuple(sorted((alice.pk, bob.pk))),
            tuple(sorted((alice.pk, carol.pk))),
        }


@pytest.mark.django_db(transaction=True)
def test_thread_roster_only_group_with_ten_participants_produces_no_ties(nexus_tables: None) -> None:
    """Thread roster co-membership never expands one group message into pair edges."""

    del nexus_tables
    with system_context(reason="test nexus roster guard"):
        parties = [_party(f"Person {index}") for index in range(10)]
        handles = [_handle(party, f"person{index}@example.com") for index, party in enumerate(parties)]
        thread = Thread._base_manager.create(platform="whatsapp", modality=Thread.Modality.GROUP)
        _message(sender=handles[0], thread=thread, sent_at=_T0, external_id="group", platform="whatsapp")
        for handle in handles:
            Participant._base_manager.create(thread=thread, handle=handle, role="to")

        assert Tie.objects.recompute(now=_T0) == 0
        assert Tie._base_manager.count() == 0


@pytest.mark.django_db(transaction=True)
def test_recompute_excludes_record_chatter_and_public_threads(nexus_tables: None) -> None:
    """Chatter and public posts stay outside private relationship gravity."""

    del nexus_tables
    with system_context(reason="test nexus excluded thread kinds"):
        alice = _party("Alice")
        bob = _party("Bob")
        alice_handle = _handle(alice, "alice@example.com")
        bob_handle = _handle(bob, "bob@example.com")
        ticket = ThreadedTicket._base_manager.create(title="Private record")
        chatter = Thread._base_manager.create(platform="email")
        ThreadAttachment._base_manager.create(
            thread=chatter,
            content_type=ContentType.objects.get_for_model(ThreadedTicket),
            object_id=ticket.pk,
        )
        public = Thread._base_manager.create(
            platform="facebook",
            modality=Thread.Modality.PUBLIC_THREAD,
        )
        for index, thread in enumerate((chatter, public)):
            message = _message(
                sender=alice_handle,
                thread=thread,
                sent_at=_T0 + timedelta(days=index),
                external_id=f"excluded-{index}",
                platform=str(thread.platform),
            )
            _address(message, bob_handle)

        assert Tie.objects.recompute(now=_T0 + timedelta(days=2)) == 0
        assert Tie._base_manager.count() == 0


@pytest.mark.django_db(transaction=True)
def test_recompute_deletes_stale_edges_and_refreshes_cadence(nexus_tables: None) -> None:
    """Derived rows disappear with their evidence while the human cadence survives."""

    del nexus_tables
    with system_context(reason="test nexus cadence refresh"):
        user = User.objects.create_user(username="viewer")
        viewer = Person._base_manager.create(display_name="Viewer", user=user, created_by=user)
        target = _party("Target")
        viewer_handle = _handle(viewer, "viewer@example.com")
        target_handle = _handle(target, "target@example.com")
        thread = Thread._base_manager.create(platform="email")
        message = _message(sender=viewer_handle, thread=thread, sent_at=_T0, external_id="cadence")
        _address(message, target_handle)
        cadence = Cadence.objects.create(user=user, party=target, cadence_days=14)

        assert cadence.touch_due_at is None
        Tie.objects.recompute(now=_T0 + timedelta(days=1))
        cadence.refresh_from_db()
        assert cadence.touch_due_at == _T0 + timedelta(days=14)

        Message._base_manager.all().delete()
        assert Tie.objects.recompute(now=_T0 + timedelta(days=2)) == 0
        assert not Tie._base_manager.exists()
        cadence.refresh_from_db()
        assert cadence.cadence_days == 14
        assert cadence.touch_due_at is None


@pytest.mark.django_db(transaction=True)
def test_cadence_stays_due_null_until_the_user_has_a_party_identity(nexus_tables: None) -> None:
    """A cadence cannot infer a viewer edge before Person.user establishes identity."""

    del nexus_tables
    with system_context(reason="test nexus cadence without identity"):
        user = User.objects.create_user(username="unlinked")
        target = _party("Target")
        cadence = Cadence.objects.create(user=user, party=target, cadence_days=30)

        assert cadence.touch_due_at is None


@pytest.mark.django_db(transaction=True)
def test_tie_read_requires_access_to_both_parties(nexus_tables: None) -> None:
    """One readable endpoint cannot leak the other endpoint's network."""

    del nexus_tables
    reader = User.objects.create_user(username="one-sided-reader")
    owner = User.objects.create_user(username="pair-owner")
    with system_context(reason="test nexus intersection seed"):
        first = Party._base_manager.create(display_name="First", created_by=owner)
        second = Party._base_manager.create(display_name="Second", created_by=owner)
        Tie._base_manager.create(party_a=first, party_b=second)
    _grant(first, "reader", reader)

    with actor_context(reader):
        assert Tie.objects.around_party(first).count() == 0
    _grant(second, "reader", reader)
    with actor_context(reader):
        assert Tie.objects.around_party(first).count() == 1


@pytest.mark.django_db(transaction=True)
def test_cadence_read_requires_owner_and_party_access(nexus_tables: None) -> None:
    """Cadence visibility intersects its owning user with party read."""

    del nexus_tables
    owner = User.objects.create_user(username="cadence-owner")
    other = User.objects.create_user(username="cadence-party-owner")
    with system_context(reason="test nexus cadence permission seed"):
        party = Party._base_manager.create(display_name="Party", created_by=other)
        Cadence._base_manager.create(user=owner, party=party, cadence_days=21)

    with actor_context(owner):
        assert Cadence.objects.count() == 0
    _grant(party, "reader", owner)
    with actor_context(owner):
        assert Cadence.objects.count() == 1


def test_nexus_resource_metadata_separates_derived_ties_from_cadence_writes() -> None:
    """Tie is read-only while Cadence exposes the human-authored CRUD surface."""

    resources = {item.model_label: item for item in _schema().angee_resources}
    tie = resources["nexus.Tie"]
    cadence = resources["nexus.Cadence"]

    assert tie.roots.list_name == "ties"
    assert tie.roots.create_name is None
    assert tie.roots.update_name is None
    assert tie.roots.delete_name is None
    assert cadence.roots.list_name == "cadences"
    assert cadence.roots.create_name == "insert_cadences_one"
    assert cadence.roots.update_name == "update_cadences_by_pk"
    assert cadence.roots.delete_name == "delete_cadences_by_pk"
    assert cadence.create_fields == ("party", "cadence_days")
    assert cadence.update_fields == ("cadence_days",)


def test_party_resource_metadata_projects_the_canonical_party_label() -> None:
    """Party subtypes inherit the timeline capability from canonical MTI ancestry."""

    schema = _schema()
    resources = {item.model_label: item for item in schema.angee_resources}
    for label in ("parties.Party", "parties.Person", "parties.Organization"):
        assert resources[label].canonical_label == "parties.Party"

    wire = {item["modelLabel"]: item for item in schema._schema.extensions["angee"]["resources"]}
    assert wire["parties.Person"]["canonicalLabel"] == "parties.Party"


@pytest.mark.django_db(transaction=True)
def test_cadence_create_binds_the_authenticated_user(nexus_tables: None) -> None:
    """The CRUD surface accepts intent fields and owns the viewer relation server-side."""

    del nexus_tables
    viewer = User.objects.create_user(username="cadence-create-viewer")
    with system_context(reason="test nexus cadence create seed"):
        party = Party._base_manager.create(display_name="Party", created_by=viewer)

    created = _data(
        execute_schema(
            _schema(),
            """
            mutation CreateCadence($party: ID!) {
              insert_cadences_one(object: {party: $party, cadence_days: 10}) {
                id
                cadence_days
              }
            }
            """,
            {"party": party.sqid},
            user=viewer,
        )
    )["insert_cadences_one"]

    cadence = Cadence._base_manager.get(sqid=created["id"])
    assert created["cadence_days"] == 10
    assert cadence.user_id == viewer.pk
    assert cadence.party_id == party.pk


@pytest.mark.django_db(transaction=True)
def test_party_fields_resolve_the_viewers_edge_and_cadence(nexus_tables: None) -> None:
    """Party tie and cadence are selected through the signed-in Person identity."""

    del nexus_tables
    viewer_user = User.objects.create_user(username="viewer-fields")
    with system_context(reason="test nexus viewer fields seed"):
        viewer = Person._base_manager.create(display_name="Viewer", user=viewer_user, created_by=viewer_user)
        target = Party._base_manager.create(display_name="Target", created_by=viewer_user)
        other = Party._base_manager.create(display_name="Other", created_by=viewer_user)
        expected = Tie._base_manager.create(
            party_a=viewer,
            party_b=target,
            a_to_b_count=3,
            message_count=3,
        )
        Tie._base_manager.create(party_a=target, party_b=other, message_count=99)
        cadence = Cadence._base_manager.create(user=viewer_user, party=target, cadence_days=14)

    payload = _data(
        execute_schema(
            _schema(),
            """
            query ViewerParty($id: String!) {
              parties_by_pk(id: $id) {
                tie { id message_count }
                cadence { id cadence_days }
              }
            }
            """,
            {"id": target.sqid},
            user=viewer_user,
        )
    )["parties_by_pk"]

    assert payload == {
        "tie": {"id": expected.sqid, "message_count": 3},
        "cadence": {"id": cadence.sqid, "cadence_days": 14},
    }


@pytest.mark.django_db(transaction=True)
def test_party_tie_is_null_without_a_viewer_party_identity(nexus_tables: None) -> None:
    """A signed-in user without Person.user has no viewer-relative edge."""

    del nexus_tables
    viewer = User.objects.create_user(username="viewer-without-person")
    with system_context(reason="test nexus missing viewer identity seed"):
        target = Party._base_manager.create(display_name="Target", created_by=viewer)

    payload = _data(
        execute_schema(
            _schema(),
            "query ViewerParty($id: String!) { parties_by_pk(id: $id) { tie { id } } }",
            {"id": target.sqid},
            user=viewer,
        )
    )["parties_by_pk"]

    assert payload == {"tie": None}


@pytest.mark.django_db(transaction=True)
def test_party_network_applies_pair_intersection_to_every_edge(nexus_tables: None) -> None:
    """The party network returns only edges whose two endpoints are readable."""

    del nexus_tables
    reader = User.objects.create_user(username="network-reader")
    owner = User.objects.create_user(username="network-owner")
    with system_context(reason="test nexus network seed"):
        center = Party._base_manager.create(display_name="Center", created_by=owner)
        visible = Party._base_manager.create(display_name="Visible", created_by=owner)
        hidden = Party._base_manager.create(display_name="Hidden", created_by=owner)
        shown = Tie._base_manager.create(party_a=center, party_b=visible)
        Tie._base_manager.create(party_a=center, party_b=hidden)
    _grant(center, "reader", reader)
    _grant(visible, "reader", reader)

    payload = _data(
        execute_schema(
            _schema(),
            """
            query PartyNetwork($id: ID!) {
              party_network(party_id: $id) { id }
            }
            """,
            {"id": center.sqid},
            user=reader,
        )
    )["party_network"]

    assert payload == [{"id": shown.sqid}]
