"""One owner writes an outgoing message, and the recipient model keeps its own thread."""

from __future__ import annotations

import email.policy
import string
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from email import message_from_bytes
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext, override_settings
from django.utils import timezone
from hypothesis import example, given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from rebac import PermissionDenied, actor_context, system_context
from rebac.models import PermissionAuditEvent

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
from angee.messaging.email import header_text
from angee.messaging.managers import _preview
from angee.messaging.tasks import deliver_message
from angee.messaging.testing.models import Channel, Handle, Message, Part, Recipient, Thread
from angee.messaging_integrate_imap.parser import parse_message
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


def _read_back(raw: bytes) -> str:
    """The subject a reader's mail library parses out of a message's bytes."""

    return str(message_from_bytes(raw, policy=email.policy.default)["Subject"])


def _subjects(channel: Any, message: Any) -> tuple[str, str, str]:
    """The subject compose stored, the one read back off the wire, and the one a provider's own mail carries.

    A provider's HTTP API takes the subject as a string and writes its own header from it.
    """

    with system_context(reason="tests.messaging.compose.subject"):
        stored = message.title()
        built = channel.backend.email_message(message)
    provided = EmailMessage(policy=email.policy.default)
    provided["Subject"] = built.subject
    return stored, _read_back(built.message().as_bytes()), _read_back(provided.as_bytes())


def _one_plain_line(subject: str) -> bool:
    return not any(unicodedata.category(character) in ("Cc", "Zl", "Zp") for character in subject)


ENCODED_WORDS = {
    "terminated": "Invoice =?utf-8?q?=E2=80=AE?= ready",
    "unterminated": "x =?utf-8?q?=E2=80=AE y",
    "mid-word": "abc=?utf-8?b?4oCu?=def",
    "empty charset": "a =??q?=E2=80=AE?= b",
    "unknown charset": "a =?x-nope?q?=E2=80=AE?= b",
    "upper case": "a =?UTF-8?Q?=E2=80=AE?= b",
    "encoded CR LF": "Hi =?utf-8?q?a=0D=0ABcc=3A?= evil@example.com there",
    "encoded NUL": "x =?utf-8?q?=00?= y",
    "escape sequence": "x =?utf-8?q?=1B[31m?= y",
    "typed control": "Tab\there \x1b[31m red",
}


@pytest.mark.parametrize("subject", ENCODED_WORDS.values(), ids=ENCODED_WORDS)
def test_a_subject_is_sent_and_read_back_as_it_is_stored(desk: Any, subject: str) -> None:
    """No encoded word or control character survives into what is stored, sent or read: they are one string."""

    message = Message.objects.compose(channel=desk, to=["r@example.test"], subject=subject, text="t", actor=desk.owner)
    stored, wire, provided = _subjects(desk, message)
    assert stored == wire == provided
    assert _one_plain_line(stored)


def test_a_subject_that_opens_no_encoded_word_is_kept_as_typed(desk: Any) -> None:
    message = Message.objects.compose(
        channel=desk, to=["r@example.test"], subject="2+2=? and more", text="t", actor=desk.owner,
    )
    assert _subjects(desk, message) == ("2+2=? and more",) * 3


def test_a_reply_to_a_title_holding_an_encoded_word_is_written_on_one_line(desk: Any) -> None:
    """A hostile inbound subject never makes its conversation unanswerable, nor reaches a reader decoded."""

    raw = (
        b"From: Mallory <mallory@example.test>\r\nTo: desk@example.test\r\nMessage-ID: <hostile@example.test>\r\n"
        b"Subject: =?utf-8?q?=3D=3Futf-8=3Fq=3F=3DE2=3D80=3DAE=3F=3D_and_=3D=3Futf-8=3Fq=3Fa=3D0D=3D0ABcc=3A=3F=3D?="
        b"\r\n\r\nbody\r\n"
    )
    with system_context(reason="tests.messaging.compose.hostile"):
        (parent,) = Message.objects.ingest([parse_message(raw, mailbox="INBOX", uid=1, uidvalidity=1)], channel=desk)
        assert "=?utf-8?q?" in parent.thread.display_title
    reply = Message.objects.compose(parent=parent, text="Answer.", actor=desk.owner)
    stored, wire, provided = _subjects(desk, reply)
    assert stored == wire == provided
    assert stored.startswith("Re: ") and _one_plain_line(stored)


MAILERS = {
    "default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"},
    "smtp": {"BACKEND": "django.core.mail.backends.smtp.EmailBackend", "OPTIONS": {"host": "localhost"}},
}
"""The test mailer delivers; the SMTP one is only asked for the envelope it would send, never connected."""


def _delivered(message: Any) -> EmailMessage:
    """Deliver ``message`` through the test mailer as the worker does, and return the bytes it sent, parsed."""

    assert deliver_message(message._meta.label_lower, message.pk, message.external_id)["delivered"]
    return message_from_bytes(mail.outbox[-1].message().as_bytes(), policy=email.policy.default)


def test_an_answer_to_a_sender_named_with_an_encoded_word_is_delivered_to_that_sender(desk: Any, settings: Any) -> None:
    """A hostile inbound name reaches the wire as written, never decoded into another name the envelope refuses."""

    settings.MAILERS = MAILERS
    raw = (
        b"From: =?utf-8?q?=3D=3Futf-8=3Fq=3Fceo=3D40bank.test=3F=3D?= <mallory@example.test>\r\n"
        b"To: desk@example.test\r\nMessage-ID: <hostile-name@example.test>\r\nSubject: Hello\r\n\r\nbody\r\n"
    )
    with system_context(reason="tests.messaging.compose.hostile_name"):
        (parent,) = Message.objects.ingest([parse_message(raw, mailbox="INBOX", uid=1, uidvalidity=1)], channel=desk)
        stored = (parent.sender.display_name, parent.sender.value)
    assert stored == ("=?utf-8?q?ceo=40bank.test?=", "mallory@example.test")
    reply = Message.objects.compose(parent=parent, text="Answer.", actor=desk.owner, queued=True)
    (to,) = _delivered(reply)["To"].addresses
    assert (to.display_name, to.addr_spec) == (header_text(stored[0]), stored[1])
    assert to.display_name == "= ?utf-8?q?ceo=40bank.test?="
    assert mail.mailers["smtp"].prep_address(str(to)) == stored[1]


def test_an_internationalised_domain_is_delivered_in_its_ascii_form(desk: Any, settings: Any) -> None:
    """Typed or taken from an inbound sender, the address is stored as written and sent with its domain in IDNA form."""

    settings.MAILERS = MAILERS
    raw = (
        "From: Ada <ada@exämple.test>\r\nTo: desk@example.test\r\nMessage-ID: <idn@example.test>\r\n"
        "Subject: Hello\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nbody\r\n"
    ).encode()
    with system_context(reason="tests.messaging.compose.idn"):
        (parent,) = Message.objects.ingest([parse_message(raw, mailbox="INBOX", uid=1, uidvalidity=1)], channel=desk)
    typed = Message.objects.compose(
        channel=desk, to=["Ada <ada@exämple.test>"], text="Hi.", actor=desk.owner, queued=True,
    )
    answer = Message.objects.compose(parent=parent, text="Answer.", actor=desk.owner, queued=True)
    for message in (typed, answer):
        with system_context(reason="tests.messaging.compose.idn"):
            assert Recipient._base_manager.get(message=message).handle.value == "ada@exämple.test"
        (to,) = _delivered(message)["To"].addresses
        assert (to.display_name, to.addr_spec) == ("Ada", "ada@xn--exmple-cua.test")
        assert mail.mailers["smtp"].prep_address(str(to)) == "ada@xn--exmple-cua.test"


def test_mail_in_a_mailbox_thread_an_earlier_version_folded_into_a_record_stays_its_owners(desk: Any) -> None:
    """The record's moderator removes what was received on the chatter, never a colleague's own mail in its group."""

    owner = desk.owner
    colleague = make_user("compose-folded-colleague")
    mailbox = make_integration("compose-folded-mailbox", model=Channel, backend_class="manual", owner=colleague)
    with system_context(reason="tests.messaging.compose.folded"):
        doc = ChatterDoc.objects.create(title="Quote 7")
    grant(doc, "manager", owner)
    sent = Message.objects.compose(channel=desk, record=doc, to=["client@example.test"], subject="Quote 7",
                                   text="Here is the quote.", actor=owner)
    private = (
        b"From: friend@example.test\r\nTo: colleague@example.test\r\nMessage-ID: <private@example.test>\r\n"
        b"Subject: Between us\r\n\r\nPrivate note.\r\n"
    )
    planted = (
        f"From: stranger@evil.test\r\nTo: desk@example.test\r\nMessage-ID: <planted@evil.test>\r\n"
        f"In-Reply-To: <{sent.external_id}>\r\nSubject: Re: Quote 7\r\n\r\nPay this other account.\r\n"
    ).encode()
    with system_context(reason="tests.messaging.compose.folded"):
        (mail,) = Message.objects.ingest(
            [parse_message(private, mailbox="INBOX", uid=1, uidvalidity=1)], channel=mailbox,
        )
        (spam,) = Message.objects.ingest([parse_message(planted, mailbox="INBOX", uid=2, uidvalidity=1)], channel=desk)
        chatter = doc.message_thread(create=False)
        chatter.fold([Thread._base_manager.get(pk=mail.thread_id)])  # as source identity folded one before
    with actor_context(owner):
        record = ChatterDoc.objects.with_actor(owner).get(pk=doc.pk)
        rows, _count = Message.objects.for_record(record)
        assert {row.pk: row.can_trash(moderate_access=True) for row in rows if row.pk in (mail.pk, spam.pk)} == {
            mail.pk: False, spam.pk: True,
        }
        with pytest.raises(ValidationError) as retained:
            record.message_trash(Message.objects.in_record(record, mail.public_id), reason="moderated")
        record.message_trash(Message.objects.in_record(record, spam.public_id), reason="spam")
        removed, _count = record.message_removed()
    assert retained.value.code == "retained"
    assert [message.pk for message in removed] == [spam.pk]
    assert not Message._base_manager.get(pk=mail.pk).is_trashed


def test_a_stored_message_keeps_the_kind_it_was_ingested_with_through_every_re_sync(desk: Any) -> None:
    """An answer stored as a comment before email answers landed as email stays one, edited or not."""

    owner = desk.owner
    with system_context(reason="tests.messaging.compose.kept_kind"):
        doc = ChatterDoc.objects.create(title="Invoice 10")
    grant(doc, "manager", owner)
    sent = Message.objects.compose(channel=desk, record=doc, to=["client@example.test"], subject="Invoice 10",
                                   text="Your invoice.", actor=owner)

    def answer(text: str) -> Any:
        raw = (
            f"From: client@example.test\r\nTo: desk@example.test\r\nMessage-ID: <earlier@example.test>\r\n"
            f"In-Reply-To: <{sent.external_id}>\r\nSubject: Re: Invoice 10\r\n\r\n{text}\r\n"
        ).encode()
        return parse_message(raw, mailbox="INBOX", uid=1, uidvalidity=1)

    with system_context(reason="tests.messaging.compose.kept_kind"):
        (earlier,) = Message.objects.ingest([answer("Thanks.")], channel=desk)
        Message._base_manager.filter(pk=earlier.pk).update(message_type="comment")  # as ingested before
        kinds = []
        for text in ("Thanks.", "Thanks, corrected."):
            Message.objects.ingest([answer(text)], channel=desk)
            kinds.append(Message._base_manager.get(pk=earlier.pk).message_type)
    assert kinds == ["comment", "comment"]


def test_mail_a_stranger_sends_onto_a_records_chatter_is_removable_and_mail_sent_from_it_is_retained(
    desk: Any,
) -> None:
    """Anyone who saw a chatter mail's Message-ID can answer onto the record; its moderator can take it off."""

    owner = desk.owner
    with system_context(reason="tests.messaging.compose.planted"):
        doc = ChatterDoc.objects.create(title="Invoice 9")
    grant(doc, "manager", owner)
    sent = Message.objects.compose(channel=desk, record=doc, to=["client@example.test"], subject="Invoice 9",
                                   text="Your invoice.", actor=owner)
    raw = (
        f"From: stranger@evil.test\r\nTo: desk@example.test\r\nMessage-ID: <planted@evil.test>\r\n"
        f"In-Reply-To: <{sent.external_id}>\r\nSubject: Re: Invoice 9\r\n\r\nPay this other account.\r\n"
    ).encode()
    with system_context(reason="tests.messaging.compose.planted"):
        (planted,) = Message.objects.ingest([parse_message(raw, mailbox="INBOX", uid=1, uidvalidity=1)], channel=desk)
        assert planted.thread_id == doc.message_thread(create=False).pk
    with actor_context(owner):
        record = ChatterDoc.objects.with_actor(owner).get(pk=doc.pk)
        assert Message.objects.with_actor(owner).get(pk=planted.pk).can_trash(moderate_access=True)
        record.message_trash(Message.objects.with_actor(owner).get(pk=planted.pk), reason="spam")
        with pytest.raises(ValidationError) as retained:
            record.message_trash(Message.objects.with_actor(owner).get(pk=sent.pk))
    assert Message._base_manager.get(pk=planted.pk).is_trashed
    assert not Message._base_manager.get(pk=sent.pk).is_trashed
    assert (retained.value.code, retained.value.messages) == (
        "retained", ["Only comments and received messages can be removed."],
    )


def test_an_address_whose_local_part_no_transport_carries_fails_delivery_saying_so(desk: Any, settings: Any) -> None:
    """Admitted, as inbound mail stores such senders, and refused at delivery with the reason a person can act on."""

    settings.MAILERS = MAILERS
    typed = Message.objects.compose(channel=desk, to=["jörg@example.test"], text="Hi.", actor=desk.owner, queued=True)
    answer = Message.objects.compose(
        parent=_receive(desk, "utf8-local", sender="jörg@example.test"), text="Answer.", actor=desk.owner, queued=True,
    )
    for message in (typed, answer):
        assert deliver_message(message._meta.label_lower, message.pk, message.external_id)["status"] == "failed"
        with system_context(reason="tests.messaging.compose.utf8_local"):
            message.refresh_from_db()
            assert message.local_metadata["delivery_error"] == (
                "jörg@example.test cannot be delivered: this mail transport cannot carry an address whose part "
                "before the @ is outside ASCII. Send to another address of this contact."
            )
    assert not mail.outbox


def test_a_mail_sent_from_a_records_chatter_is_answered_and_its_answer_answered_by_email(
    desk: Any, settings: Any,
) -> None:
    """Send, receive and reply: the customer's answer lands on the chatter as an email, and is answered as one."""

    settings.MAILERS = MAILERS
    owner = desk.owner
    with system_context(reason="tests.messaging.compose.round_trip"):
        doc = ChatterDoc.objects.create(title="Invoice 42")
    grant(doc, "manager", owner)
    sent = Message.objects.compose(
        channel=desk, record=doc, to=["Client <client@example.test>"], subject="Invoice 42",
        text="Please find the invoice attached.", actor=owner, queued=True,
    )
    wire = _delivered(sent)
    raw = (
        f"From: Client <client@example.test>\r\nTo: {wire['From']}\r\nMessage-ID: <paid@example.test>\r\n"
        f"In-Reply-To: {wire['Message-ID']}\r\nReferences: {wire['Message-ID']}\r\nSubject: Re: Invoice 42\r\n"
        "\r\nThanks, paid.\r\n"
    ).encode()
    with system_context(reason="tests.messaging.compose.round_trip"):
        (received,) = Message.objects.ingest([parse_message(raw, mailbox="INBOX", uid=1, uidvalidity=1)], channel=desk)
        chatter = doc.message_thread(create=False).pk
    assert (received.message_type, received.thread_id, received.parent_id) == ("email", chatter, sent.pk)
    answer = Message.objects.compose(parent=received, text="Receipt attached.", actor=owner, queued=True)
    assert (answer.message_type, answer.thread_id) == ("email", chatter)
    out = _delivered(answer)
    assert ([address.addr_spec for address in out["To"].addresses], out["In-Reply-To"]) == (
        ["client@example.test"], "<paid@example.test>",
    )


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


def test_a_refusal_that_followed_a_write_is_seen_in_what_the_call_executed(desk: Any) -> None:
    """The refusal check reads the statements: a late refusal is caught though its rollback leaves no row.

    A keyed call claims its key before present state decides it, so a channel whose
    configured sender is invalid refuses it after that write.
    """

    unsendable = make_integration(
        "compose-invalid-sender", model=Channel, backend_class="email", config={"from_email": "not an address"},
    )
    before = _rows()
    with pytest.raises(AssertionError), _refused(key="channel"):
        Message.objects.compose(
            channel=unsendable, to=["reader@example.test"], text="Hi.", actor=unsendable.owner, idempotency_key="late",
        )
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


def test_the_permission_to_send_is_the_channels_one_named_permission(
    desk: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The answer gate and the new-message gate both decide ``Channel.reply_permission``, so renaming it moves both."""

    request = _receive(desk, "named-permission")
    reader = make_user("compose-named-permission-reader")
    grant(desk, "reader", reader)
    with pytest.raises(PermissionDenied):
        Message.objects.compose(channel=desk, to=["named@example.test"], text="Hi.", actor=reader)
    monkeypatch.setattr(Channel, "reply_permission", "read")
    answer = Message.objects.compose(parent=request, text="Which team?", actor=reader)
    new = Message.objects.compose(channel=desk, to=["named@example.test"], text="Hi.", actor=reader)
    assert (answer.parent_id, new.channel_id) == (request.pk, desk.pk)


def test_a_refused_reply_writes_no_permission_audit_row(desk: Any) -> None:
    """Who may answer is decided under the actor before anything is read elevated, so no refusal is audited."""

    request = _receive(desk, "audited")
    reader = make_user("compose-audit-reader")
    grant(desk, "reader", reader)
    refusals = (
        (PermissionDenied, {"actor": make_user("compose-audit-outsider")}, True),
        (PermissionDenied, {"actor": reader}, True),
        (OutboundEmailNotConfigured, {"actor": desk.owner}, False),
    )
    for error, values, configured in refusals:
        before = PermissionAuditEvent.objects.count()
        with override_settings(ANGEE_EMAIL_DELIVERY_CONFIGURED=configured), pytest.raises(error):
            Message.objects.compose(parent=request, text="Which team?", **values)
        assert PermissionAuditEvent.objects.count() == before, values


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


REREAD = {
    "a<b@example.test": ("b@example.test",),
    "a:b@example.test": ("b@example.test",),
    "a(comment)@example.test": ("a@example.test",),
    "team:a@example.test;": ("a@example.test",),
    "a@x.test,b@example.test": ("a@x.test", "b@example.test"),
    "a@example.test junk": ("a@example.testjunk",),
    "a@example.test(c)": ("a@example.test",),
    "eve@example.test (Eve)": ("eve@example.test",),
}
"""Entries a mail library reads as another address, or as several: each would land the addresses beside it."""


@pytest.mark.parametrize("entry", REREAD)
def test_an_entry_compose_would_read_as_another_address_is_refused(desk: Any, entry: str) -> None:
    with _refused(key="to", code="invalid"):
        Message.objects.compose(channel=desk, to=[entry], text="Hi.", actor=desk.owner)
    assert not Handle._base_manager.filter(value__in=REREAD[entry]).exists()


@pytest.mark.parametrize("role", ["to", "cc", "bcc"])
@pytest.mark.parametrize("entry", [
    "=?us-ascii?q?boss?=@example.test",
    "=?utf-8?q?b=40evil.test?=@example.test",
    "=?utf-8?q?ceo=40bank.test?= <eve@example.test>",
    '"Eve\tX" <eve@example.test>',
    '"Eve\x1bX" <eve@example.test>',
    "Eve\u2028X <eve@example.test>",
])
def test_an_entry_a_mail_header_would_not_carry_as_typed_is_refused_under_its_role(
    desk: Any, role: str, entry: str,
) -> None:
    """A mail library decodes an encoded word into another address or name, and rewrites a control character."""

    with _refused(key=role, code="invalid"):
        Message.objects.compose(channel=desk, **{role: [entry]}, text="Hi.", actor=desk.owner)
    assert not Handle._base_manager.filter(value__in=("boss@example.test", "b@evil.test", "eve@example.test")).exists()


@pytest.mark.parametrize("role", ["to", "cc", "bcc"])
@pytest.mark.parametrize("entry", ["Na\x00me <nul@example.test>", "nul@example.test\x00", "nu\x00l@example.test"])
def test_an_entry_carrying_a_nul_is_refused_under_its_role(desk: Any, role: str, entry: str) -> None:
    """PostgreSQL stores no NUL, so a name holding one would fail the write after it began; it is refused first."""

    with _refused(key=role, code="invalid"):
        Message.objects.compose(channel=desk, **{role: [entry]}, text="Hi.", actor=desk.owner)


@pytest.mark.parametrize(("entry", "expected"), [
    ("Customer@Example.COM", ("", "Customer@Example.COM")),
    ("Name <a@example.test>", ("Name", "a@example.test")),
    ('"Doe, Jane" <jane@example.test>', ("Doe, Jane", "jane@example.test")),
    ("jörg@example.test", ("", "jörg@example.test")),
    ("Eve\tX <eve@example.test>", ("Eve X", "eve@example.test")),
])
def test_clean_recipient_returns_exactly_what_compose_lands(desk: Any, entry: str, expected: tuple[str, str]) -> None:
    assert Message.objects.clean_recipient(entry) == expected
    message = Message.objects.compose(channel=desk, to=[entry], text="Hi.", actor=desk.owner)
    with system_context(reason="tests.messaging.compose.clean_recipient"):
        assert Recipient._base_manager.get(message=message).handle.value == expected[1]


@given(st.text(alphabet=string.ascii_letters + '@.<>:;,()" ', max_size=40))
@example("a<b@example.test")
@example("a(b)@example.test")
@example("a@example.test c")
@example("a:b@example.test")
@hypothesis_settings(max_examples=300, deadline=None)
def test_an_entry_is_refused_or_lands_exactly_the_address_it_names(entry: str) -> None:
    """Whatever is typed, compose refuses it or lands one address that the entry itself names."""

    try:
        outgoing = Message.objects._validate_outgoing(
            channel=None, parent=None, record=None, sender=None, to=(entry,), cc=(), bcc=(), subject="",
            text="x", html="", attachments=(), local={}, queued=False, scheduled_at=None,
        )
    except ValidationError as error:
        assert "to" in error.error_dict
        return
    (addressee,) = outgoing.recipients
    typed = entry.strip()
    assert addressee.address == typed or typed.endswith(f"<{addressee.address}>")


def test_a_schedule_time_without_an_offset_is_refused_before_the_claim(desk: Any) -> None:
    """A naive time is never read in the server's zone; an aware one holds the draft until then."""

    naive = datetime(2030, 1, 1, 10, 0)
    with _refused(key="scheduled_at", code="naive_datetime"):
        Message.objects.compose(
            channel=desk, to=["later@example.test"], text="Hi.", scheduled_at=naive, idempotency_key="naive",
            actor=desk.owner,
        )
    held = Message.objects.compose(
        channel=desk, to=["later@example.test"], text="Hi.", scheduled_at=naive.replace(tzinfo=UTC),
        actor=desk.owner,
    )
    assert held.scheduled_at == naive.replace(tzinfo=UTC)


def _addresses(role: str, count: int) -> list[str]:
    return [f"{role}-{index}@example.test" for index in range(count)]


@pytest.mark.parametrize(("to", "cc", "bcc", "key"), [(51, 0, 0, "to"), (30, 21, 0, "cc"), (20, 20, 11, "bcc")])
def test_more_recipients_than_the_bound_are_refused_under_the_role_that_crosses_it(
    desk: Any, to: int, cc: int, bcc: int, key: str,
) -> None:
    """The three roles share one bound (50 by default), checked before anything is read."""

    with CaptureQueriesContext(connection) as queries, pytest.raises(ValidationError) as refused:
        Message.objects.compose(
            channel=desk, to=_addresses("to", to), cc=_addresses("cc", cc), bcc=_addresses("bcc", bcc),
            text="Hi.", actor=desk.owner,
        )
    assert [(field, error.code) for field, errors in refused.value.error_dict.items() for error in errors] == [
        (key, "too_many"),
    ]
    assert not queries.captured_queries


def test_a_message_at_the_recipient_bound_is_sent(desk: Any) -> None:
    message = Message.objects.compose(
        channel=desk, to=_addresses("to", 48), cc=_addresses("cc", 1), bcc=_addresses("bcc", 1),
        text="Hi.", actor=desk.owner,
    )
    with system_context(reason="tests.messaging.compose.bound"):
        assert Recipient._base_manager.filter(message=message).count() == 50


def test_more_attachments_than_the_bound_are_refused_before_any_file_is_read(desk: Any, settings: Any) -> None:
    files = [_file(desk.owner, f"attachment {index}".encode(), name=f"a{index}.txt") for index in range(3)]
    with CaptureQueriesContext(connection) as queries, pytest.raises(ValidationError) as refused:
        Message.objects.compose(
            channel=desk, to=["bound@example.test"], text="Hi.", attachments=[files[0]] * 21, actor=desk.owner,
        )
    assert [(field, error.code) for field, errors in refused.value.error_dict.items() for error in errors] == [
        ("attachments", "too_many"),
    ]
    assert not queries.captured_queries
    settings.ANGEE_MESSAGING_MAX_ATTACHMENTS = 2
    with _refused(key="attachments", code="too_many"):
        Message.objects.compose(
            channel=desk, to=["bound@example.test"], text="Hi.", attachments=files, actor=desk.owner,
        )
    message = Message.objects.compose(
        channel=desk, to=["bound@example.test"], text="Hi.", attachments=files[:2], actor=desk.owner,
    )
    with system_context(reason="tests.messaging.compose.bound"):
        assert Part._base_manager.filter(message=message, file__isnull=False).count() == 2


def test_the_channels_sender_handle_has_no_creator_and_a_typed_contact_keeps_its_composer(desk: Any) -> None:
    """The first composer on a channel holds nothing of its sender; the person who types a contact holds that."""

    replier = make_user("compose-first-replier")
    grant(desk, "replier", replier)
    message = Message.objects.compose(channel=desk, to=["first-contact@example.test"], text="Hi.", actor=replier)
    with system_context(reason="tests.messaging.compose.sender_creator"):
        sender = Handle._base_manager.get(pk=message.sender_id)
        recipient = Recipient._base_manager.select_related("handle").get(message=message).handle
    assert sender.created_by_id is None
    for action in ("read", "write", "delete"):
        assert not Handle._base_manager.get(pk=sender.pk).with_actor(replier).has_access(action), action
    assert recipient.created_by_id == replier.pk


def test_every_writer_lands_an_email_address_on_one_handle(db: None, settings: Any) -> None:
    """The channel's sender, a typed recipient and ingest each land an address on its one handle, as first written."""

    del db
    settings.ANGEE_EMAIL_DELIVERY_CONFIGURED = True
    channel = make_integration("compose-case", model=Channel, backend_class="email",
                               config={"from_email": "Desk <Desk@Example.TEST>"})
    with system_context(reason="tests.messaging.compose.case"):
        Message.objects.ingest([ParsedMessage(
            external_id="<case@example.test>", platform="email", subject="Hello",
            # The form the IMAP parser emits: trimmed and lower-cased.
            sender=ParsedHandle(platform="email", value="client@example.test"),
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


def test_compose_refuses_an_elevated_call(desk: Any) -> None:
    """No elevation is carried into compose, for its owner or anyone else."""

    for actor in (desk.owner, make_user("compose-elevated-outsider")):
        with system_context(reason="tests.messaging.compose.elevated"), _refused(RuntimeError):
            Message.objects.compose(channel=desk, to=["reader@example.test"], text="Hi.", actor=actor,
                                    idempotency_key="elevated")


@pytest.mark.entrypoint_boundaries
@pytest.mark.django_db
def test_compose_runs_as_its_actor_under_a_requests_changeset_with_none_process_wide(settings: Any) -> None:
    """A request or a task opens its own changeset and publishes none process-wide; that one holds compose's rows."""

    settings.ANGEE_EMAIL_DELIVERY_CONFIGURED = True
    with changeset(fresh=True, change_reason="setup"):
        desk = make_integration("compose-request", model=Channel, backend_class="email", config={"from_email": DESK})
    owner = desk.owner
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
    assert to == {"reply-to": ["Ann@Example.test"], "sender": ["requester@example.test"]}
    unaddressed = _receive(desk, "unaddressed", sender=None)
    assert unaddressed.reply_error(actor=owner).error_dict["parent"][0].code == "reply_unaddressed"


def test_an_answer_goes_once_to_each_address_a_reply_to_names_as_the_header_spells_it(desk: Any) -> None:
    """Two spellings of one address in a Reply-To are one recipient, addressed as the header first spells it."""

    relayed = _receive(
        desk, "relayed-twice", sender="relay@example.test",
        reply_to="Ann <Ann@Example.test>, ann@EXAMPLE.test, Bob <bob@xn--bcher-kva.example>, bob@bücher.example",
    )
    assert relayed.reply_addresses(actor=desk.owner) == {"Ann@Example.test": "Ann", "bob@xn--bcher-kva.example": "Bob"}
    reply = Message.objects.compose(parent=relayed, text="Thanks.", actor=desk.owner)
    with system_context(reason="tests.messaging.compose.reply_to.twice"):
        to = list(Recipient._base_manager.filter(message=reply).order_by("pk").values_list("handle__value", flat=True))
    assert to == ["Ann@Example.test", "bob@xn--bcher-kva.example"]
    assert _wire(desk, reply)["To"] == "Ann <Ann@Example.test>, Bob <bob@xn--bcher-kva.example>"


def test_an_answer_to_an_address_a_handle_holds_goes_as_that_handle_stores_it_with_its_name(desk: Any) -> None:
    """The header's spelling and name only fill a handle that has none: compose first stored the address, inbound
    mail and a named third spelling landed on it, and an answer to a fourth spelling is addressed as stored."""

    first = Message.objects.compose(channel=desk, to=["Client@Example.test"], text="Hello.", actor=desk.owner)
    relayed = _receive(desk, "relayed-held", sender="client@example.test", reply_to="Relay <CLIENT@EXAMPLE.TEST>")
    with system_context(reason="tests.messaging.compose.reply_to.held"):
        (held,) = Recipient._base_manager.filter(message=first).values_list("handle_id", flat=True)
        assert Handle.objects.upsert(platform="email", value="CLIENT@example.TEST", display_name="Third").pk == held
    assert relayed.sender_id == held
    reply = Message.objects.compose(parent=relayed, text="Thanks.", actor=desk.owner)
    with system_context(reason="tests.messaging.compose.reply_to.held"):
        assert list(Recipient._base_manager.filter(message=reply).values_list("handle_id", flat=True)) == [held]
    assert _wire(desk, reply)["To"] == "Third <Client@Example.test>"


@pytest.mark.parametrize("local", ["a\x1bb", "a\u202eb", "a\u2028b", "a\u200bb", "a\x85b"])
def test_an_answer_to_a_sender_whose_address_is_not_one_is_refused_unwritten(desk: Any, local: str) -> None:
    """The sender an answer falls back to is checked as a Reply-To is, so no unprintable address reaches the wire."""

    parent = _receive(desk, f"sender-{ord(local[1])}", sender=f"{local}@example.test")
    assert parent.reply_error(actor=desk.owner).error_dict["parent"][0].code == "reply_unaddressed"
    with _refused(key="parent", code="reply_unaddressed"):
        Message.objects.compose(parent=parent, text="Thanks.", actor=desk.owner)


def test_an_answer_to_a_reply_to_over_the_recipient_bound_is_refused_and_one_at_it_is_written(desk: Any) -> None:
    """The bound on to, cc and bcc holds for the addresses an answer takes from its parent's Reply-To."""

    owner = desk.owner
    over = _receive(desk, "reply-to-over", reply_to=", ".join(_addresses("reply", 51)))
    assert over.reply_error(actor=owner).error_dict["reply_to"][0].code == "too_many"
    with _refused(key="reply_to", code="too_many"):
        Message.objects.compose(parent=over, text="Thanks.", actor=owner)
    at = _receive(desk, "reply-to-at", reply_to=", ".join(_addresses("reply", 50)))
    assert at.reply_error(actor=owner) is None
    answer = Message.objects.compose(parent=at, text="Thanks.", actor=owner)
    with system_context(reason="tests.messaging.compose.reply_to_bound"):
        assert Recipient._base_manager.filter(message=answer).count() == 50


def test_an_answer_to_a_message_with_a_channel_and_no_thread_is_refused_unwritten(desk: Any) -> None:
    """``Message.thread`` is nullable and nothing ties it to the channel, so the envelope refuses such a parent."""

    with system_context(reason="tests.messaging.compose.threadless"):
        parent = Message._base_manager.create(channel=desk, platform="email", message_type="email")
    assert parent.reply_error(actor=desk.owner).error_dict["parent"][0].code == "reply_incomplete"
    with _refused(key="parent", code="reply_incomplete"):
        Message.objects.compose(parent=parent, text="Thanks.", actor=desk.owner)


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


STATEMENTS = {1: 99, 20: 365}
"""Statements one compose to 1 and to 20 known recipients executes: the measured count is the ceiling."""


def test_a_compose_executes_a_bounded_number_of_statements_per_known_recipient(desk: Any) -> None:
    """Known recipients resolve in one read; what each further one costs is its recipient row's own insert.

    That insert runs the REBAC relation checks for its thread and message and the
    timeline's invalidation (about fourteen statements); a new address adds the
    handle owner's create.
    """

    known = [f"known-{index}@example.test" for index in range(20)]
    for size in STATEMENTS:
        Message.objects.compose(channel=desk, to=known[:size], text="Hi.", actor=desk.owner)
    counts = {}
    for size in STATEMENTS:
        with CaptureQueriesContext(connection) as queries:
            Message.objects.compose(channel=desk, to=known[:size], text="Hi.", actor=desk.owner)
        counts[size] = len(queries)
    assert all(counts[size] <= ceiling for size, ceiling in STATEMENTS.items()), counts


def test_a_compose_to_another_letter_case_of_a_contacts_address_is_addressed_as_the_contact_stores_it(
    desk: Any,
) -> None:
    """One address is one handle: the contact typed first keeps its spelling, which the wire carries."""

    with system_context(reason="tests.messaging.compose.spelling"):
        contact = Handle.objects.upsert(platform="email", value="Client@Example.test")
    message = Message.objects.compose(channel=desk, to=["client@example.test"], text="Hi.", actor=desk.owner)
    with system_context(reason="tests.messaging.compose.spelling"):
        assert list(Recipient._base_manager.filter(message=message).values_list("handle_id", flat=True)) == [contact.pk]
        assert Handle._base_manager.filter(platform="email").count() == 2  # The client and the desk.
    assert _wire(desk, message)["To"] == "Client@Example.test"


def test_a_new_recipient_is_stored_and_sent_as_typed_and_every_other_spelling_lands_on_it(desk: Any) -> None:
    """A handle compose creates first keeps the typed spelling, which the wire carries; a contact typed later in
    another letter case, and a later compose, land on it."""

    message = Message.objects.compose(channel=desk, to=["Ada@Example.com"], text="Hi.", actor=desk.owner)
    with system_context(reason="tests.messaging.compose.spelling"):
        (written,) = Recipient._base_manager.filter(message=message).values_list("handle_id", flat=True)
        contact = Handle.objects.upsert(platform="email", value="ada@example.com")
    later = Message.objects.compose(channel=desk, to=["ADA@example.COM"], text="Again.", actor=desk.owner)
    with system_context(reason="tests.messaging.compose.spelling"):
        assert list(Recipient._base_manager.filter(message=later).values_list("handle_id", flat=True)) == [written]
    assert (contact.pk, contact.value) == (written, "Ada@Example.com")
    assert _wire(desk, message)["To"] == _wire(desk, later)["To"] == "Ada@Example.com"


def test_one_new_address_typed_twice_in_one_compose_is_stored_and_sent_once_as_first_typed(desk: Any) -> None:
    """Compose takes its handles by spelling key, which keeps the typed order among spellings of one address."""

    message = Message.objects.compose(
        channel=desk, to=["ada@twice.example", "Ada@TWICE.example"], text="Hi.", actor=desk.owner,
    )
    with system_context(reason="tests.messaging.compose.twice"):
        handles = set(Recipient._base_manager.filter(message=message).values_list("handle__value", flat=True))
    assert handles == {"ada@twice.example"}
    assert _wire(desk, message)["To"] == "ada@twice.example"


def test_a_channels_sender_is_landed_as_configured(db: None, settings: Any) -> None:
    """The desk's configured address is its sender handle's spelling, written on the wire as configured."""

    del db
    settings.ANGEE_EMAIL_DELIVERY_CONFIGURED = True
    channel = make_integration("compose-sender-case", model=Channel, backend_class="email",
                               config={"from_email": "Desk <Desk@Example.TEST>"})
    message = Message.objects.compose(channel=channel, to=["client@example.test"], text="Hi.", actor=channel.owner)
    with system_context(reason="tests.messaging.compose.sender"):
        assert Message._base_manager.get(pk=message.pk).sender.value == "Desk@Example.TEST"
    assert _wire(channel, message)["From"] == "Desk <Desk@Example.TEST>"


def test_a_providers_alias_is_a_handle_of_its_own_and_is_sent_as_typed(desk: Any) -> None:
    """A Gmail plus-tag is the same person's address, not the same address: its own handle, its tag kept."""

    with system_context(reason="tests.messaging.compose.alias"):
        mailbox = Handle.objects.upsert(platform="email", value="ab@gmail.com")
    message = Message.objects.compose(channel=desk, to=["a.b+promo@gmail.com"], text="Hi.", actor=desk.owner)
    with system_context(reason="tests.messaging.compose.alias"):
        (handle_id,) = Recipient._base_manager.filter(message=message).values_list("handle_id", flat=True)
    assert handle_id != mailbox.pk
    assert _wire(desk, message)["To"] == "a.b+promo@gmail.com"


def test_inbound_mail_from_another_letter_case_of_a_contacts_address_lands_on_the_contacts_handle(desk: Any) -> None:
    raw = (
        b"From: Client <client@example.test>\r\nTo: desk@example.test\r\nMessage-ID: <spelled@example.test>\r\n"
        b"Subject: Hello\r\n\r\nbody\r\n"
    )
    with system_context(reason="tests.messaging.compose.inbound"):
        contact = Handle.objects.upsert(platform="email", value="Client@Example.test")
        (inbound,) = Message.objects.ingest([parse_message(raw, mailbox="INBOX", uid=1, uidvalidity=1)], channel=desk)
    assert inbound.sender_id == contact.pk


def test_after_the_key_repair_folds_two_spellings_a_compose_lands_on_the_older_and_keeps_its_spelling(
    desk: Any,
) -> None:
    """Rows an earlier rule kept apart are one handle after the repair: the survivor's spelling is on the wire."""

    with system_context(reason="tests.messaging.compose.repair"):
        older, newer = Handle._base_manager.bulk_create(
            Handle(
                platform="email", value=value, normalized_value=Handle.normalize_value("email", value),
                normalized_version=1, spelling_key="", created_by=desk.owner,
            )
            for value in ("Ada@Example.test", "ada@example.test")
        )
    assert Handle.objects.renormalize_handles().folded == 1

    message = Message.objects.compose(channel=desk, to=["ADA@example.test"], text="Hi.", actor=desk.owner)
    with system_context(reason="tests.messaging.compose.repair"):
        assert list(Recipient._base_manager.filter(message=message).values_list("handle_id", flat=True)) == [older.pk]
        assert Handle._base_manager.get(pk=newer.pk).merged_into_id == older.pk
    assert _wire(desk, message)["To"] == "Ada@Example.test"
