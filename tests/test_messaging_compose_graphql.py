"""The console composes a message through its one owner and reads back what delivery made of it."""

from __future__ import annotations

from datetime import timedelta
from smtplib import SMTPRecipientsRefused
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from django.core import mail
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rebac import evaluator_scope, system_context

from angee.graphql.testing.documents import execute_schema, measure_graphql_document, result_data
from angee.jobs.enqueue import celery_app
from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedPart, ParsedThread
from angee.messaging.constants import DELIVER_MESSAGE_TASK
from angee.messaging.delivery import run_message_delivery
from angee.messaging.testing.models import Channel, Message, Recipient, Thread
from angee.provenance.context import changeset_model
from tests.conftest import File, create_platform_admin, make_integration
from tests.messaging_campaign import grant, make_user
from tests.test_messaging_compose import DESK, _file, _receive, _refused, _rows, _wire
from tests.test_messaging_compose import desk as desk
from tests.test_messaging_graphql import _schema

COMPOSE = """
mutation ComposeMessage(
  $text: String!, $idempotency_key: String!, $queued: Boolean!, $parent: ID, $channel: ID,
  $to: [String!], $cc: [String!], $bcc: [String!], $subject: String! = "", $attachments: [ID!],
  $scheduled_at: DateTime
) {
  compose_message(
    text: $text, idempotency_key: $idempotency_key, queued: $queued, parent: $parent, channel: $channel,
    to: $to, cc: $cc, bcc: $bcc, subject: $subject, attachments: $attachments, scheduled_at: $scheduled_at
  ) { ok message code validation_errors id }
}
"""

DELIVERY = """
query Delivery($message: String!, $channel: String!) {
  messages_by_pk(id: $message) { id status is_held scheduled_at delivery_error delivery_attempts }
  channels_by_pk(id: $channel) { id permissions }
}
"""

SAVEPOINTS = ("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")


@pytest.fixture(scope="module")
def console() -> Any:
    """The console schema messaging composes into."""

    return _schema()


class _Refusal(Exception):
    """``compose_message`` composed nothing: ``payload`` is its in-band result, ``code`` its GraphQL error's."""

    def __init__(self, *, payload: dict[str, Any] | None = None, code: str | None = None) -> None:
        super().__init__(payload or code)
        self.payload, self.code = payload, code


def _compose(console: Any, actor: Any, **arguments: Any) -> dict[str, Any]:
    """Run ``compose_message`` as ``actor`` and return its result; raise ``_Refusal`` when it composed nothing."""

    result = execute_schema(console, COMPOSE, {"queued": True, **arguments}, user=actor)
    if result.errors:
        raise _Refusal(code=(result.errors[0].extensions or {}).get("code"))
    payload = result.data["compose_message"]
    if not payload["ok"]:
        raise _Refusal(payload=payload)
    return payload


def _new(channel: Any, **arguments: Any) -> dict[str, Any]:
    return {"channel": channel.public_id, "to": ["Client <client@example.test>"], "text": "Hello.", **arguments}


def _delivery(console: Any, actor: Any, message: str, channel: Any) -> tuple[Any, Any]:
    """What ``actor`` reads of a message's delivery and of the channel it went out on."""

    data = result_data(execute_schema(console, DELIVERY, {"message": message, "channel": channel.public_id},
                                      user=actor))
    return data["messages_by_pk"], data["channels_by_pk"]


def _failed(desk: Any) -> Any:
    """A message on ``desk`` whose delivery settlement recorded two refused attempts."""

    message = Message.objects.compose(channel=desk, to=["client@example.test"], text="Hi.", actor=desk.owner)
    with system_context(reason="tests.messaging.compose.graphql.settled"):
        Message._base_manager.filter(pk=message.pk).update(status="failed", metadata={"local": {
            "delivery_error": "The email recipients were refused.", "delivery_attempts": 2,
        }})
    return message


def test_an_answer_is_composed_on_its_parents_thread_and_threads_back_to_it(console: Any, desk: Any) -> None:
    request = _receive(desk, "answered", reply_to="Applicant <applicant@example.test>")
    composed = _compose(console, desk.owner, parent=request.public_id, text="Which team?", idempotency_key="answer")
    message = Message._base_manager.get(sqid=composed["id"])
    assert (message.parent_id, message.thread_id, message.status) == (request.pk, request.thread_id, "queued")
    with system_context(reason="tests.messaging.compose.graphql.answer"):
        to = list(Recipient._base_manager.filter(message=message).values_list("role", "handle__value"))
    assert to == [("to", "applicant@example.test")]
    wire = _wire(desk, message)
    assert wire["In-Reply-To"] == wire["References"] == request.external_id
    assert wire["From"] == DESK


def test_a_new_email_carries_its_envelope_and_attachments(console: Any, desk: Any) -> None:
    attachment = _file(desk.owner, b"Invoice 42: 100.00")
    composed = _compose(
        console, desk.owner, **_new(desk, cc=["copy@example.test"], bcc=["blind@example.test"]),
        subject="Invoice 42", attachments=[attachment.public_id], idempotency_key="new",
    )
    message = Message._base_manager.get(sqid=composed["id"])
    wire = _wire(desk, message)
    assert (wire["From"], wire["To"], wire["Cc"], wire["Subject"]) == (
        DESK, "Client <client@example.test>", "copy@example.test", "Invoice 42",
    )
    assert wire["Message-ID"] == f"<{message.external_id}>"
    with system_context(reason="tests.messaging.compose.graphql.new"):
        roles = sorted(Recipient._base_manager.filter(message=message).values_list("role", "handle__value"))
    assert roles == [("bcc", "blind@example.test"), ("cc", "copy@example.test"), ("to", "client@example.test")]
    assert [part.get_filename() for part in wire.walk() if part.get_content_disposition() == "attachment"] == [
        "invoice.txt",
    ]


def test_a_message_is_held_or_scheduled_as_asked(
    console: Any, desk: Any, django_capture_on_commit_callbacks: Any,
) -> None:
    later = timezone.now() + timedelta(hours=1)
    with django_capture_on_commit_callbacks(execute=True):
        held = _compose(console, desk.owner, **_new(desk), queued=False, idempotency_key="held")
        scheduled = _compose(
            console, desk.owner, **_new(desk), queued=False, scheduled_at=later.isoformat(), idempotency_key="later",
        )
    assert desk.sent == []
    assert held["message"] != scheduled["message"]
    held_row, _channel = _delivery(console, desk.owner, held["id"], desk)
    scheduled_row, _channel = _delivery(console, desk.owner, scheduled["id"], desk)
    assert (held_row["status"], held_row["is_held"], held_row["scheduled_at"]) == ("DRAFT", True, None)
    assert (scheduled_row["status"], scheduled_row["scheduled_at"]) == ("DRAFT", later.isoformat())


@pytest.mark.parametrize(
    ("anchor", "arguments", "key"),
    [
        pytest.param("new", {"channel": None}, "channel", id="no-anchor"),
        pytest.param("new", {"text": "  "}, "text", id="no-text"),
        pytest.param("new", {"subject": "One\nTwo"}, "subject", id="two-line-subject"),
        pytest.param("reply", {"subject": "Other"}, "subject", id="titled-answer"),
        pytest.param("reply", {"to": ["other@example.test"]}, "to", id="addressed-answer"),
        pytest.param("new", {"to": []}, "to", id="no-recipient"),
        pytest.param("new", {"to": ["not an address"]}, "to", id="not-an-address"),
        pytest.param("new", {"to": [], "cc": ["Reader <reader@example>"]}, "cc", id="cc-not-an-address"),
        pytest.param("new", {"to": [], "bcc": ["x" * 320 + "@example.test"]}, "bcc", id="bcc-not-an-address"),
        pytest.param("new", {"queued": True, "scheduled_at": "later"}, "scheduledAt", id="queued-and-scheduled"),
        pytest.param("new", {"idempotency_key": " "}, "idempotencyKey", id="blank-key"),
        pytest.param("new", {"idempotency_key": "k" * 129}, "idempotencyKey", id="oversized-key"),
        pytest.param("chat", {}, "sender", id="chat-answer-without-sender"),
    ],
)
def test_a_refusal_of_the_arguments_binds_under_its_key_before_anything_is_written(
    console: Any, desk: Any, anchor: str, arguments: dict[str, Any], key: str,
) -> None:
    """Each key is the argument's camel-cased name; one the form has no control for shows at form level."""

    actor = desk.owner
    if anchor == "chat":
        chat = make_integration("compose-graphql-chat", model=Channel, backend_class="manual")
        question = _receive(chat, "room/1", sender="U-asker", platform="slack", thread=ParsedThread(external_id="room"))
        actor, values = chat.owner, {"parent": question.public_id, "text": "Yes."}
    elif anchor == "reply":
        values = {"parent": _receive(desk, "refused").public_id, "text": "Which team?"}
    else:
        values = _new(desk)
    if arguments.get("scheduled_at") == "later":
        arguments = {**arguments, "scheduled_at": (timezone.now() + timedelta(hours=1)).isoformat()}
    with _refused(_Refusal) as refused:
        _compose(console, actor, **{"idempotency_key": f"refused-{key}", **values, **arguments})
    payload = refused.value.payload
    assert payload is not None and list(payload["validation_errors"]) == [key], payload


def _unconfigured(desk: Any, settings: Any) -> tuple[Any, dict[str, Any]]:
    settings.ANGEE_EMAIL_DELIVERY_CONFIGURED = False
    return desk.owner, {"parent": _receive(desk, "unconfigured").public_id, "text": "Which team?"}


def _not_connected(desk: Any, settings: Any) -> tuple[Any, dict[str, Any]]:
    manual = make_integration("compose-graphql-manual", model=Channel, backend_class="manual")
    return manual.owner, _new(manual)


def _invalid_sender(desk: Any, settings: Any) -> tuple[Any, dict[str, Any]]:
    channel = make_integration("compose-graphql-sender", model=Channel, backend_class="email",
                               config={"from_email": "Desk <not an address>"})
    return channel.owner, _new(channel)


def _reply_to_without_address(desk: Any, settings: Any) -> tuple[Any, dict[str, Any]]:
    relayed = _receive(desk, "relayed", sender="noreply@forms.example.test", reply_to="Applicant <applicant@intranet>")
    return desk.owner, {"parent": relayed.public_id, "text": "Thanks."}


def _unaddressed(desk: Any, settings: Any) -> tuple[Any, dict[str, Any]]:
    return desk.owner, {"parent": _receive(desk, "unaddressed", sender=None).public_id, "text": "Thanks."}


def _threadless(desk: Any, settings: Any) -> tuple[Any, dict[str, Any]]:
    with system_context(reason="tests.messaging.compose.graphql.threadless"):
        parent = Message._base_manager.create(channel=desk, platform="email", message_type="email")
    return desk.owner, {"parent": parent.public_id, "text": "Thanks."}


def _discarded(desk: Any, settings: Any) -> tuple[Any, dict[str, Any]]:
    values = _new(desk)
    original = Message.objects.compose(channel=desk, to=values["to"], text=values["text"], actor=desk.owner,
                                       idempotency_key="present-state")
    with system_context(reason="tests.messaging.compose.graphql.discard"):
        Message._base_manager.filter(pk=original.pk).update(is_trashed=True)
    return desk.owner, values


@pytest.mark.parametrize(
    ("world", "key", "code"),
    [
        pytest.param(_unconfigured, "channel", "OUTBOUND_EMAIL_NOT_CONFIGURED", id="delivery-not-configured"),
        pytest.param(_not_connected, "channel", "OUTBOUND_EMAIL_CHANNEL_MISSING", id="no-email-channel"),
        pytest.param(_invalid_sender, "channel", None, id="invalid-channel-sender"),
        pytest.param(_reply_to_without_address, "replyTo", None, id="reply-to-names-no-address"),
        pytest.param(_unaddressed, "parent", None, id="unaddressed-parent"),
        pytest.param(_threadless, "parent", None, id="incomplete-parent"),
        pytest.param(_discarded, "idempotencyKey", None, id="discarded-original"),
    ],
)
def test_a_refusal_of_present_state_leaves_no_message_and_no_claim(
    console: Any, desk: Any, settings: Any, world: Any, key: str, code: str | None,
) -> None:
    """Present state is read after the claim; its refusal rolls the claim back with everything else."""

    actor, values = world(desk, settings)
    claims = changeset_model()._base_manager.filter(key="present-state").count()
    rows = _rows()
    with pytest.raises(_Refusal) as refused:
        _compose(console, actor, **values, idempotency_key="present-state")
    payload = refused.value.payload
    assert payload is not None and list(payload["validation_errors"]) == [key], payload
    assert payload["code"] == code
    assert _rows() == rows
    assert changeset_model()._base_manager.filter(key="present-state").count() == claims


def test_an_answer_naming_a_channel_is_refused_before_anything_is_read(console: Any, desk: Any) -> None:
    request = _receive(desk, "anchored")
    with CaptureQueriesContext(connection) as queries, pytest.raises(_Refusal) as refused:
        _compose(console, desk.owner, parent=request.public_id, channel=desk.public_id, text="Hi.",
                 idempotency_key="anchored")
    assert refused.value.payload is not None and list(refused.value.payload["validation_errors"]) == ["channel"]
    assert [query["sql"] for query in queries if not query["sql"].startswith(SAVEPOINTS)] == []


SEATS = {
    # seat: (answers a message, sends a new email, reads why delivery failed, the channel answers reply)
    # ``None`` reads nothing: neither the message nor the channel is the seat's to read.
    "owner": ("ok", "ok", True, True),
    "replier": ("ok", "ok", False, True),
    "reader": ("PERMISSION_DENIED", "PERMISSION_DENIED", False, False),
    "outsider": ("not_found", "PERMISSION_DENIED", None, None),
    "admin": ("ok", "ok", True, True),
    "anonymous": ("PERMISSION_DENIED", "PERMISSION_DENIED", None, None),
    "replier-with-an-unreadable-file": ("PERMISSION_DENIED", "PERMISSION_DENIED", False, True),
}


@pytest.mark.parametrize("seat", SEATS)
def test_who_may_compose_and_who_reads_why_delivery_failed(console: Any, desk: Any, seat: str) -> None:
    """An outsider answering finds no message in band; every other refusal is the schema's permission error."""

    if seat == "owner":
        actor = desk.owner
    elif seat == "admin":
        actor = create_platform_admin("compose-graphql-admin")
    elif seat != "anonymous":
        actor = make_user(f"compose-graphql-{seat}")
    else:
        actor = None
    relation = {"replier": "replier", "reader": "reader", "replier-with-an-unreadable-file": "replier"}.get(seat)
    if relation is not None:
        grant(desk, relation, actor)
    attachments = []
    if seat == "replier-with-an-unreadable-file":
        hidden = _file(make_user("compose-graphql-stranger"), b"Not yours")
        with system_context(reason="tests.messaging.compose.graphql.hidden"):
            File._base_manager.filter(pk=hidden.pk).update(visibility="record")
        attachments = [hidden.public_id]
    request = _receive(desk, f"seat-{seat}")
    answer, new, reads_error, replies = SEATS[seat]
    for anchor, expected, values in (
        ("answer", answer, {"parent": request.public_id, "text": "Which team?"}),
        ("new", new, _new(desk)),
    ):
        arguments = {**values, "attachments": attachments, "idempotency_key": f"seat-{seat}-{anchor}"}
        if expected == "ok":
            assert _compose(console, actor, **arguments)["id"]
            continue
        with _refused(_Refusal) as refused:
            _compose(console, actor, **arguments)
        assert (refused.value.code or refused.value.payload["code"]) == expected, (anchor, refused.value)
    message, channel = _delivery(console, actor, _failed(desk).public_id, desk)
    if reads_error is None:
        assert (message, channel) == (None, None)
        return
    assert (message["status"], message["delivery_attempts"]) == ("FAILED", 2)
    assert message["delivery_error"] == ("The email recipients were refused." if reads_error else None)
    assert channel["permissions"] == (["reply"] if replies else [])


def test_one_key_composes_one_message_and_other_content_under_it_conflicts(
    console: Any, desk: Any, django_capture_on_commit_callbacks: Any,
) -> None:
    values = _new(desk, idempotency_key="once")
    with django_capture_on_commit_callbacks(execute=True):
        first = _compose(console, desk.owner, **values)
        again = _compose(console, desk.owner, **values)
    assert again["id"] == first["id"]
    assert desk.sent == [DELIVER_MESSAGE_TASK]
    rows = _rows()
    with pytest.raises(_Refusal) as conflict:
        _compose(console, desk.owner, **{**values, "text": "Twice."})
    assert conflict.value.code == "CREATION_KEY_CONFLICT"
    assert _rows() == rows


def test_a_call_without_a_key_is_refused_before_it_runs(console: Any, desk: Any) -> None:
    document = 'mutation { compose_message(channel: "%s", to: ["a@example.test"], text: "Hi.", queued: true) { ok } }'
    with CaptureQueriesContext(connection) as queries:
        result = execute_schema(console, document % desk.public_id, user=desk.owner)
    assert result.errors and result.data is None
    assert len(queries) == 0


def test_every_status_a_replay_can_return_is_reported(console: Any, desk: Any) -> None:
    """A keyed replay returns its original as it is now; each delivery outcome reads distinctly."""

    values = _new(desk, queued=False, idempotency_key="replayed")
    first = _compose(console, desk.owner, **values)
    banners = {}
    for status in Message.MessageStatus.values:
        for scheduled_at in (None, timezone.now() + timedelta(hours=1)):
            with system_context(reason="tests.messaging.compose.graphql.settle"):
                Message._base_manager.filter(sqid=first["id"]).update(status=status, scheduled_at=scheduled_at)
            replay = _compose(console, desk.owner, **values)
            assert replay["id"] == first["id"] and replay["message"]
            banners[status, scheduled_at is not None] = replay["message"]
    outcomes = (
        ("draft", False), ("draft", True), ("queued", False), ("queued", True), ("sent", False), ("failed", False),
        ("uncertain", False),
    )
    assert len({banners[outcome] for outcome in outcomes}) == len(outcomes)


@pytest.fixture
def mailer(desk: Any, settings: Any, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The deliveries composing enqueues on commit, which Django's in-memory mailer sends when run."""

    settings.MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
    enqueued: list[dict[str, Any]] = []
    monkeypatch.setattr(celery_app, "send_task", lambda name, *, kwargs, **options: enqueued.append(kwargs))
    return enqueued


def test_a_delivered_email_reads_back_sent_and_its_replay_sends_nothing_more(
    console: Any, desk: Any, mailer: list[dict[str, Any]], django_capture_on_commit_callbacks: Any,
) -> None:
    values = _new(desk, subject="Invoice 42", idempotency_key="delivered")
    with django_capture_on_commit_callbacks(execute=True):
        composed = _compose(console, desk.owner, **values)
    (delivery,) = mailer
    assert run_message_delivery(**delivery)["delivered"] is True
    (sent,) = mail.outbox
    message = Message._base_manager.get(sqid=composed["id"])
    assert (sent.from_email, sent.to, sent.subject) == (DESK, ["Client <client@example.test>"], "Invoice 42")
    assert sent.message()["Message-ID"] == f"<{message.external_id}>"
    row, _channel = _delivery(console, desk.owner, composed["id"], desk)
    assert (row["status"], row["delivery_error"], row["delivery_attempts"]) == ("SENT", None, 1)
    with django_capture_on_commit_callbacks(execute=True):
        replay = _compose(console, desk.owner, **values)
    assert replay["id"] == composed["id"] and replay["message"] != composed["message"]
    assert (len(mailer), len(mail.outbox)) == (1, 1)


@pytest.mark.parametrize(("code", "status"), [(550, "FAILED"), (421, "QUEUED")])
def test_a_refused_delivery_reads_back_why_and_how_often(
    console: Any, desk: Any, mailer: list[dict[str, Any]], django_capture_on_commit_callbacks: Any,
    monkeypatch: pytest.MonkeyPatch, code: int, status: str,
) -> None:
    """A definite refusal fails, a temporary one is retried later, and each reads back its settlement."""

    transport = Mock()
    transport.send_messages.side_effect = SMTPRecipientsRefused({"client@example.test": (code, b"Refused.")})
    monkeypatch.setattr("angee.messaging.email.mailers", SimpleNamespace(default=transport))
    with django_capture_on_commit_callbacks(execute=True):
        composed = _compose(console, desk.owner, **_new(desk, idempotency_key=f"refused-{code}"))
    (delivery,) = mailer
    run_message_delivery(**delivery)
    stored = Message._base_manager.get(sqid=composed["id"]).local_metadata["delivery_error"]
    row, _channel = _delivery(console, desk.owner, composed["id"], desk)
    assert (row["status"], row["delivery_attempts"]) == (status, 1)
    assert stored and row["delivery_error"] == stored
    assert (row["scheduled_at"] is not None) is (status == "QUEUED")


ROW = "id status is_held scheduled_at"
FACTS = "delivery_error delivery_attempts"
READS = {
    "thread_message_feed": (
        "query Feed($thread: ID!) { thread_message_feed(thread_id: $thread, limit: 50) { messages { %s } } }",
        lambda data: data["thread_message_feed"]["messages"],
    ),
    "thread_message_feed_revalidate": (
        "query Revalidate($thread: ID!, $ids: [ID!]!) {"
        " thread_message_feed_revalidate(thread_id: $thread, ids: $ids) { messages { %s } absent_ids } }",
        lambda data: data["thread_message_feed_revalidate"]["messages"],
    ),
    "messages": (
        "query Messages($thread: String!) { messages(where: {thread: {_eq: $thread}}) { %s } }",
        lambda data: data["messages"],
    ),
}
SELECTIONS = {
    "row": ROW,
    "facts": f"{ROW} {FACTS}",
    "actions": f"{ROW} delivery_actions",
    "actions and facts": f"{ROW} delivery_actions {FACTS}",
}
"""What a list selects of each message: the delivery facts alone, beside ``delivery_actions``, or neither."""
READ_BUDGETS = {
    "thread_message_feed": {"statements": 33, "repeated": 25},
    "thread_message_feed_revalidate": {"statements": 21, "repeated": 16},
    "messages": {"statements": 12, "repeated": 8},
    "channels": {"statements": 6, "repeated": 3},
}
"""Each read's statements and repeated texts with the delivery facts (``channels``: with ``permissions``), measured.

The same for the channel's owner and a replier, at either size. Two of them are
REBAC's per-request statements deciding which grants the viewer can hold, which
a permission answer pays once (``send_held`` here, ``reply`` on channels).
"""


def _conversation(desk: Any, rows: int) -> dict[str, Any]:
    """A conversation of ``rows`` messages on ``desk``: one received and its answers, every other one failed."""

    with system_context(reason="tests.messaging.compose.graphql.conversation"):
        (request,) = Message.objects.ingest([ParsedMessage(
            external_id=f"<conversation-{rows}@example.test>", platform="email", subject=f"Conversation {rows}",
            sender=ParsedHandle(platform="email", value="requester@example.test"), body=ParsedPart(text="Hello."),
        )], channel=desk)
    answers = [Message.objects.compose(parent=request, text=f"Answer {index}.", actor=desk.owner)
               for index in range(rows - 1)]
    with system_context(reason="tests.messaging.compose.graphql.conversation"):
        Message._base_manager.filter(pk__in=[answer.pk for answer in answers[::2]]).update(
            status="failed", metadata={"local": {"delivery_error": "Refused.", "delivery_attempts": 2}},
        )
        thread = Thread._base_manager.get(pk=request.thread_id).public_id
    return {"thread": thread, "ids": [request.public_id, *(answer.public_id for answer in answers)]}


def test_the_delivery_facts_are_read_with_the_rows_of_every_list_the_composer_reads(console: Any, desk: Any) -> None:
    """A feed page, its revalidation and the message list read the facts with their rows, at 3 rows as at 6.

    ``delivery_attempts`` is a column of the page's own statement. ``delivery_error``
    shares ``delivery_actions``' answer to ``send_held``, so beside it the facts
    add nothing, and alone they cost exactly that answer: REBAC's statements
    deciding which grants the viewer can hold, once per request.
    """

    replier = make_user("compose-graphql-list-replier")
    grant(desk, "replier", replier)
    conversations = {rows: _conversation(desk, rows) for rows in (3, 6)}
    for seat, actor in (("owner", desk.owner), ("replier", replier)):
        for name, (document, rows_of) in READS.items():
            counts = {}
            for rows, variables in conversations.items():
                for selection, fields in SELECTIONS.items():
                    query = document % fields
                    arguments = variables if "$ids" in query else {"thread": variables["thread"]}
                    measure_graphql_document(console, query, arguments, actor=actor)
                    read = measure_graphql_document(console, query, arguments, actor=actor)
                    assert len(rows_of(read.data)) == rows
                    counts[selection, rows] = (read.statements, read.repeated)
                    if selection == "actions and facts":
                        read.assert_ceiling(**READ_BUDGETS[name])
            for selection in SELECTIONS:
                assert counts[selection, 3] == counts[selection, 6], (name, seat, selection, counts)
            assert counts["facts", 6] == counts["actions", 6] == counts["actions and facts", 6], (name, seat, counts)


def test_whether_one_may_send_on_a_channel_is_read_with_the_channels(console: Any, db: None) -> None:
    """``permissions`` is answered in the channels' own statement, for 1 channel as for 4."""

    counts = {}
    for size in (1, 4):
        replier = make_user(f"compose-graphql-channels-{size}")
        for index in range(size):
            grant(make_integration(f"compose-graphql-{size}-{index}", model=Channel, backend_class="email"),
                  "replier", replier)
        for fields in ("id", "id permissions"):
            document = "query Channels { channels { %s } }" % fields
            measure_graphql_document(console, document, {}, actor=replier)
            read = measure_graphql_document(console, document, {}, actor=replier)
            assert len(read.data["channels"]) == size
            assert all(row.get("permissions", ["reply"]) == ["reply"] for row in read.data["channels"])
            counts[size, fields] = (read.statements, read.repeated)
        read.assert_ceiling(**READ_BUDGETS["channels"])
    assert counts[1, "id permissions"] == counts[4, "id permissions"], counts
    assert counts[1, "id"] == counts[4, "id"], counts


def test_the_mutation_executes_what_compose_executes(console: Any, desk: Any) -> None:
    """The resolver decodes and dispatches: a new email costs compose's statements, at 1 recipient as at 20.

    The direct call runs in an evaluator scope, as every GraphQL operation does,
    so both decide the viewer's grants alike; compose's own test bounds what a
    recipient costs.
    """

    known = [f"known-{index}@example.test" for index in range(20)]
    Message.objects.compose(channel=desk, to=known, text="Hi.", actor=desk.owner, idempotency_key="warm")
    added = {}
    for size in (1, 20):
        values = {"to": known[:size], "text": "Hi."}
        with evaluator_scope(), CaptureQueriesContext(connection) as direct:
            Message.objects.compose(channel=desk, **values, actor=desk.owner, idempotency_key=f"direct-{size}")
        with CaptureQueriesContext(connection) as mutation:
            _compose(console, desk.owner, channel=desk.public_id, **values, queued=False,
                     idempotency_key=f"mutation-{size}")
        added[size] = len(mutation) - len(direct)
    assert added == {1: 0, 20: 0}, added
