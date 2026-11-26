"""What the console reads to write a new message or a reply: where one may send, what was written here, the bound.

The rule a channel sends email by is its owner's, proved there
(``ChannelQuerySet.outbound_email`` in ``tests/test_messaging_email.py``), and the
recipient bound is ``compose``'s (``tests/test_messaging_compose.py``). This module
proves what the console adds: the ``sendable`` channels filter lists, seat by seat, the
channels on which ``compose_message`` admits a new email, at the cost of a read the
console already pays; ``is_locally_written`` reads the message's own answer with its
rows; and ``message_recipient_limit`` states the bound ``compose_message`` enforces.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from rebac import system_context

from angee.graphql.testing.documents import execute_schema, result_data
from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedPart
from angee.messaging.testing.models import Channel, Message, Thread
from tests.conftest import Feed, Integration, create_platform_admin, make_integration
from tests.messaging_campaign import grant, make_user
from tests.test_messaging_compose import _receive
from tests.test_messaging_compose import desk as desk
from tests.test_messaging_compose_graphql import (
    COMPOSE,
    SIZES,
    _compose,
    _conversation,
    _measured,
    _new,
    _Refusal,
)
from tests.test_messaging_graphql import _schema

READS = {
    # The New message header's presence and preselection: none, exactly one, or a choice.
    "presence": (
        "query MessagingSendableChannels { channels(where: {sendable: {_eq: true}},"
        " order_by: [{display_name: asc}], limit: 2) { id display_name } }",
        lambda ids: {},
    ),
    # Which loaded rows of a transcript offer Reply: the sendable channels among theirs.
    "ids": (
        "query MessagingSendableChannelIds($ids: [String!]!) {"
        " channels(where: {sendable: {_eq: true}, id: {_in: $ids}}) { id } }",
        lambda ids: {"ids": ids},
    ),
    # The Channel picker's options, as the shared relation picker pages them under its filter.
    "picker": (
        "query ResourceList($where: channels_bool_exp, $limit: Int, $offset: Int) {"
        " channels(where: $where, limit: $limit, offset: $offset) { id display_name }"
        " channels_aggregate(where: $where) { aggregate { count } } }",
        lambda ids: {"where": {"_and": [{"sendable": {"_eq": True}}]}, "limit": 200, "offset": 0},
    ),
}
"""The three ``sendable`` reads the console makes, each with its variables over the channel ids it may name."""

DRAFT = {
    "presence": "query Channels { channels(order_by: [{display_name: asc}]) { id display_name permissions } }",
    "ids": "query Channels($ids: [String!]!) { channels(where: {id: {_in: $ids}}) { id permissions } }",
}
"""The same reads answering ``permissions`` instead of filtering: how the console read whether one may send before."""

BUDGETS = {
    "owner": {"presence": (4, 0), "ids": (3, 0), "picker": (7, 2)},
    "replier": {"presence": (7, 3), "ids": (6, 3), "picker": (14, 9)},
    "administrator": {"presence": (9, 4), "ids": (8, 4), "picker": (18, 12)},
}
"""Each read's statements and repeated texts per seat, the same at 1 channel and at 25, on SQLite and PostgreSQL.

The presence read and the Reply gate cost what :data:`DRAFT` costs; selecting
``display_name`` costs any channels read one statement, the audit row of the
elevated vendor-branding read a channel's label falls back to. The picker's count
is a second root over the same filter. Inside a test's transaction REBAC pins its
policy revision for one compiled statement only, so that root reads the revision
and the viewer's grants again: its repeated texts.
"""


@pytest.fixture(scope="module")
def console() -> Any:
    """The console schema messaging composes into."""

    return _schema()


CHANNELS = ("support", "sales", "mailbox", "bad sender", "archive", "paused", "chat", "feed")
"""The channels of :func:`world`, in the order :data:`SEATS` gives what ``compose_message`` answers on each."""

SENT = "sent"
DENIED = "PERMISSION_DENIED"
SENDER = "channel"
"""Refused under ``channel`` with no code: the sender, which the filter does not check, is checked at send."""
MISSING = "channel: OUTBOUND_EMAIL_CHANNEL_MISSING"
UNCONFIGURED = "channel: OUTBOUND_EMAIL_NOT_CONFIGURED"

SENDS_EMAIL = {"support", "sales", "mailbox", "bad sender"}
SEATS = {
    "mailer configured": {
        # seat: (the channels `sendable` lists, what a new email on each of CHANNELS answers)
        "replier": (SENDS_EMAIL, (SENT, SENT, SENT, SENDER, MISSING, MISSING, MISSING, MISSING)),
        "service account": (SENDS_EMAIL, (SENT, SENT, SENT, SENDER, MISSING, MISSING, MISSING, MISSING)),
        "administrator": (SENDS_EMAIL, (SENT, SENT, SENT, SENDER, MISSING, MISSING, MISSING, MISSING)),
        "channel owner": ({"support"}, (SENT, DENIED, DENIED, DENIED, DENIED, DENIED, DENIED, DENIED)),
        "feed owner": (set(), (DENIED, DENIED, DENIED, DENIED, DENIED, DENIED, DENIED, MISSING)),
        "reader": (set(), (DENIED,) * 8),
        "integration reader": (set(), (DENIED,) * 8),
        "outsider": (set(), (DENIED,) * 8),
        "anonymous": (set(), (DENIED,) * 8),
    },
    "no mailer": {
        "replier": (set(), (UNCONFIGURED,) * 4 + (MISSING,) * 4),
        "service account": (set(), (UNCONFIGURED,) * 4 + (MISSING,) * 4),
        "administrator": (set(), (UNCONFIGURED,) * 4 + (MISSING,) * 4),
        "channel owner": (set(), (UNCONFIGURED, DENIED, DENIED, DENIED, DENIED, DENIED, DENIED, DENIED)),
        "feed owner": (set(), (DENIED, DENIED, DENIED, DENIED, DENIED, DENIED, DENIED, MISSING)),
        "reader": (set(), (DENIED,) * 8),
        "integration reader": (set(), (DENIED,) * 8),
        "outsider": (set(), (DENIED,) * 8),
        "anonymous": (set(), (DENIED,) * 8),
    },
}
"""Per seat, what ``sendable`` lists and what ``compose_message`` answers a new email on each channel.

A listed channel is one ``compose`` admits, but for a channel whose configured
sender is not an address, which is listed and refused under ``channel``. An
unlisted one is refused or denied.
"""


@pytest.fixture
def world(db: None, settings: Any) -> SimpleNamespace:
    """The channels of :data:`CHANNELS` and the seats of :data:`SEATS`.

    The replier, the service account and the reader hold their relation on every
    channel; the integration reader reads Support's integration; the channel owner
    owns Support and the feed owner the feed; the administrator holds every channel.
    """

    del db
    settings.ANGEE_EMAIL_DELIVERY_CONFIGURED = True
    feed_owner = make_user("compose-reads-feed-owner")
    channels = {
        "support": make_integration("compose-reads-support", model=Channel, backend_class="email"),
        "sales": make_integration("compose-reads-sales", model=Channel, backend_class="email"),
        "mailbox": make_integration("compose-reads-mailbox", model=Channel, backend_class="imap",
                                    config={"host": "192.0.2.10"}),
        "bad sender": make_integration("compose-reads-bad-sender", model=Channel, backend_class="email",
                                       config={"from_email": "Desk <not an address>"}),
        "archive": make_integration("compose-reads-archive", model=Channel, backend_class="email"),
        "paused": make_integration("compose-reads-paused", model=Channel, backend_class="email"),
        "chat": make_integration("compose-reads-chat", model=Channel, backend_class="manual"),
        "feed": make_integration("compose-reads-feed", model=Feed, backend_class="feed", feed_backend_class="stub",
                                 owner=feed_owner),
    }
    replier, reader, integration_reader = (make_user(f"compose-reads-{name}") for name in ("replier", "reader", "ir"))
    service = get_user_model().objects.ensure_service_user(username="compose-reads-service", name="Compose reads")
    with system_context(reason="tests.messaging.compose.reads.world"):
        channels["archive"].disconnect()
        channels["paused"].pause()
        rows = [Channel._base_manager.get(pk=channel.pk) for channel in channels.values()]
        support_integration = Integration._base_manager.get(pk=channels["support"].pk)
    for row in rows:
        for relation, user in (("replier", replier), ("replier", service), ("reader", reader)):
            grant(row, relation, user)
    grant(support_integration, "reader", integration_reader)
    return SimpleNamespace(channels=channels, actors={
        "replier": replier, "service account": service,
        "administrator": create_platform_admin("compose-reads-admin"),
        "channel owner": channels["support"].owner, "feed owner": feed_owner, "reader": reader,
        "integration reader": integration_reader, "outsider": make_user("compose-reads-outsider"), "anonymous": None,
    })


def _read(console: Any, name: str, actor: Any, ids: list[str]) -> tuple[set[str], int | None]:
    """The channel ids ``actor`` reads through one of :data:`READS`, and its count where it has one."""

    document, variables = READS[name]
    data = result_data(execute_schema(console, document, variables(ids), user=actor))
    count = data["channels_aggregate"]["aggregate"]["count"] if "channels_aggregate" in data else None
    return {row["id"] for row in data["channels"]}, count


def _new_email(console: Any, actor: Any, channel: Any, key: str) -> str:
    """What ``compose_message`` answers ``actor`` for a new email on ``channel``, as :data:`SEATS` spells it."""

    result = execute_schema(console, COMPOSE, {
        "channel": channel.public_id, "to": ["client@example.test"], "text": "Hello.", "queued": False,
        "idempotency_key": key,
    }, user=actor)
    if result.errors:
        return str((result.errors[0].extensions or {}).get("code"))
    payload = result.data["compose_message"]
    if payload["ok"]:
        return SENT
    return ": ".join(filter(None, (", ".join(payload["validation_errors"] or {}), payload["code"])))


@pytest.mark.parametrize("configured", ["mailer configured", "no mailer"], ids=["mailer", "no-mailer"])
def test_sendable_lists_the_channels_composing_admits_for_each_seat(
    console: Any, world: SimpleNamespace, settings: Any, configured: str,
) -> None:
    """Each seat is listed the channels a new email on which ``compose_message`` admits, and no other.

    The one exception is stated: the sender is checked at send, so a listed
    channel whose sender is not an address is refused under ``channel``. Each of
    the console's three reads answers alike: the presence read at most two, the
    Reply gate those among the ids it names, the picker each with its count.
    """

    settings.ANGEE_EMAIL_DELIVERY_CONFIGURED = configured == "mailer configured"
    names = {channel.public_id: name for name, channel in world.channels.items()}
    ids = sorted(names)
    read = {}
    for seat, actor in world.actors.items():
        listed, _count = _read(console, "ids", actor, ids)
        picked, count = _read(console, "picker", actor, ids)
        shown, _count = _read(console, "presence", actor, ids)
        assert (picked, count) == (listed, len(listed)), seat
        assert shown <= listed and len(shown) == min(len(listed), 2), seat
        sendable = {names[id] for id in listed}
        answers = tuple(
            _new_email(console, actor, world.channels[name], f"{seat}-{name}-{configured}") for name in CHANNELS
        )
        admitted = {name for name, answer in zip(CHANNELS, answers, strict=True) if answer == SENT}
        refused_at_send = {name for name, answer in zip(CHANNELS, answers, strict=True) if answer == SENDER}
        assert admitted | refused_at_send == sendable and refused_at_send <= {"bad sender"}, seat
        read[seat] = (sendable, answers)
    assert read == SEATS[configured]


@pytest.mark.parametrize("where", [
    "{sendable: {_eq: true}}", "{sendable: {_eq: false}}", "{sendable: {_neq: true}}", "{sendable: {_neq: false}}",
    "{sendable: {_is_null: false}}", "{_not: {sendable: {_eq: true}}}",
    "{_or: [{sendable: {_eq: true}}, {sendable: {_eq: false}}]}",
    "{_or: [{sendable: {_eq: true}}, {lifecycle: {_eq: \"disconnected\"}}]}",
])
def test_an_outsider_and_anonymous_read_no_channel_through_the_filter(
    console: Any, world: SimpleNamespace, where: str,
) -> None:
    document = "query { channels(where: %s) { id } channels_aggregate(where: %s) { aggregate { count } } }"
    for seat in ("outsider", "anonymous"):
        data = result_data(execute_schema(console, document % (where, where), user=world.actors[seat]))
        assert (data["channels"], data["channels_aggregate"]["aggregate"]["count"]) == ([], 0), seat


def _seat(seat: str, size: int, viewer: Any, created: list[Any]) -> list[str]:
    """Bring ``viewer``'s sendable channels to ``size``, each held by ``seat``; return their ids."""

    for index in range(len(created), size):
        slug = f"compose-reads-{seat}-{index}"
        if seat == "owner":
            created.append(make_integration(slug, model=Channel, backend_class="email", owner=viewer))
        else:
            created.append(make_integration(slug, model=Channel, backend_class="email"))
            if seat == "replier":
                grant(created[-1], "replier", viewer)
    return [channel.public_id for channel in created]


@pytest.mark.parametrize("seat", ["owner", "replier", "administrator"])
def test_each_read_of_sendable_costs_what_reading_the_permissions_costs(
    console: Any, db: None, settings: Any, seat: str,
) -> None:
    """The header's presence, a transcript's Reply gate and the picker's options cost the same at 1 channel as at 25.

    ``sendable`` decides ``reply`` inside the channels' own statement, so the
    presence read and the Reply gate cost what the same reads cost answering
    ``permissions``; the picker pays for its count as well (:data:`BUDGETS`).
    """

    del db
    settings.ANGEE_EMAIL_DELIVERY_CONFIGURED = True
    viewer = create_platform_admin(f"compose-reads-{seat}") if seat == "administrator" else make_user(
        f"compose-reads-{seat}")
    created: list[Any] = []
    counts: dict[tuple[str, int], tuple[int, int]] = {}
    for size in SIZES:
        ids = _seat(seat, size, viewer, created)
        for name, (document, variables) in READS.items():
            read = _measured(console, document, variables(ids), viewer)
            listed = {row["id"] for row in read.data["channels"]}
            assert listed <= set(ids) and len(listed) == (min(size, 2) if name == "presence" else size), (name, size)
            assert read.data.get("channels_aggregate", {"aggregate": {"count": size}})["aggregate"]["count"] == size
            counts[name, size] = (read.statements, read.repeated)
        for name, document in DRAFT.items():
            read = _measured(console, document, {"ids": ids} if "$ids" in document else {}, viewer)
            assert sorted(row["id"] for row in read.data["channels"]) == sorted(ids)
            counts[f"{name} answering permissions", size] = (read.statements, read.repeated)
    for name, size in list(counts):
        assert counts[name, size] == counts[name, SIZES[0]], (seat, name, counts)
    for name in DRAFT:
        (statements, repeated), (draft, draft_repeated) = counts[name, 25], counts[f"{name} answering permissions", 25]
        assert statements <= draft and repeated <= draft_repeated, (seat, name, counts)
    assert {name: counts[name, 25] for name in READS} == BUDGETS[seat], (seat, counts)


@pytest.mark.parametrize("status", Message.MessageStatus.values)
def test_a_message_reads_whether_it_was_written_here_in_each_status(
    console: Any, desk: Any, status: str,
) -> None:
    """A composed message reads as written here in each delivery state, not in one only a source's copy holds."""

    message = Message.objects.compose(channel=desk, to=["client@example.test"], text="Hi.", actor=desk.owner)
    with system_context(reason="tests.messaging.compose.reads.status"):
        Message._base_manager.filter(pk=message.pk).update(status=status)
    read = result_data(execute_schema(
        console, "query Message($id: String!) { messages_by_pk(id: $id) { status is_locally_written } }",
        {"id": message.public_id}, user=desk.owner,
    ))["messages_by_pk"]
    assert read == {"status": status.upper(), "is_locally_written": status not in ("synced", "edited")}


def test_whether_a_message_was_written_here_follows_its_status_not_its_direction(console: Any, desk: Any) -> None:
    """Mail synced in either direction is not written here; an internal note is, though it has no delivery.

    So a reader showing delivery status requires an outgoing direction too.
    """

    with system_context(reason="tests.messaging.compose.reads.kinds"):
        (synced,) = Message.objects.ingest([ParsedMessage(
            external_id="<sent-elsewhere@example.test>", platform="email", direction="outbound",
            subject="Sent from another client", sender=ParsedHandle(platform="email", value="desk@example.test"),
            body=ParsedPart(text="Hello."),
        )], channel=desk)
        thread = Thread._base_manager.get(pk=synced.thread_id)
        note = Message.objects.post_to_thread(thread, body="An internal note.", created_by_id=desk.owner.pk)
    received = _receive(desk, "received")
    document = "query Message($id: String!) { messages_by_pk(id: $id) { direction status is_locally_written } }"
    read = {kind: result_data(execute_schema(console, document, {"id": message.public_id}, user=desk.owner))[
        "messages_by_pk"] for kind, message in (("received", received), ("synced", synced), ("note", note))}
    assert read == {
        "received": {"direction": "INBOUND", "status": "SYNCED", "is_locally_written": False},
        "synced": {"direction": "OUTBOUND", "status": "SYNCED", "is_locally_written": False},
        "note": {"direction": "INTERNAL", "status": "SENT", "is_locally_written": True},
    }


def test_whether_a_message_was_written_here_is_read_with_its_row(console: Any, desk: Any) -> None:
    """A transcript page selecting it alone costs what the page costs without it, at 1 message as at 25."""

    counts = {}
    for rows in SIZES:
        conversation = _conversation(desk, rows)
        for fields in ("id", "id is_locally_written"):
            read = _measured(
                console, "query Feed($thread: ID!) { thread_message_feed(thread_id: $thread, limit: 50) {"
                " messages { %s } } }" % fields, {"thread": conversation["thread"]}, desk.owner,
            )
            messages = read.data["thread_message_feed"]["messages"]
            assert len(messages) == rows
            if "is_locally_written" in fields:
                assert sum(message["is_locally_written"] for message in messages) == rows - 1
            counts[fields, rows] = (read.statements, read.repeated)
    assert counts["id is_locally_written", 1] == counts["id", 1], counts
    assert counts["id is_locally_written", 25] == counts["id", 25], counts


def test_the_console_states_the_recipient_bound_that_composing_enforces(
    console: Any, desk: Any, settings: Any,
) -> None:
    """The root answers the deployment's bound, and a message crossing it is refused under the role that does."""

    bound = "query Bound { message_recipient_limit }"
    assert result_data(execute_schema(console, bound, user=desk.owner)) == {"message_recipient_limit": 50}
    settings.ANGEE_MESSAGING_MAX_RECIPIENTS = 2
    assert result_data(execute_schema(console, bound, user=desk.owner)) == {"message_recipient_limit": 2}
    two = ["one@example.test", "two@example.test"]
    with pytest.raises(_Refusal) as refused:
        _compose(console, desk.owner, **_new(desk, to=two, cc=["three@example.test"]), idempotency_key="over")
    assert refused.value.payload is not None
    assert refused.value.payload["validation_errors"] == {"cc": ["A message takes at most 2 recipients."]}
    assert _compose(console, desk.owner, **_new(desk, to=two), idempotency_key="at")["ok"]
