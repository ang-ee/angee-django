"""One owner writes an outgoing message, and the recipient model keeps its own thread."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext, override_settings
from django.utils import timezone
from hypothesis import given
from hypothesis import strategies as st
from rebac import PermissionDenied, system_context

from angee.jobs.enqueue import celery_app
from angee.messaging.backends import (
    OutboundEmailChannelMissing,
    OutboundEmailNotConfigured,
    ParsedHandle,
    ParsedMessage,
    ParsedPart,
    ParsedRecipient,
    ParsedThread,
)
from angee.messaging.constants import DELIVER_MESSAGE_TASK
from angee.messaging.managers import MessageManager, _preview
from angee.messaging.testing.models import Channel, Handle, Message, Part, Recipient, Thread
from angee.provenance import context as provenance_context
from angee.provenance.context import changeset, changeset_model
from angee.provenance.contrib.angee.exceptions import CreationKeyConflict
from tests.chatterdemo.models import ChatterDoc
from tests.conftest import File, create_platform_admin, make_integration
from tests.messaging_campaign import grant, make_user
from tests.test_messaging import _storage_drive

DESK = "Desk <desk@example.test>"


@pytest.fixture
def desk(db: None, settings: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """An email channel whose default sender is the desk, delivery configured, sends recorded and never made."""

    del db
    settings.ANGEE_EMAIL_DELIVERY_CONFIGURED = True
    sent: list[str] = []
    monkeypatch.setattr(celery_app, "send_task", lambda name, **options: sent.append(name))
    channel = make_integration("compose-desk", model=Channel, backend_class="email", config={"from_email": DESK})
    with system_context(reason="tests.messaging.compose.drive"):
        _storage_drive(tmp_path, owner=channel.owner)
    channel.sent = sent
    return channel


def _receive(channel: Any, key: str, *, sender: str | None = "requester@example.test", reply_to: str = "",
             platform: str = "email", thread: ParsedThread | None = None) -> Any:
    """Land one inbound message on ``channel`` through messaging's ingest."""

    with system_context(reason="tests.messaging.compose.receive"):
        (message,) = Message.objects.ingest(
            [
                ParsedMessage(
                    external_id=f"<{key}@example.test>" if platform == "email" else key,
                    platform=platform,
                    subject="Access request",
                    headers=(("Reply-To", reply_to),) if reply_to else (),
                    sender=ParsedHandle(platform=platform, value=sender, display_name="Requester") if sender else None,
                    thread=thread,
                    body=ParsedPart(text="Please add me."),
                )
            ],
            channel=channel,
        )
    return message


def _file(owner: Any, content: bytes, name: str = "invoice.txt") -> Any:
    with system_context(reason="tests.messaging.compose.file"):
        file, _created = File.objects.ingest_bytes(content, filename=name, owner_id=owner.pk)
    return file


def _rows() -> tuple[int, int, int]:
    return Message._base_manager.count(), Part._base_manager.count(), Recipient._base_manager.count()


@contextmanager
def _refused(
    expected: type[Exception] = ValidationError, *, key: str | None = None, code: str | None = None,
) -> Iterator[pytest.ExceptionInfo[Any]]:
    """Expect the call inside to be refused having written nothing, judged by what it executed.

    A write the refusal's rollback undoes leaves no row behind, so the statements
    the call ran are read instead: none may insert, update or delete anything but
    a permission audit row, and none may touch a changeset. ``key`` and ``code``
    name the refusal of a ``ValidationError``.
    """

    audit, changesets = "rebac_permissionauditevent", changeset_model()._meta.db_table
    with CaptureQueriesContext(connection) as queries:
        with pytest.raises(expected) as refused:
            yield refused
    writes = [
        query["sql"] for query in queries
        if changesets in query["sql"]
        or (query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) and audit not in query["sql"])
    ]
    assert writes == []
    if key is not None:
        assert isinstance(refused.value, ValidationError)
        assert key in refused.value.error_dict, refused.value
        if code is not None:
            assert refused.value.error_dict[key][0].code == code


def _wire(channel: Any, message: Any) -> Any:
    with system_context(reason="tests.messaging.compose.render"):
        return channel.backend.email_message(message).message()


def _assert_recipients_on_their_messages_threads() -> None:
    rows = Recipient._base_manager.values_list("thread_id", "message__thread_id")
    assert rows.exists()
    assert all(thread_id == message_thread_id for thread_id, message_thread_id in rows)


def test_a_new_email_on_a_record_lands_on_its_chatter_and_renders_its_envelope(desk: Any) -> None:
    owner = desk.owner
    with system_context(reason="tests.messaging.compose.record"):
        doc = ChatterDoc.objects.create(title="Invoice 42")
    grant(doc, "manager", owner)
    attachment = _file(owner, b"Invoice 42: 100.00")
    message = Message.objects.compose(
        channel=desk, record=doc, to=["Client <client@example.test>"], subject="Invoice 42",
        text="Please find the invoice attached.", attachments=[attachment], actor=owner, queued=True,
    )
    assert message.actor() is not None
    with system_context(reason="tests.messaging.compose.chatter"):
        assert message.thread_id == doc.message_thread(create=False).pk
    assert (message.message_type, message.status, message.direction) == ("email", "queued", "outbound")
    assert message.preview == "Please find the invoice attached."
    wire = _wire(desk, message)
    assert (wire["From"], wire["To"], wire["Subject"]) == (DESK, "Client <client@example.test>", "Invoice 42")
    assert wire["Message-ID"] == f"<{message.external_id}>"
    assert [part.get_filename() for part in wire.walk() if part.get_content_disposition() == "attachment"] == [
        "invoice.txt",
    ]
    _assert_recipients_on_their_messages_threads()


def test_a_new_email_on_a_channel_starts_its_own_thread_titled_by_its_subject(desk: Any) -> None:
    owner = desk.owner
    first, second = (
        Message.objects.compose(channel=desk, to=[address], subject=subject, text="The report.", actor=owner)
        for address, subject in (("a@example.test", "Re: Quarterly report"), ("b@example.test", "Quarterly report"))
    )
    assert first.thread_id != second.thread_id
    with system_context(reason="tests.messaging.compose.threads"):
        threads = Thread._base_manager.filter(pk__in=(first.thread_id, second.thread_id))
        assert {thread.display_title for thread in threads} == {"Quarterly report"}
        assert all(thread.external_id.startswith("msg:") and thread.channel_id == desk.pk for thread in threads)
    # The title part carries the subject as written; the thread is titled by its normalised form.
    assert (first.title(), second.title()) == ("Re: Quarterly report", "Quarterly report")
    assert desk.sent == []


def test_inbound_mail_joins_a_composed_thread_only_through_its_threading_headers(desk: Any) -> None:
    """A subject alone groups inbound mail with inbound mail, never into a conversation a person started."""

    alice, bob = (
        Message.objects.compose(channel=desk, to=[address], subject="Salary review", text="Your numbers.",
                                actor=desk.owner)
        for address in ("alice@example.test", "bob@example.test")
    )

    def receive(key: str, *, in_reply_to: str = "") -> Any:
        with system_context(reason="tests.messaging.compose.fuse"):
            (message,) = Message.objects.ingest([ParsedMessage(
                external_id=f"<{key}@example.test>", platform="email", subject="Re: Salary review",
                in_reply_to=in_reply_to, sender=ParsedHandle(platform="email", value=f"{key}@example.test"),
                body=ParsedPart(text=key),
            )], channel=desk)
        return message

    unrelated, again = receive("mallory"), receive("carol")
    answer = receive("alice", in_reply_to=alice.external_id)
    assert unrelated.thread_id not in (alice.thread_id, bob.thread_id)
    assert again.thread_id == unrelated.thread_id
    assert (answer.thread_id, answer.parent_id) == (alice.thread_id, alice.pk)


def test_the_text_part_comes_from_the_text_and_never_from_the_html(desk: Any) -> None:
    owner = desk.owner
    bodies = {
        "text": {"text": "Plain words."},
        "both": {"text": "Plain words.", "html": "<p>Rich <b>words</b>.</p>"},
        "html": {"html": "<style>p { color: red }</style><p>Rich words.</p>"},
    }
    types = {}
    for name, body in bodies.items():
        message = Message.objects.compose(channel=desk, to=["reader@example.test"], actor=owner, **body)
        types[name] = [part.get_content_type() for part in _wire(desk, message).walk()]
    assert types["text"] == ["text/plain"]
    assert types["both"] == ["multipart/alternative", "text/plain", "text/html"]
    assert "text/plain" not in types["html"] and "text/html" in types["html"]


@pytest.mark.parametrize(
    ("values", "key", "code"),
    [
        ({"local": {"delivery_attempts": 3}}, "local", "reserved"),
        ({"queued": True, "scheduled_at": timezone.now() + timedelta(hours=1)}, "scheduled_at", "queued"),
        ({"subject": "One\nTwo"}, "subject", "invalid"),
        ({"text": "  ", "html": ""}, "text", "required"),
        ({"to": ["not an address"]}, "to", "invalid"),
        ({"to": [], "cc": ["Reader <reader@example>"]}, "cc", "invalid"),
        ({"to": [], "bcc": ["x" * 320 + "@example.test"]}, "bcc", "invalid"),
        ({"to": []}, "to", "required"),
        ({"sender": "desk"}, "sender", "channel_sender"),
    ],
)
def test_a_new_message_is_refused_before_anything_is_written(
    desk: Any, values: dict[str, Any], key: str, code: str,
) -> None:
    with system_context(reason="tests.messaging.compose.sender"):
        handle = Handle.objects.upsert(platform="email", value="desk@example.test")
    values = {"text": "Hello.", "to": ["reader@example.test"], **values}
    if values.get("sender") == "desk":
        values["sender"] = handle
    with _refused(key=key, code=code):
        Message.objects.compose(channel=desk, actor=desk.owner, idempotency_key=f"refused-{key}", **values)


def test_a_refusal_that_followed_a_write_is_seen_in_what_the_call_executed(
    desk: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal check reads the statements: a late refusal is caught though its rollback leaves no row."""

    def refuse_late(*args: Any, **kwargs: Any) -> None:
        raise ValidationError({"to": ValidationError("Refused after the writes.", code="invalid")})

    monkeypatch.setattr(MessageManager, "_write_recipients", refuse_late)
    before = _rows()
    with pytest.raises(AssertionError), _refused(key="to", code="invalid"):
        Message.objects.compose(channel=desk, to=["reader@example.test"], text="Hi.", actor=desk.owner)
    assert _rows() == before


def test_an_anchor_is_refused_with_what_it_cannot_take(desk: Any) -> None:
    request = _receive(desk, "anchor")
    with system_context(reason="tests.messaging.compose.anchor"):
        doc = ChatterDoc.objects.create(title="Anchor")
        handle = Handle.objects.upsert(platform="email", value="desk@example.test")
    for values, key, code in (
        ({"parent": request, "channel": desk}, "channel", "reply_anchored"),
        ({"parent": request, "record": doc}, "record", "reply_anchored"),
        ({"record": doc, "to": ["reader@example.test"]}, "channel", "required"),
        ({"parent": request, "subject": "Other"}, "subject", "reply_titled"),
        ({"parent": request, "to": ["other@example.test"]}, "to", "reply_addressed"),
        ({"parent": request, "sender": handle}, "sender", "channel_sender"),
    ):
        with _refused(key=key, code=code):
            Message.objects.compose(text="Hello.", actor=desk.owner, **values)


def test_an_email_reply_on_a_channel_that_cannot_send_is_refused_even_held(desk: Any) -> None:
    request = _receive(desk, "unsendable")
    with override_settings(ANGEE_EMAIL_DELIVERY_CONFIGURED=False), _refused(
        OutboundEmailNotConfigured, key="channel", code="OUTBOUND_EMAIL_NOT_CONFIGURED",
    ):
        Message.objects.compose(parent=request, text="Which team?", actor=desk.owner)
    manual = make_integration("compose-manual", model=Channel, backend_class="manual")
    with _refused(OutboundEmailChannelMissing, key="channel", code="OUTBOUND_EMAIL_CHANNEL_MISSING"):
        Message.objects.compose(channel=manual, to=["reader@example.test"], text="Hello.", actor=manual.owner)


def test_a_keyed_retry_returns_its_original_after_the_channel_stops_sending(desk: Any) -> None:
    """Present state is read after the replay: a retry finds its message, a first call is refused unwritten."""

    owner = desk.owner
    request = _receive(desk, "retried")
    reply = Message.objects.compose(parent=request, text="Which team?", actor=owner, idempotency_key="retry-reply")
    new = Message.objects.compose(channel=desk, to=["reader@example.test"], text="Hello.", actor=owner,
                                  idempotency_key="retry-new")
    rows = _rows()
    with override_settings(ANGEE_EMAIL_DELIVERY_CONFIGURED=False):
        again = Message.objects.compose(parent=request, text="Which team?", actor=owner, idempotency_key="retry-reply")
        repeated = Message.objects.compose(channel=desk, to=["reader@example.test"], text="Hello.", actor=owner,
                                           idempotency_key="retry-new")
        with pytest.raises(OutboundEmailNotConfigured):
            Message.objects.compose(parent=request, text="Which team?", actor=owner, idempotency_key="first")
    assert (again.pk, repeated.pk, _rows()) == (reply.pk, new.pk, rows)
    assert not changeset_model()._base_manager.filter(key="first").exists()


def test_a_chat_reply_is_plain_text_from_an_explicit_sender_on_its_platform(desk: Any) -> None:
    chat = make_integration("compose-chat", model=Channel, backend_class="manual")
    question = _receive(chat, "room/1", sender="U-asker", platform="slack", thread=ParsedThread(external_id="room"))
    with system_context(reason="tests.messaging.compose.chat"):
        slack, email = (Handle.objects.upsert(platform=platform, value=value)
                        for platform, value in (("slack", "U-desk"), ("email", "desk@example.test")))
    attachment = _file(chat.owner, b"chat attachment")
    for values, key, code in (
        ({}, "sender", "invalid"), ({"sender": email}, "sender", "invalid"),
        ({"sender": slack, "html": "<p>Yes.</p>"}, "html", "plain_text"),
        ({"sender": slack, "attachments": [attachment]}, "attachments", "plain_text"),
    ):
        with _refused(key=key, code=code):
            Message.objects.compose(parent=question, text="Yes.", actor=chat.owner, **values)
    reply = Message.objects.compose(parent=question, text="Yes.", sender=slack, actor=chat.owner)
    assert (reply.message_type, reply.sender_id, reply.title()) == ("chat", slack.pk, "")
    assert not Recipient._base_manager.filter(message=reply).exists()


def test_every_writer_lands_an_email_address_on_one_handle(db: None, settings: Any) -> None:
    """The channel's sender, a typed recipient and ingest store an address in the handle owner's one form."""

    del db
    settings.ANGEE_EMAIL_DELIVERY_CONFIGURED = True
    channel = make_integration("compose-case", model=Channel, backend_class="email",
                               config={"from_email": "Desk <Desk@Example.TEST>"})
    with system_context(reason="tests.messaging.compose.case"):
        Message.objects.ingest([ParsedMessage(
            external_id="<case@example.test>", platform="email", subject="Hello",
            sender=ParsedHandle(platform="email", value=Handle.email_value(" Client@Example.TEST ")),
            recipients=(ParsedRecipient(handle=ParsedHandle(platform="email", value="desk@example.test")),),
            body=ParsedPart(text="Hello."),
        )], channel=channel)
    message = Message.objects.compose(channel=channel, to=["CLIENT@example.test"], text="Hi.", actor=channel.owner)
    with system_context(reason="tests.messaging.compose.case.read"):
        values = sorted(Handle._base_manager.filter(value__iendswith="@example.test").values_list("value", flat=True))
        recipient = Recipient._base_manager.get(message=message).handle_id
        assert (message.sender.value, values) == ("desk@example.test", ["client@example.test", "desk@example.test"])
        assert recipient == Handle._base_manager.get(value="client@example.test").pk


def test_a_typed_name_never_renames_a_known_handle_and_an_invalid_address_writes_no_handle(desk: Any) -> None:
    with system_context(reason="tests.messaging.compose.names"):
        known = Handle.objects.upsert(platform="email", value="known@example.test", display_name="Known")
        blank = Handle.objects.upsert(platform="email", value="blank@example.test")
    Message.objects.compose(
        channel=desk, to=["Other <Known@Example.test>", "New <new@example.test>"], cc=["Typed <blank@example.test>"],
        text="Hello.", actor=desk.owner,
    )
    names = dict(Handle._base_manager.filter(
        value__in=("known@example.test", "new@example.test", "blank@example.test"),
    ).values_list("value", "display_name"))
    assert names == {"known@example.test": "Known", "new@example.test": "New", "blank@example.test": "Typed"}
    assert Handle._base_manager.get(pk=known.pk).display_name == "Known"
    assert Handle._base_manager.get(pk=blank.pk).display_name == "Typed"
    with _refused(key="to", code="invalid"):
        Message.objects.compose(channel=desk, to=["fresh@example.test", "broken@"], text="Hello.", actor=desk.owner)


def test_a_keyed_compose_replays_its_message_and_refuses_changed_or_discarded_intent(desk: Any) -> None:
    owner = desk.owner
    values = {"channel": desk, "to": ["reader@example.test"], "subject": "Keyed", "text": "Once.", "actor": owner}
    first = Message.objects.compose(**values, idempotency_key="once")
    rows, count = _rows(), Thread._base_manager.get(pk=first.thread_id).message_count
    replay = Message.objects.compose(**values, idempotency_key="once", scheduled_at=timezone.now() + timedelta(days=1))
    assert replay.pk == first.pk
    assert (replay.status, replay.scheduled_at) == ("draft", None)
    assert _rows() == rows
    assert Thread._base_manager.get(pk=first.thread_id).message_count == count == 1
    with pytest.raises(CreationKeyConflict):
        Message.objects.compose(**{**values, "text": "Twice."}, idempotency_key="once")
    with system_context(reason="tests.messaging.compose.discard"):
        Message._base_manager.filter(pk=first.pk).update(is_trashed=True)
    with pytest.raises(ValidationError) as refused:
        Message.objects.compose(**values, idempotency_key="once")
    assert refused.value.error_dict["idempotency_key"][0].code == "discarded"
    assert _rows() == rows


def test_compose_refuses_an_elevated_call_and_runs_as_its_actor_without_a_process_changeset(
    desk: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No elevation is carried into compose; a request's changeset, with none process-wide, holds its rows."""

    owner = desk.owner
    for actor in (owner, make_user("compose-elevated-outsider")):
        with system_context(reason="tests.messaging.compose.elevated"), _refused(RuntimeError):
            Message.objects.compose(channel=desk, to=["reader@example.test"], text="Hi.", actor=actor,
                                    idempotency_key="elevated")
    # A request or a task opens its own changeset and publishes none process-wide.
    monkeypatch.setattr(provenance_context, "_process_boundary", None)
    with changeset(fresh=True, change_reason="request"):
        plain = Message.objects.compose(channel=desk, to=["fresh-a@example.test"], text="Hi.", actor=owner)
        keyed = Message.objects.compose(channel=desk, to=["fresh-b@example.test"], text="Hi.", actor=owner,
                                        idempotency_key="request-keyed")
    assert plain.pk != keyed.pk
    with system_context(reason="tests.messaging.compose.history"):
        recorded = Thread.history.filter(id=plain.thread_id).values_list("changeset_id", flat=True).first()
        reason = changeset_model()._base_manager.filter(pk=recorded).values_list("change_reason", flat=True).first()
    assert reason == "request"


def test_a_queued_message_is_handed_to_delivery_once_on_commit_and_a_held_one_waits(
    desk: Any, django_capture_on_commit_callbacks: Any,
) -> None:
    owner = desk.owner
    values = {"channel": desk, "to": ["reader@example.test"], "text": "Hello.", "actor": owner}
    with django_capture_on_commit_callbacks(execute=True):
        queued = Message.objects.compose(**values, queued=True)
    assert (queued.status, desk.sent) == ("queued", [DELIVER_MESSAGE_TASK])
    before = _rows()
    with django_capture_on_commit_callbacks(execute=True), pytest.raises(RuntimeError), transaction.atomic():
        Message.objects.compose(**values, queued=True)
        raise RuntimeError("the caller rolls back")
    assert (_rows(), desk.sent) == (before, [DELIVER_MESSAGE_TASK])
    with django_capture_on_commit_callbacks(execute=True):
        held = Message.objects.compose(**values)
        scheduled = Message.objects.compose(**values, scheduled_at=timezone.now() + timedelta(hours=1))
    assert (held.status, held.scheduled_at, scheduled.status) == ("draft", None, "draft")
    assert desk.sent == [DELIVER_MESSAGE_TASK]
    with system_context(reason="tests.messaging.compose.release"):
        assert Message.objects.release_held_messages(now=timezone.now()) == 0
        Message._base_manager.filter(pk=scheduled.pk).update(scheduled_at=timezone.now() - timedelta(seconds=1))
        with django_capture_on_commit_callbacks(execute=True):
            assert Message.objects.release_held_messages(now=timezone.now()) == 1
    assert desk.sent == [DELIVER_MESSAGE_TASK, DELIVER_MESSAGE_TASK]
    assert Message._base_manager.get(pk=held.pk).status == "draft"


SEATS = {
    # seat: (answers a message, sends on the channel, sends on the record)
    "owner": (True, True, True),
    "replier": (True, True, True),
    "reader": (False, False, False),
    "poster": (False, False, False),
    "replier-without-post": (True, True, False),
    "admin": (True, True, True),
    "outsider": (False, False, False),
    "none": (False, False, False),
}


@pytest.mark.parametrize("seat", SEATS)
@pytest.mark.parametrize("anchor", ["reply", "channel", "record"])
def test_the_channels_reply_and_the_records_post_decide_who_composes(desk: Any, seat: str, anchor: str) -> None:
    request = _receive(desk, f"seat-{seat}-{anchor}")
    with system_context(reason="tests.messaging.compose.seat"):
        doc = ChatterDoc.objects.create(title="Seat record")
    actor = (
        desk.owner if seat == "owner" else None if seat == "none"
        else create_platform_admin(f"compose-admin-{anchor}") if seat == "admin" else make_user(f"compose-{seat}")
    )
    grants = {
        "owner": ((doc, "manager"),),
        "replier": ((desk, "replier"), (doc, "manager")),
        "reader": ((desk, "reader"), (doc, "manager")),
        "poster": ((doc, "manager"),),
        "replier-without-post": ((desk, "replier"), (doc, "writer")),
    }
    for row, relation in grants.get(seat, ()):
        grant(row, relation, actor)
    values = {
        "reply": {"parent": request},
        "channel": {"channel": desk, "to": ["reader@example.test"]},
        "record": {"channel": desk, "record": doc, "to": ["reader@example.test"]},
    }[anchor]
    allowed = SEATS[seat][("reply", "channel", "record").index(anchor)]
    key = f"seat-{seat}-{anchor}"
    if allowed:
        message = Message.objects.compose(**values, text="Hello.", actor=actor, idempotency_key=key)
        assert message.direction == "outbound"
        return
    with _refused(PermissionDenied):
        Message.objects.compose(**values, text="Hello.", actor=actor, idempotency_key=key)


def test_an_attachment_the_actor_cannot_read_is_refused(desk: Any) -> None:
    stranger = make_user("compose-file-owner")
    hidden = _file(stranger, b"Not yours")
    with system_context(reason="tests.messaging.compose.hidden_file"):
        # The composer owns the drive, which lends its readers every inherited file.
        File._base_manager.filter(pk=hidden.pk).update(visibility="record")
    with _refused(PermissionDenied):
        Message.objects.compose(
            channel=desk, to=["reader@example.test"], text="Hello.", attachments=[hidden], actor=desk.owner,
        )


def test_compose_writes_each_recipient_on_its_messages_thread(desk: Any) -> None:
    request = _receive(desk, "rows", reply_to="Applicant <applicant@example.test>")
    Message.objects.compose(parent=request, text="Thanks.", actor=desk.owner)
    Message.objects.compose(channel=desk, to=["a@example.test"], cc=["b@example.test"], text="Hi.", actor=desk.owner)
    _assert_recipients_on_their_messages_threads()


def test_an_email_reply_goes_to_its_reply_to_else_its_sender_and_a_reply_to_without_an_address_is_refused(
    desk: Any,
) -> None:
    """A relay's sender is never answered in place of a Reply-To the handle owner cannot take."""

    owner = desk.owner
    rejected = ("Applicant <applicant@intranet>", "applicant@example.test.", '"a b"@example.test')
    for index, reply_to in enumerate(rejected):
        relayed = _receive(desk, f"relayed-{index}", sender="noreply@forms.example.test", reply_to=reply_to)
        assert relayed.reply_error(actor=owner).error_dict["reply_to"][0].code == "reply_to_invalid"
        with _refused(key="reply_to", code="reply_to_invalid"):
            Message.objects.compose(parent=relayed, text="Thanks.", actor=owner)
    answered = {
        "reply-to": _receive(desk, "relayed-ok", sender="relay@example.test", reply_to="Ann <Ann@Example.test>"),
        "sender": _receive(desk, "direct", sender="requester@example.test"),
    }
    replies = {name: Message.objects.compose(parent=parent, text="Thanks.", actor=owner)
               for name, parent in answered.items()}
    with system_context(reason="tests.messaging.compose.reply_to.read"):
        to = {name: list(Recipient._base_manager.filter(message=reply).values_list("handle__value", flat=True))
              for name, reply in replies.items()}
    assert to == {"reply-to": ["ann@example.test"], "sender": ["requester@example.test"]}
    unaddressed = _receive(desk, "unaddressed", sender=None)
    assert unaddressed.reply_error(actor=owner).error_dict["parent"][0].code == "reply_unaddressed"


@given(st.text())
def test_a_plain_body_previews_as_its_first_280_characters(text: str) -> None:
    """The chatter writers' previews did not change when they moved to the one preview rule."""

    assert _preview(ParsedPart(text=text)) == text[:280]


def test_a_recipient_row_is_inserted_on_its_messages_thread(db: None) -> None:
    """The model sets the row's thread from its message, refuses another, and loads no message it was given."""

    del db
    loaded = f'"{Message._meta.db_table}"."thread_id"'
    with system_context(reason="tests.messaging.recipient.thread"):
        thread, other = Thread._base_manager.create(), Thread._base_manager.create()
        message = Message._base_manager.create(thread=thread)
        handle = Handle._base_manager.create(platform="email", value="row@example.test")
        elsewhere = Handle._base_manager.create(platform="email", value="elsewhere@example.test")
        with CaptureQueriesContext(connection) as given:
            row = Recipient._base_manager.create(message=message, handle=handle, role="to")
        with CaptureQueriesContext(connection) as by_id:
            Recipient._base_manager.create(message_id=message.pk, handle=handle, role="cc")
        assert row.thread_id == thread.pk
        # REBAC probes the message's key for its relation either way; only a bare id loads the row.
        assert [query for query in given if loaded in query["sql"]] == []
        assert len([query for query in by_id if loaded in query["sql"]]) == 1
        same = Recipient._base_manager.create(message=message, thread=thread, handle=handle, role="bcc")
        assert same.thread_id == thread.pk
        with pytest.raises(ValueError):
            Recipient._base_manager.create(message=message, thread=other, handle=elsewhere, role="to")
    assert Recipient._base_manager.filter(message=message).count() == 3


def test_ingest_writes_each_recipient_on_its_messages_thread(db: None) -> None:
    """The ingest writer passes its message; every row it writes is on that message's thread."""

    del db
    channel: Any = make_integration("recipient-ingest", model=Channel, backend_class="manual")
    with system_context(reason="tests.messaging.recipient.ingest"):
        Message.objects.ingest(
            [
                ParsedMessage(
                    external_id=f"<ingest-{index}@example.test>",
                    platform="email",
                    subject="Shared subject",
                    sender=ParsedHandle(platform="email", value="from@example.test"),
                    recipients=(
                        ParsedRecipient(handle=ParsedHandle(platform="email", value="to@example.test")),
                        ParsedRecipient(handle=ParsedHandle(platform="email", value="to@example.test")),
                        ParsedRecipient(handle=ParsedHandle(platform="email", value="cc@example.test"), role="cc"),
                    ),
                    body=ParsedPart(text=f"Body {index}"),
                )
                for index in range(2)
            ],
            channel=channel,
        )
    assert Recipient._base_manager.count() == 4
    _assert_recipients_on_their_messages_threads()
