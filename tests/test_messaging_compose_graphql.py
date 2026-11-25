"""The console composes a message through its one owner and reads back what delivery made of it.

``compose``'s own refusals, envelope and seats are proved at their owner
(``tests/test_messaging_compose.py``) and its permissions in messaging's decision
table; this module proves what the console adds: the arguments that reach
``compose`` and the keys its refusals bind to, the resolver's own gates, the
delivery facts and the cost of reading them.
"""

from __future__ import annotations

from datetime import timedelta
from smtplib import SMTPRecipientsRefused
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from django.core import mail
from django.db import connection, reset_queries
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rebac import evaluator_scope, system_context

from angee.graphql.testing.documents import GraphQLDocumentRead, execute_schema, measure_graphql_document, result_data
from angee.jobs.enqueue import celery_app
from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedPart
from angee.messaging.constants import DELIVER_MESSAGE_TASK
from angee.messaging.delivery import run_message_delivery
from angee.messaging.testing.models import Channel, Message, Part, Recipient, Thread
from tests.conftest import make_integration
from tests.messaging_campaign import grant, make_user
from tests.test_messaging_compose import DESK, _file, _receive, _rows
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
  messages_by_pk(id: $message) {
    id status is_held scheduled_at delivery_error delivery_attempts delivery_actions
  }
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


def _settle(message: Any, **local: Any) -> None:
    """Record ``local`` as the failed delivery's facts on ``message``, as settlement stores them."""

    with system_context(reason="tests.messaging.compose.graphql.settled"):
        Message._base_manager.filter(pk=message.pk).update(status="failed", metadata={"local": local})


def _statements(queries: CaptureQueriesContext) -> list[str]:
    return [query["sql"] for query in queries if not query["sql"].startswith(SAVEPOINTS)]


def test_an_answer_is_composed_on_the_parent_it_names(console: Any, desk: Any) -> None:
    request = _receive(desk, "answered")
    composed = _compose(console, desk.owner, parent=request.public_id, text="Which team?", idempotency_key="answer")
    message = Message._base_manager.get(sqid=composed["id"])
    assert (message.parent_id, message.thread_id, message.status) == (request.pk, request.thread_id, "queued")


def test_a_new_email_carries_each_argument_to_compose(console: Any, desk: Any) -> None:
    attachment = _file(desk.owner, b"Invoice 42: 100.00")
    later = timezone.now() + timedelta(hours=1)
    composed = _compose(
        console, desk.owner, **_new(desk, cc=["copy@example.test"], bcc=["blind@example.test"]),
        subject="Invoice 42", attachments=[attachment.public_id], queued=False, scheduled_at=later.isoformat(),
        idempotency_key="new",
    )
    message = Message._base_manager.get(sqid=composed["id"])
    with system_context(reason="tests.messaging.compose.graphql.new"):
        roles = sorted(Recipient._base_manager.filter(message=message).values_list("role", "handle__value"))
        attached = list(Part._base_manager.filter(message=message, file__isnull=False).values_list("file", flat=True))
    assert roles == [("bcc", "blind@example.test"), ("cc", "copy@example.test"), ("to", "client@example.test")]
    assert (message.title(), attached) == ("Invoice 42", [attachment.pk])
    row, _channel = _delivery(console, desk.owner, composed["id"], desk)
    assert (row["status"], row["scheduled_at"]) == ("DRAFT", later.isoformat())


@pytest.mark.parametrize(
    ("arguments", "key"),
    [
        pytest.param({"channel": None}, "channel", id="channel"),
        pytest.param({"text": "  "}, "text", id="text"),
        pytest.param({"subject": "One\nTwo"}, "subject", id="subject"),
        pytest.param({"to": ["not an address"]}, "to", id="to"),
        pytest.param({"to": [], "cc": ["Reader <reader@example>"]}, "cc", id="cc"),
        pytest.param({"to": [], "bcc": ["x" * 320 + "@example.test"]}, "bcc", id="bcc"),
        pytest.param({"queued": True, "scheduled_at": "later"}, "scheduledAt", id="scheduled-at"),
        pytest.param({"idempotency_key": " "}, "idempotencyKey", id="idempotency-key"),
    ],
)
def test_compose_refuses_an_argument_under_its_name(
    console: Any, desk: Any, arguments: dict[str, Any], key: str,
) -> None:
    """The mutation's arguments are compose's names, so each refusal binds to the argument it is about."""

    if arguments.get("scheduled_at") == "later":
        arguments = {**arguments, "scheduled_at": (timezone.now() + timedelta(hours=1)).isoformat()}
    with pytest.raises(_Refusal) as refused:
        _compose(console, desk.owner, **{**_new(desk), "idempotency_key": f"refused-{key}", **arguments})
    assert refused.value.payload is not None and list(refused.value.payload["validation_errors"]) == [key]


def test_a_refusal_with_a_code_reaches_the_client_with_it(console: Any, desk: Any, settings: Any) -> None:
    request = _receive(desk, "unconfigured")
    settings.ANGEE_EMAIL_DELIVERY_CONFIGURED = False
    with pytest.raises(_Refusal) as refused:
        _compose(console, desk.owner, parent=request.public_id, text="Which team?", idempotency_key="unconfigured")
    payload = refused.value.payload
    assert payload is not None and list(payload["validation_errors"]) == ["channel"]
    assert payload["code"] == "OUTBOUND_EMAIL_NOT_CONFIGURED"


def test_the_key_and_queued_are_required(console: Any, desk: Any) -> None:
    """A complete call composes; one without either required argument is refused before anything runs."""

    document = 'mutation { compose_message(channel: "%s", to: ["a@example.test"], text: "Hi."%s%s) { ok id } }'
    key, queued = ', idempotency_key: "required"', ", queued: false"
    assert result_data(execute_schema(console, document % (desk.public_id, key, queued), user=desk.owner))[
        "compose_message"]["ok"]
    for missing in ((desk.public_id, "", queued), (desk.public_id, key, "")):
        with CaptureQueriesContext(connection) as queries:
            result = execute_schema(console, document % missing, user=desk.owner)
        assert result.errors and result.data is None, missing
        assert len(queries) == 0


def test_a_caller_without_a_session_is_refused_before_anything_is_read(console: Any, desk: Any) -> None:
    request = _receive(desk, "anonymous")
    for values in ({"parent": request.public_id, "text": "Which team?"}, _new(desk)):
        with CaptureQueriesContext(connection) as queries, pytest.raises(_Refusal) as refused:
            _compose(console, None, **values, idempotency_key="anonymous")
        assert refused.value.code == "PERMISSION_DENIED"
        assert _statements(queries) == []


def test_a_parent_the_caller_cannot_read_is_not_found_whether_unreadable_missing_or_malformed(
    console: Any, desk: Any,
) -> None:
    """The resolver reads the parent under the caller's read, and every way of missing it answers alike."""

    request = _receive(desk, "hidden")
    for actor, parent in (
        (make_user("compose-graphql-outsider"), request.public_id),
        (desk.owner, Message.public_id_from_pk(10**9)),
        (desk.owner, "msg_not-a-public-id"),
    ):
        with pytest.raises(_Refusal) as refused:
            _compose(console, actor, parent=parent, text="Which team?", idempotency_key="hidden")
        assert refused.value.payload is not None, parent
        assert (refused.value.payload["code"], refused.value.payload["validation_errors"]) == ("not_found", None)


def test_an_answer_naming_a_channel_is_refused_before_anything_is_read(console: Any, desk: Any) -> None:
    request = _receive(desk, "anchored")
    with CaptureQueriesContext(connection) as queries, pytest.raises(_Refusal) as refused:
        _compose(console, desk.owner, parent=request.public_id, channel=desk.public_id, text="Hi.",
                 idempotency_key="anchored")
    assert refused.value.payload is not None and list(refused.value.payload["validation_errors"]) == ["channel"]
    assert _statements(queries) == []


def test_why_delivery_failed_is_read_by_who_may_act_on_it_and_not_by_its_author(console: Any, desk: Any) -> None:
    author, other = make_user("compose-graphql-author"), make_user("compose-graphql-replier")
    for replier in (author, other):
        grant(desk, "replier", replier)
    composed = _compose(console, author, **_new(desk), idempotency_key="authored")
    _settle(Message._base_manager.get(sqid=composed["id"]), delivery_error="Refused.", delivery_attempts=2)
    read = {seat: _delivery(console, actor, composed["id"], desk)[0]
            for seat, actor in (("owner", desk.owner), ("replier", other), ("author", author))}
    assert {seat: (row["delivery_error"], row["delivery_attempts"]) for seat, row in read.items()} == {
        "owner": ("Refused.", 2), "replier": (None, 2), "author": (None, 2),
    }


def test_whether_one_may_send_on_a_channel_is_answered_to_each_viewer(console: Any, desk: Any) -> None:
    replier, reader = make_user("compose-graphql-sender"), make_user("compose-graphql-reader")
    grant(desk, "replier", replier)
    grant(desk, "reader", reader)
    message = _receive(desk, "permissions")
    answers = {seat: _delivery(console, actor, message.public_id, desk)[1]["permissions"]
               for seat, actor in (("replier", replier), ("reader", reader))}
    assert answers == {"replier": ["reply"], "reader": []}


@pytest.mark.parametrize(
    ("local", "read"),
    [
        pytest.param({"delivery_error": "null", "delivery_conflict": "0"}, ("null", []), id="json-looking-texts"),
        pytest.param({"delivery_error": "Refused.", "delivery_conflict": False}, ("Refused.", ["retry"]),
                     id="a-false-conflict"),
    ],
)
def test_a_stored_fact_reads_back_with_its_stored_type(
    console: Any, desk: Any, local: dict[str, Any], read: tuple[str, list[str]],
) -> None:
    """An error ``"null"`` reads as that text; a conflict ``"0"`` blocks a retry, and a ``false`` one does not.

    The row's annotation is read on a list and by primary key, and agrees with
    the stored facts as the delivery verbs read them.
    """

    message = Message.objects.compose(channel=desk, to=["client@example.test"], text="Hi.", actor=desk.owner)
    _settle(message, delivery_attempts=1, **local)
    by_pk, _channel = _delivery(console, desk.owner, message.public_id, desk)
    thread = Thread._base_manager.get(pk=message.thread_id).public_id
    (listed,) = result_data(execute_schema(
        console, "query List($thread: String!) { messages(where: {thread: {_eq: $thread}}) {"
        " delivery_error delivery_actions } }", {"thread": thread}, user=desk.owner,
    ))["messages"]
    assert (by_pk["delivery_error"], by_pk["delivery_actions"]) == read
    assert (listed["delivery_error"], listed["delivery_actions"]) == read
    assert Message._base_manager.get(pk=message.pk).delivery_blocked is (not read[1])


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


def test_every_status_a_replay_can_return_is_reported(console: Any, desk: Any) -> None:
    """A keyed replay returns its original as it is now, whatever its delivery became."""

    values = _new(desk, queued=False, idempotency_key="replayed")
    first = _compose(console, desk.owner, **values)
    for status in Message.MessageStatus.values:
        for scheduled_at in (None, timezone.now() + timedelta(hours=1)):
            with system_context(reason="tests.messaging.compose.graphql.settle"):
                Message._base_manager.filter(sqid=first["id"]).update(status=status, scheduled_at=scheduled_at)
            replay = _compose(console, desk.owner, **values)
            assert replay["id"] == first["id"] and replay["message"], (status, scheduled_at)
            row, _channel = _delivery(console, desk.owner, replay["id"], desk)
            assert row["status"] == status.upper()


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
    assert replay["id"] == composed["id"]
    assert _delivery(console, desk.owner, replay["id"], desk)[0]["status"] == "SENT"
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


def _measured(console: Any, document: str, variables: dict[str, Any], actor: Any) -> GraphQLDocumentRead:
    """``document``'s second execution as ``actor``, measured on an emptied query log.

    Django keeps 9,000 captured statements per connection and then counts none,
    so the log is emptied first and a read that counted nothing fails.
    """

    measure_graphql_document(console, document, variables, actor=actor)
    reset_queries()
    read = measure_graphql_document(console, document, variables, actor=actor)
    assert read.statements, "the query log counted no statement"
    return read


SIZES = (1, 25)
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
"""Each read's statements and repeated texts with the delivery facts (``channels``: with ``permissions``).

Measured for the channel's owner and for a replier, the same at 1 row and at 25.
Two of them are REBAC's per-request statements deciding which grants the viewer
can hold, which a permission answer pays once (``send_held`` on messages,
``reply`` on channels; the owner of the channels pays none there, 3 statements in
all). An administrator pays more on every read, and three statements, not two,
for a permission answer: 37, 25, 15 and 8 statements.
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
    for answer in answers[::2]:
        _settle(answer, delivery_error="Refused.", delivery_attempts=2)
    thread = Thread._base_manager.get(pk=request.thread_id).public_id
    return {"thread": thread, "ids": [request.public_id, *(answer.public_id for answer in answers)]}


def test_the_delivery_facts_are_read_with_the_rows_of_every_list_the_composer_reads(console: Any, desk: Any) -> None:
    """A feed page, its revalidation and the message list read the facts with their rows, at 1 row as at 25.

    ``delivery_attempts`` is a column of the page's own statement. ``delivery_error``
    shares ``delivery_actions``' answer to ``send_held``, so beside it the facts
    add nothing, and alone they cost exactly that answer.
    """

    replier = make_user("compose-graphql-list-replier")
    grant(desk, "replier", replier)
    conversations = {rows: _conversation(desk, rows) for rows in SIZES}
    for seat, actor in (("owner", desk.owner), ("replier", replier)):
        for name, (document, rows_of) in READS.items():
            counts = {}
            for rows, variables in conversations.items():
                for selection, fields in SELECTIONS.items():
                    query = document % fields
                    arguments = variables if "$ids" in query else {"thread": variables["thread"]}
                    read = _measured(console, query, arguments, actor)
                    assert len(rows_of(read.data)) == rows
                    counts[selection, rows] = (read.statements, read.repeated)
                    if selection == "actions and facts":
                        read.assert_ceiling(**READ_BUDGETS[name])
            for selection in SELECTIONS:
                assert counts[selection, 1] == counts[selection, 25], (name, seat, selection, counts)
            assert counts["facts", 25] == counts["actions", 25] == counts["actions and facts", 25], (
                name, seat, counts,
            )


def test_whether_one_may_send_is_read_with_the_channels(console: Any, db: None) -> None:
    """``permissions`` is answered in the channels' own statement, for 1 channel as for 25."""

    for seat in ("owner", "replier"):
        counts = {}
        for size in SIZES:
            viewer = make_user(f"compose-graphql-channels-{seat}-{size}")
            for index in range(size):
                slug = f"compose-graphql-{seat}-{size}-{index}"
                if seat == "owner":
                    make_integration(slug, model=Channel, backend_class="email", owner=viewer)
                else:
                    grant(make_integration(slug, model=Channel, backend_class="email"), "replier", viewer)
            for fields in ("id", "id permissions"):
                read = _measured(console, "query Channels { channels { %s } }" % fields, {}, viewer)
                assert len(read.data["channels"]) == size
                assert all(row.get("permissions", ["reply"]) == ["reply"] for row in read.data["channels"])
                counts[size, fields] = (read.statements, read.repeated)
            read.assert_ceiling(**READ_BUDGETS["channels"])
        assert counts[1, "id permissions"] == counts[25, "id permissions"], (seat, counts)
        assert counts[1, "id"] == counts[25, "id"], (seat, counts)


PARENT_READ_STATEMENTS = 6
"""What the resolver adds to answer a message: reading the parent under the caller's read, measured."""


def test_the_mutation_executes_what_compose_executes(console: Any, desk: Any) -> None:
    """A new email costs compose's statements at 1 recipient as at 20; an answer adds only its parent's read.

    The direct call runs in an evaluator scope, as every GraphQL operation does,
    so both decide the viewer's grants alike; compose's own test bounds what a
    recipient costs.
    """

    known = [f"known-{index}@example.test" for index in range(20)]
    request = _receive(desk, "measured")
    Message.objects.compose(channel=desk, to=known, text="Hi.", actor=desk.owner, idempotency_key="warm")
    Message.objects.compose(parent=request, text="Hi.", actor=desk.owner, idempotency_key="warm-answer")
    cases = {
        **{size: ({"channel": desk}, {"channel": desk.public_id}, {"to": known[:size]}) for size in (1, 20)},
        "answer": ({"parent": request}, {"parent": request.public_id}, {}),
    }
    added = {}
    for case, (direct_anchor, mutation_anchor, values) in cases.items():
        reset_queries()
        with evaluator_scope(), CaptureQueriesContext(connection) as direct:
            Message.objects.compose(**direct_anchor, **values, text="Hi.", actor=desk.owner,
                                    idempotency_key=f"direct-{case}")
        with CaptureQueriesContext(connection) as mutation:
            _compose(console, desk.owner, **mutation_anchor, **values, text="Hi.", queued=False,
                     idempotency_key=f"mutation-{case}")
        assert len(direct) and len(mutation), case
        added[case] = len(mutation) - len(direct)
    assert added == {1: 0, 20: 0, "answer": PARENT_READ_STATEMENTS}, added
