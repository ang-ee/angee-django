"""The composer's story fixture is what the server says, recorded; fail here when it drifts.

``src/angee/messaging/web/src/compose.fixtures.recorded.json`` holds every value the
console's New message dialog, reply box and transcript stories and native tests quote
from the server: the console resource metadata of ``messaging.Message``,
``messaging.Channel`` and ``messaging.Part`` as a host enabling messaging emits it; per seat, the channels it
reads and those it may send on as the page's three ``sendable`` reads answer, and the
recipient bound; the Support and Sales conversations as each seat's transcript reads
them, Support holding a message in every local delivery state and a synced outgoing
row; what ``compose_message`` answers each send the stories make, keyed by seat and by
its variables without the key, with the conversation read again after it where it
wrote; and the message page's row of the failed and the uncertain message per seat.
The documents are the page's own (``documents.ts``), read from its source. Public ids
are stable aliases, cursors and feed keys keep their order, and times are fixed, so an
unchanged server records the same file.

The test records it afresh and fails when the committed file differs. Re-record it,
never edit it, with ``ANGEE_RECORD_FIXTURES=1`` (the command is in ``docs/checks.md``),
whenever the console's schema, a document of the page or a server message changes.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from smtplib import SMTPRecipientsRefused
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from django.db import transaction
from django.utils import timezone
from django_compose.components import enabled_apps
from graphql import DocumentNode, FragmentDefinitionNode, FragmentSpreadNode, OperationDefinitionNode, print_ast, visit
from graphql.language import Visitor
from rebac import system_context

from angee.graphql.schema import GraphQLSchemas
from angee.graphql.testing.documents import authored_documents, execute_schema
from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedPart
from angee.messaging.constants import DELIVERY_TASK_EXPIRES, TRANSPORT_MAILER_ALIAS
from angee.messaging.delivery import run_message_delivery
from angee.messaging.testing.models import Channel, Message
from tests.conftest import Feed, create_user, make_integration
from tests.messaging_campaign import grant
from tests.test_messaging_compose import MAILERS
from tests.test_messaging_compose import desk as desk
from tests.test_messaging_compose_graphql import mailer as mailer

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src/angee/messaging/web/src"
FIXTURE = WEB / "compose.fixtures.recorded.json"
RECORD = os.environ.get("ANGEE_RECORD_FIXTURES") == "1"

MESSAGE_MODEL = "messaging.Message"
CHANNEL_MODEL = "messaging.Channel"
PART_MODEL = "messaging.Part"

SEATS = ("ana", "ben", "cleo", "dana")
"""The seats the stories read as: Ana may send on Support and reads Sales; Ben reads Support; Cleo manages Support;
Dana may send on Support, Sales and Billing. ``anaRevoked`` is Ana once her right to send on Support is revoked."""

CHANNELS: dict[str, dict[str, Any]] = {
    "sales": {"display_name": "Sales desk", "config": {"from_email": "Sales <sales@example.test>"}},
    # Connected and offered, and refused at send: its configured sender is not an address.
    "billing": {"display_name": "Billing desk", "config": {"from_email": "Billing <not an address>"}},
    "archive": {"display_name": "Archive desk", "config": {"from_email": "Archive <archive@example.test>"}},
}
"""The email channels beside Support (the compose tests' desk); Archive is disconnected. A feed stands beside them."""

_MESSAGE_ROW = (
    "id display_name title status revision platform direction message_type delivery_actions delivery_error "
    "delivery_attempts scheduled_at sent_at created_at is_held is_locally_written preview starred permissions "
    "thread { id modality } channel { id display_name } "
    "sender { id display_name value party_link_confirmed avatar { id url } "
    "party { id display_name display_avatar { id url } } "
    "merged_into { id party_link_confirmed party { id display_name display_avatar { id url } } } } "
    "parent { id preview message_type sender { id display_name value } thread { id modality } } "
    "recipients { id role handle { id display_name value } } "
    "parts { id name type position role disposition cid parent { id type } fragment { id text } "
    "file { id filename content_hash title size_bytes url mime_type { mime_type label } } } "
    "tags { id name }"
)
"""The message page's one row: the union of what ``MessageForm`` declares and ``MessageReaderDocument`` selects."""

_SAVED_AT = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")
_FEED_KEY = re.compile(r"1:\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}):\d{20}")


def _page_documents() -> dict[str, str]:
    """Each operation the page authors in ``documents.ts``, with every fragment it spreads, as the page sends it."""

    definitions = [item for document in authored_documents(WEB / "documents.ts") for item in document.definitions]
    fragments = {item.name.value: item for item in definitions if isinstance(item, FragmentDefinitionNode)}

    def spreads(node: Any) -> set[str]:
        found: set[str] = set()

        class Spreads(Visitor):
            def enter_fragment_spread(self, spread: FragmentSpreadNode, *_args: Any) -> None:
                found.add(spread.name.value)

        visit(node, Spreads())
        return found

    documents = {}
    for operation in (item for item in definitions if isinstance(item, OperationDefinitionNode) and item.name):
        needed: list[str] = []
        pending = sorted(spreads(operation))
        while pending:
            name = pending.pop(0)
            if name in needed:
                continue
            needed.append(name)
            pending.extend(sorted(spreads(fragments[name]) - set(needed)))
        documents[operation.name.value if operation.name else ""] = print_ast(
            DocumentNode(definitions=(operation, *(fragments[name] for name in sorted(needed)))),
        )
    return documents


@contextmanager
def _rolled_back() -> Iterator[None]:
    """Run one recording against the conversations as they stand, then put them back."""

    with transaction.atomic():
        yield
        transaction.set_rollback(True)


def _system() -> Any:
    return system_context(reason="tests.messaging.compose.console_fixture")


def _answer(result: Any) -> dict[str, Any]:
    """One response as the wire carries it: its data, or its first error with its code."""

    if result.errors:
        error = result.errors[0]
        return {"error": {"message": error.message, "path": list(error.path or []),
                          "extensions": dict(error.extensions or {})}}
    return {"data": result.data}


def _aliased(value: Any, ids: dict[str, str]) -> Any:
    """``value`` with every public id in ``ids`` replaced by its alias, wherever a string holds it, longest first."""

    if not ids:
        return value
    pattern = re.compile("|".join(re.escape(id_) for id_ in sorted(ids, key=len, reverse=True)))

    def walk(item: Any) -> Any:
        if isinstance(item, dict):
            return {walk(key): walk(inner) for key, inner in item.items()}
        if isinstance(item, list):
            return [walk(inner) for inner in item]
        return pattern.sub(lambda match: ids[match.group(0)], item) if isinstance(item, str) else item

    return walk(value)


def _strings(value: Any) -> Iterator[tuple[str | None, str]]:
    """Every string ``value`` holds, with the key it sits under, keys sorted so the walk is the same every time."""

    if isinstance(value, dict):
        for key in sorted(value):
            inner = value[key]
            if isinstance(inner, str):
                yield key, inner
            else:
                yield from _strings(inner)
    elif isinstance(value, list):
        for inner in value:
            if isinstance(inner, str):
                yield None, inner
            else:
                yield from _strings(inner)


def _settled(recording: dict[str, Any], named: dict[str, str], given: set[str]) -> dict[str, Any]:
    """The recording with stable aliases and fixed times.

    The rows the stories name keep their names (``named``, and ``given``: the names a
    recording of its own gave the messages it wrote); every other public id read (a
    handle, a party, a part, a fragment, a file) becomes ``<prefix>_<n>`` in the order a
    sorted walk meets it. Times keep their order and become minutes after a fixed
    morning, feed keys keep their order and lose what they encode, and cursors are
    numbered as the walk meets them.
    """

    ids = dict(named)
    taken = set(named.values()) | given
    counts: dict[str, int] = {}
    for key, text in _strings(recording):
        if key == "id" and text not in ids and text not in taken and "_" in text:
            prefix = text.split("_", 1)[0]
            counts[prefix] = counts.get(prefix, 0) + 1
            ids[text] = f"{prefix}_{counts[prefix]}"
    aliased = _aliased(recording, ids)
    times = sorted({text for _key, text in _strings(aliased) if _TIME.fullmatch(text)},
                   key=lambda text: datetime.fromisoformat(text.replace("Z", "+00:00")))
    keys = sorted({text for _key, text in _strings(aliased) if _FEED_KEY.fullmatch(text)})
    # A cursor is opaque: named in the order the walk meets it, as the client never compares one.
    cursors = list(dict.fromkeys(text for key, text in _strings(aliased) if key and key.endswith("_cursor")))
    fixed = {text: (_SAVED_AT + timedelta(minutes=index)).isoformat() for index, text in enumerate(times)}
    fixed.update({text: f"1:{index:06d}" for index, text in enumerate(keys)})
    fixed.update({text: f"cursor-{index:03d}" for index, text in enumerate(cursors)})

    def walk(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: walk(inner) for key, inner in item.items()}
        if isinstance(item, list):
            return [walk(inner) for inner in item]
        return fixed.get(item, item) if isinstance(item, str) else item

    return walk(aliased)


def _inbound(channel: Any, key: str, *, answers: str = "", reply_to: str = "", text: str = "Please add me.",
             direction: str = "inbound", sender: tuple[str, str] = ("requester@example.test", "Requester")) -> Any:
    """One email on ``channel`` through messaging's ingest, in the conversation of the email ``answers`` names."""

    with _system():
        (message,) = Message.objects.ingest([ParsedMessage(
            external_id=f"<{key}@example.test>", platform="email", direction=direction, subject="Access request",
            headers=(("Reply-To", reply_to),) if reply_to else (),
            sender=ParsedHandle(platform="email", value=sender[0], display_name=sender[1]),
            in_reply_to=f"<{answers}@example.test>" if answers else "",
            references=(f"<{answers}@example.test>",) if answers else (),
            body=ParsedPart(text=text),
        )], channel=channel)
    return message


def _world(desk: Any, settings: Any) -> SimpleNamespace:
    """The channels, the seats and the two conversations, each outgoing message in its delivery state."""

    settings.MAILERS = MAILERS
    with _system():
        Channel._base_manager.filter(pk=desk.pk).update(display_name="Support desk")
    channels = {"support": Channel._base_manager.get(pk=desk.pk)}
    for name, values in CHANNELS.items():
        channels[name] = make_integration(f"composer-fixture-{name}", model=Channel, backend_class="email", **values)
    feed_owner = create_user("composer-fixture-feed-manager")
    channels["feed"] = make_integration("composer-fixture-feed", model=Feed, backend_class="feed",
                                        feed_backend_class="stub", owner=feed_owner, display_name="Announcements")
    with _system():
        channels["archive"].disconnect()
        # Grants name the channel row itself, whatever kind of channel it is.
        rows = {name: Channel._base_manager.get(pk=channel.pk) for name, channel in channels.items()}
    seats = {name: create_user(f"composer-fixture-{name}") for name in ("ana", "ben", "dana")}
    seats["cleo"] = desk.owner
    for name, relation in (("ana", "replier"), ("ana", "reader"), ("ben", "reader"), ("dana", "replier")):
        grant(rows["support"], relation, seats[name])
    grant(rows["sales"], "reader", seats["ana"])
    for name in ("sales", "billing", "archive", "feed"):
        grant(rows[name], "replier", seats["dana"])
    return SimpleNamespace(channels=channels, seats=seats)


def test_the_composer_fixture_is_what_the_server_says(  # noqa: PLR0915 -- one recording, read top to bottom
    desk: Any, mailer: list[dict[str, Any]], settings: Any, monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: Any,
) -> None:
    """The committed fixture equals a fresh recording; ``ANGEE_RECORD_FIXTURES=1`` writes the recording instead."""

    world = _world(desk, settings)
    channels, seats = world.channels, world.seats
    documents = _page_documents()
    schemas = GraphQLSchemas(enabled_apps(("angee.messaging",), group="angee.addons").app_configs)
    schema = schemas.build("console")
    cleo = seats["cleo"]

    def run(document: str, variables: dict[str, Any], seat: Any) -> Any:
        return execute_schema(schema, document, variables, user=seat)

    def read(document: str, variables: dict[str, Any], seat: Any) -> dict[str, Any]:
        result = run(document, variables, seat)
        assert not result.errors, result.errors
        return result.data

    def compose(seat: Any, key: str, **variables: Any) -> dict[str, Any]:
        """What ``compose_message`` answers ``seat``, its delivery handed on and recorded by the mailer."""

        with django_capture_on_commit_callbacks(execute=True):
            return _answer(run(documents["MessagingComposeMessage"], {**variables, "idempotency_key": key}, seat))

    def delivered(answer: dict[str, Any]) -> Any:
        assert "data" in answer and answer["data"]["compose_message"]["ok"], answer
        return Message._base_manager.get(sqid=answer["data"]["compose_message"]["id"])

    def deliver(message: Any) -> None:
        """Run the delivery composing handed on for ``message``, once."""

        delivery = next(item for item in mailer if str(item["pk"]) == str(message.pk))
        run_message_delivery(**delivery)

    def refusing(code: int) -> None:
        transport = Mock()
        transport.send_messages.side_effect = SMTPRecipientsRefused({"client@example.test": (code, b"Refused.")})
        monkeypatch.setattr("angee.messaging.email.mailers", {TRANSPORT_MAILER_ALIAS: transport})

    # The Support conversation: one request, then an outgoing message in each local state, and synced own mail.
    request = _inbound(desk, "support-request")
    unaddressed = _inbound(desk, "support-unaddressed", answers="support-request", reply_to="undisclosed-recipients:;",
                           text="Who can see this?")
    crowded = _inbound(desk, "support-crowded", answers="support-request", text="Copying the whole team.",
                       reply_to=", ".join(f"member{index}@example.test" for index in range(51)))
    sales_request = _inbound(channels["sales"], "sales-request", text="A quote, please.")
    answers = {}
    for state, text in (("queued", "We are on it."), ("sent", "Done: you are in."), ("failed", "Your access is ready."),
                        ("retrying", "Checking with the team."), ("uncertain", "One more thing.")):
        answers[state] = delivered(compose(cleo, f"support-{state}", parent=request.public_id, text=text, queued=True))
    later = timezone.now() + timedelta(days=1)
    answers["scheduled"] = delivered(compose(cleo, "support-scheduled", parent=request.public_id, text="Reminder.",
                                             queued=False, scheduled_at=later.isoformat()))
    deliver(answers["sent"])
    refusing(421)
    deliver(answers["retrying"])
    deliver(answers["failed"])
    with _system():
        # The recording keeps only the order of its times, so the times set here stay clear of the ones the run
        # writes, however slow it is: the provider's one-minute retry moves to half a day out (the scheduled draft
        # stays later), and the failed message's retry falls due a day back; the provider then refuses it for good:
        # two attempts.
        retry = later - timedelta(hours=12)
        Message._base_manager.filter(pk=answers["retrying"].pk).update(
            scheduled_at=retry, delivery_lease_until=retry + timedelta(seconds=DELIVERY_TASK_EXPIRES))
        Message._base_manager.filter(pk=answers["failed"].pk).update(scheduled_at=timezone.now() - timedelta(days=1))
    refusing(550)
    deliver(answers["failed"])
    uncertain = answers["uncertain"]
    claimed = Message.objects.claim_delivery(uncertain.pk, token=uncertain.external_id)
    assert claimed is not None
    Message.objects.release_held_messages(now=claimed.delivery_lease_until + timedelta(seconds=1))
    synced = _inbound(desk, "support-synced", answers="support-request", direction="outbound",
                      sender=("desk@example.test", "Desk"), text="Sent from the desk's own mail client.")
    statuses = {state: Message._base_manager.get(pk=row.pk).status for state, row in answers.items()}
    assert statuses == {"queued": "queued", "sent": "sent", "failed": "failed", "retrying": "queued",
                        "uncertain": "uncertain", "scheduled": "draft"}, statuses
    assert Message._base_manager.get(pk=synced.pk).status == "synced"
    assert {row.thread_id for row in (request, unaddressed, crowded, synced)} == {request.thread_id}

    named = {channel.public_id: f"chn_{name}" for name, channel in channels.items()}
    named.update({request.thread.public_id: "thr_support", sales_request.thread.public_id: "thr_sales"})
    named.update({request.public_id: "msg_request", unaddressed.public_id: "msg_unaddressed",
                  crowded.public_id: "msg_crowded", sales_request.public_id: "msg_sales_request",
                  synced.public_id: "msg_synced"})
    named.update({row.public_id: f"msg_{state}" for state, row in answers.items()})
    channel_ids = sorted(channel.public_id for channel in channels.values())
    conversations = {"support": request.thread.public_id, "sales": sales_request.thread.public_id}

    def transcript(seat: Any, thread: str) -> Any:
        result = run(documents["MessagingThreadTranscript"], {"threadId": thread, "limit": 50}, seat)
        return _answer(result)

    def seat_reads(seat: Any) -> dict[str, Any]:
        """What one seat reads of the channels and the conversations."""

        presence = read(documents["MessagingSendableChannels"], {}, seat)
        sendable = read("query { channels(where: {sendable: {_eq: true}}, order_by: [{display_name: asc}]) "
                        "{ id display_name } }", {}, seat)["channels"]
        ids = read(documents["MessagingSendableChannelIds"], {"ids": channel_ids}, seat)["channels"]
        # The fake answers the presence read and the Reply gate from `sendable`: prove that is what they say.
        assert presence["channels"] == sendable[:2] and {row["id"] for row in ids} == {row["id"] for row in sendable}
        readable = read("query { channels(order_by: [{display_name: asc}]) { id display_name } }", {}, seat)
        return {
            "channels": readable["channels"],
            "sendable": sendable,
            "recipientLimit": presence["message_recipient_limit"],
            "transcripts": {name: transcript(seat, thread) for name, thread in conversations.items()},
        }

    readers = {name: seat_reads(seat) for name, seat in seats.items()}

    page = {}
    for name in ("cleo", "ana"):
        page[name] = {state: _answer(run(f"query($id: String!) {{ messages_by_pk(id: $id) {{ {_MESSAGE_ROW} }} }}",
                                         {"id": answers[state].public_id}, seats[name]))
                      for state in ("failed", "uncertain")}

    sends: list[dict[str, Any]] = []
    given: set[str] = set()

    def send(seat: str, key: str, variables: dict[str, Any], *, reread: str | None = None) -> None:
        """Record what ``seat`` is answered for ``variables``, in a recording of its own, and the conversation after.

        A message it wrote is named by the seat and the key, in its own entry: the next recording reuses its id.
        """

        with _rolled_back():
            answer = compose(seats[seat], key, **variables)
            entry = {"seat": seat, "variables": variables, "answer": answer}
            written = {}
            if "data" in answer and answer["data"]["compose_message"]["ok"]:
                written = {answer["data"]["compose_message"]["id"]: f"msg_{seat}_{key.replace('-', '_')}"}
                given.update(written.values())
                if reread:
                    entry["conversation"] = reread
                    entry["transcript"] = transcript(seats[seat], conversations[reread])
        sends.append(_aliased(entry, written))

    support, sales, billing = (channels[name].public_id for name in ("support", "sales", "billing"))
    new_email = {"channel": support, "to": ["client@example.test"], "subject": "Your order",
                 "text": "Hello from Support.", "queued": True}
    send("ana", "new", new_email)
    send("dana", "new-sales", {"channel": sales, "to": ["buyer@example.test"], "cc": ["team@example.test"],
                               "text": "Hello from Sales.", "queued": True})
    send("ana", "scheduled", {"channel": support, "to": ["client@example.test"], "text": "See you tomorrow.",
                              "queued": False, "scheduled_at": later.isoformat()})
    send("ana", "refused-address", {"channel": support, "to": ["ana"], "text": "Hello.", "queued": True})
    send("ana", "refused-encoded", {"channel": support, "to": ["=?utf-8?q?Ana?= <ana@example.test>"],
                                    "text": "Hello.", "queued": True})
    send("ana", "too-many", {"channel": support, "to": [f"guest{index}@example.test" for index in range(51)],
                             "text": "Hello, everyone.", "queued": True})
    send("dana", "refused-sender", {"channel": billing, "to": ["client@example.test"], "text": "Your invoice.",
                                    "queued": True})
    with _rolled_back():
        compose(seats["ana"], "conflict", channel=support, to=["client@example.test"], text="First version.",
                queued=True)
        conflict = _answer(run(documents["MessagingComposeMessage"], {
            "channel": support, "to": ["client@example.test"], "text": "Second version.", "queued": True,
            "idempotency_key": "conflict",
        }, seats["ana"]))
    reply = {"parent": request.public_id, "text": "Thanks, adding you now.", "queued": True}
    send("ana", "reply", reply, reread="support")
    send("dana", "reply-sales", {"parent": sales_request.public_id, "text": "Quote attached soon.", "queued": True},
         reread="sales")
    send("ana", "reply-unaddressed", {"parent": unaddressed.public_id, "text": "Only you.", "queued": True})
    send("ana", "reply-crowded", {"parent": crowded.public_id, "text": "Hello, all.", "queued": True})
    # Ana loses her right to send on Support; the reply's key and the new email's, replayed, are refused for who may
    # write, though each original was written.
    with _rolled_back():
        compose(seats["ana"], "reply", **reply)
        compose(seats["ana"], "new", **new_email)
        with _system():
            channels["support"].revoke_record_access("replier", seats["ana"])
        for key, variables in (("reply", reply), ("new", new_email)):
            sends.append({"seat": "anaRevoked", "variables": variables,
                          "answer": compose(seats["ana"], key, **variables)})

    console: Any = schemas.render_metadata()["console"]
    metadata = {
        resource["modelLabel"]: resource for resource in console["angee"]["resources"]
        if resource["modelLabel"] in {MESSAGE_MODEL, CHANNEL_MODEL, PART_MODEL}
    }
    recording = _settled({
        "metadata": metadata,
        "seats": readers,
        "messages": {state: row.public_id for state, row in {**answers, "request": request, "synced": synced,
                                                             "unaddressed": unaddressed, "crowded": crowded,
                                                             "salesRequest": sales_request}.items()},
        "channels": {name: channel.public_id for name, channel in channels.items()},
        "threads": conversations,
        "sends": sends,
        "conflict": conflict,
        "messagePage": page,
    }, named, given)
    text = json.dumps(recording, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if RECORD:
        FIXTURE.write_text(text, encoding="utf-8")
    committed = json.loads(FIXTURE.read_text(encoding="utf-8"))
    recorded = json.loads(text)
    drifted = sorted(key for key in committed.keys() | recorded.keys() if committed.get(key) != recorded.get(key))
    assert not drifted, (
        f"{FIXTURE.name} no longer says what the server says ({', '.join(drifted)}): re-record it with "
        "ANGEE_RECORD_FIXTURES=1 (docs/checks.md) and review its diff."
    )
    assert FIXTURE.read_text(encoding="utf-8") == text, f"{FIXTURE.name} is not written as the recorder writes it."
