"""One owner writes an outgoing message, and the recipient model keeps its own thread."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext, override_settings
from django.utils import timezone
from hypothesis import given
from hypothesis import strategies as st
from rebac import PermissionDenied, system_context

from angee.intake.models import TaskIntake
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
from angee.messaging.managers import _preview
from angee.messaging.testing.models import Channel, Handle, Message, Part, Recipient, Thread
from angee.provenance.context import changeset_model
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
    ("values", "key"),
    [
        ({"local": {"delivery_attempts": 3}}, "local"),
        ({"queued": True, "scheduled_at": timezone.now() + timedelta(hours=1)}, "scheduled_at"),
        ({"subject": "One\nTwo"}, "subject"),
        ({"text": "  ", "html": ""}, "text"),
        ({"to": ["not an address"]}, "to"),
        ({"to": [], "cc": ["Reader <reader@example>"]}, "cc"),
        ({"to": [], "bcc": ["x" * 320 + "@example.test"]}, "bcc"),
        ({"to": []}, "to"),
        ({"sender": "desk"}, "sender"),
    ],
)
def test_a_new_message_is_refused_before_anything_is_written(desk: Any, values: dict[str, Any], key: str) -> None:
    with system_context(reason="tests.messaging.compose.sender"):
        handle = Handle.objects.upsert(platform="email", value="desk@example.test")
    values = {"text": "Hello.", "to": ["reader@example.test"], **values}
    if values.get("sender") == "desk":
        values["sender"] = handle
    before, handles = _rows(), Handle._base_manager.count()
    with pytest.raises(ValidationError) as refused:
        Message.objects.compose(channel=desk, actor=desk.owner, idempotency_key=f"refused-{key}", **values)
    assert key in refused.value.error_dict
    assert (_rows(), Handle._base_manager.count()) == (before, handles)
    assert not changeset_model()._base_manager.filter(key=f"refused-{key}").exists()


def test_an_anchor_is_refused_with_what_it_cannot_take(desk: Any) -> None:
    request = _receive(desk, "anchor")
    with system_context(reason="tests.messaging.compose.anchor"):
        doc = ChatterDoc.objects.create(title="Anchor")
        handle = Handle.objects.upsert(platform="email", value="desk@example.test")
    before = _rows()
    for values, key in (
        ({"parent": request, "channel": desk}, "channel"),
        ({"parent": request, "record": doc}, "record"),
        ({"record": doc, "to": ["reader@example.test"]}, "channel"),
        ({"parent": request, "subject": "Other"}, "subject"),
        ({"parent": request, "to": ["other@example.test"]}, "to"),
        ({"parent": request, "sender": handle}, "sender"),
    ):
        with pytest.raises(ValidationError) as refused:
            Message.objects.compose(text="Hello.", actor=desk.owner, **values)
        assert key in refused.value.error_dict, values
    assert _rows() == before


def test_an_email_reply_on_a_channel_that_cannot_send_is_refused_even_held(desk: Any) -> None:
    request = _receive(desk, "unsendable")
    before = _rows()
    with override_settings(ANGEE_EMAIL_DELIVERY_CONFIGURED=False), pytest.raises(OutboundEmailNotConfigured) as refused:
        Message.objects.compose(parent=request, text="Which team?", actor=desk.owner)
    assert refused.value.error_dict["channel"][0].code == "OUTBOUND_EMAIL_NOT_CONFIGURED"
    assert _rows() == before
    manual = make_integration("compose-manual", model=Channel, backend_class="manual")
    with pytest.raises(OutboundEmailChannelMissing):
        Message.objects.compose(channel=manual, to=["reader@example.test"], text="Hello.", actor=manual.owner)
    assert _rows() == before


def test_a_chat_reply_is_plain_text_from_an_explicit_sender_on_its_platform(desk: Any) -> None:
    chat = make_integration("compose-chat", model=Channel, backend_class="manual")
    question = _receive(chat, "room/1", sender="U-asker", platform="slack", thread=ParsedThread(external_id="room"))
    with system_context(reason="tests.messaging.compose.chat"):
        slack, email = (Handle.objects.upsert(platform=platform, value=value)
                        for platform, value in (("slack", "U-desk"), ("email", "desk@example.test")))
    for values, key in (
        ({}, "sender"), ({"sender": email}, "sender"),
        ({"sender": slack, "html": "<p>Yes.</p>"}, "html"),
        ({"sender": slack, "attachments": [_file(chat.owner, b"chat attachment")]}, "attachments"),
    ):
        with pytest.raises(ValidationError) as refused:
            Message.objects.compose(parent=question, text="Yes.", actor=chat.owner, **values)
        assert key in refused.value.error_dict
    reply = Message.objects.compose(parent=question, text="Yes.", sender=slack, actor=chat.owner)
    assert (reply.message_type, reply.sender_id, reply.title()) == ("chat", slack.pk, "")
    assert not Recipient._base_manager.filter(message=reply).exists()


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
    handles = Handle._base_manager.count()
    with pytest.raises(ValidationError) as refused:
        Message.objects.compose(channel=desk, to=["fresh@example.test", "broken@"], text="Hello.", actor=desk.owner)
    assert "to" in refused.value.error_dict
    assert Handle._base_manager.count() == handles


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
    assert "idempotency_key" in refused.value.error_dict
    outsider = make_user("compose-elevated-outsider")
    with system_context(reason="tests.messaging.compose.elevated"), pytest.raises(PermissionDenied):
        Message.objects.compose(**{**values, "actor": outsider}, idempotency_key="elevated")
    assert not changeset_model()._base_manager.filter(key="elevated").exists()


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
    before, key = _rows(), f"seat-{seat}-{anchor}"
    if allowed:
        message = Message.objects.compose(**values, text="Hello.", actor=actor, idempotency_key=key)
        assert message.direction == "outbound"
        return
    with pytest.raises(PermissionDenied):
        Message.objects.compose(**values, text="Hello.", actor=actor, idempotency_key=key)
    assert _rows() == before
    assert not changeset_model()._base_manager.filter(key=key).exists()


def test_an_attachment_the_actor_cannot_read_is_refused(desk: Any) -> None:
    stranger = make_user("compose-file-owner")
    hidden = _file(stranger, b"Not yours")
    with system_context(reason="tests.messaging.compose.hidden_file"):
        # The composer owns the drive, which lends its readers every inherited file.
        File._base_manager.filter(pk=hidden.pk).update(visibility="record")
    before = _rows()
    with pytest.raises(PermissionDenied):
        Message.objects.compose(
            channel=desk, to=["reader@example.test"], text="Hello.", attachments=[hidden], actor=desk.owner,
        )
    assert _rows() == before


def test_compose_writes_each_recipient_on_its_messages_thread(desk: Any) -> None:
    request = _receive(desk, "rows", reply_to="Applicant <applicant@example.test>")
    Message.objects.compose(parent=request, text="Thanks.", actor=desk.owner)
    Message.objects.compose(channel=desk, to=["a@example.test"], cc=["b@example.test"], text="Hi.", actor=desk.owner)
    _assert_recipients_on_their_messages_threads()


def test_compose_and_intakes_route_agree_on_which_message_can_be_answered(desk: Any) -> None:
    owner = desk.owner
    paused = make_integration("compose-paused", model=Channel, backend_class="email", lifecycle="paused",
                              config={"from_email": DESK})
    chat = make_integration("compose-chat-row", model=Channel, backend_class="manual")
    with system_context(reason="tests.messaging.compose.chat_sender"):
        slack = Handle.objects.upsert(platform="slack", value="U-desk")
    rows = {
        "email with a sender": (_receive(desk, "with-sender"), {}, None),
        "email with only a Reply-To": (_receive(desk, "relayed", sender=None, reply_to="a@example.test"), {}, None),
        "email with neither": (_receive(desk, "neither", sender=None), {}, ValidationError),
        "delivery not configured": (_receive(desk, "unconfigured"), {}, OutboundEmailNotConfigured),
        "channel paused": (_receive(paused, "paused"), {}, OutboundEmailChannelMissing),
        "chat": (_receive(chat, "room/2", sender="U-asker", platform="slack", thread=ParsedThread(external_id="r")),
                 {"sender": slack}, None),
    }
    for name, (message, values, refusal) in rows.items():
        configured = name != "delivery not configured"
        with override_settings(ANGEE_EMAIL_DELIVERY_CONFIGURED=configured):
            with system_context(reason="tests.messaging.compose.reply_error"):
                error = message.reply_error()
            route = TaskIntake._email_reply_route([SimpleNamespace(source_message=message)])
            actor = chat.owner if name == "chat" else paused.owner if name == "channel paused" else owner
            if refusal is None:
                assert error is None, name
                reply = Message.objects.compose(parent=message, text="Answer.", actor=actor, **values)
                assert reply.parent_id == message.pk, name
            else:
                assert isinstance(error, refusal), name
                with pytest.raises(refusal):
                    Message.objects.compose(parent=message, text="Answer.", actor=actor, **values)
        assert (route is message) == (error is None and message.message_type == "email"), name


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
        with pytest.raises(ValueError, match="message's thread"):
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
