"""Outbound email composes Django's native mailer lifecycle without real I/O."""

from email.message import EmailMessage
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from uuid import UUID

import pytest
from anymail.message import AnymailMessage
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.checks import Tags, run_checks
from django.core.exceptions import ValidationError
from django.test import override_settings
from rebac import PermissionDenied, actor_context, system_context

from angee.integrate.errors import IntegrationError
from angee.messaging.backends import (
    OutboundEmailChannelMissing,
    OutboundEmailNotConfigured,
    ParsedHandle,
    ParsedMessage,
    ParsedPart,
    ParsedThread,
)
from angee.messaging.constants import TRANSPORT_MAILER_ALIAS
from angee.messaging.email import AnymailEmailChannelBackend, SystemChannelBackend, carries_address
from angee.messaging.testing.models import Channel, Fragment, Handle, Message, Part
from angee.messaging_integrate_imap.parser import parse_message
from tests.conftest import Vendor, make_integration

pytestmark = pytest.mark.filterwarnings("error::django.utils.deprecation.RemovedInDjango2028Warning")

LOCMEM = {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}
TRANSPORT = {"default": LOCMEM, "transport": LOCMEM}
"""Delivery configured: the test mailer is the transport mailer messaging sends through."""
NO_TRANSPORT = {"default": LOCMEM}
"""Delivery not configured: no transport mailer is stated."""


@override_settings(MAILERS=TRANSPORT)
def test_the_transport_mailer_delivers_an_anymail_message_without_legacy_api(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = AnymailEmailChannelBackend(None)
    email = AnymailMessage("Subject", "Body", "sender@example.test", ["reader@example.test"])
    monkeypatch.setattr(transport, "email_message", lambda message: email)
    outcome = transport.deliver(SimpleNamespace(pk="native-mailer"))
    assert outcome.accepted
    delivered = mail.outbox[-1]
    assert delivered.subject == "Subject"
    assert delivered.to == ["reader@example.test"]
    assert delivered.sent_using == "transport"


@override_settings(MAILERS=NO_TRANSPORT)
def test_without_a_transport_mailer_delivery_sends_nothing_through_the_default_mailer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = AnymailEmailChannelBackend(None)
    monkeypatch.setattr(transport, "email_message", lambda message: AnymailMessage(
        "Subject", "Body", "sender@example.test", ["reader@example.test"],
    ))
    with pytest.raises(IntegrationError, match="not configured"):
        transport.deliver(SimpleNamespace(pk="no-transport"))
    assert mail.outbox == []


def test_native_mailer_open_failure_closes_without_entering_send(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = AnymailEmailChannelBackend(None)
    email = AnymailMessage("Subject", "Body", "sender@example.test", ["reader@example.test"])
    monkeypatch.setattr(transport, "email_message", lambda message: email)
    connection = Mock()
    connection.open.side_effect = TimeoutError("Connection never opened")
    monkeypatch.setattr("angee.messaging.email.mailers", {TRANSPORT_MAILER_ALIAS: connection})
    with pytest.raises(IntegrationError) as failure:
        transport.deliver(SimpleNamespace(pk="native-mailer"))
    assert failure.value.transient
    connection.send_messages.assert_not_called()
    connection.close.assert_called_once()


@pytest.fixture
def header_message(db):
    """Retain original metadata and divergent top-level and quoted header Parts."""
    del db
    with system_context(reason="native source-header fixtures"):
        owner = get_user_model().objects.create_user(username="header-owner")
        outsider = get_user_model().objects.create_user(username="header-outsider")
        vendor = Vendor.objects.create(slug="source-headers", display_name="Source headers")
        channel = Channel.objects.create(vendor=vendor, owner=owner, backend_class="manual")
        message = Message.objects.create(channel=channel, platform="email", metadata={
            "headers": {"FrOm": ["Original <original@example.test>"]},
        })
        for position, name, text in (
            (0, "From", "Top Level <top@example.test>"),
            (1, "from", "second@example.test"),
            (2, "To", "Recipient <recipient@example.test>"),
        ):
            Part.objects.create(message=message, role="header", name=name, position=position,
                                fragment=Fragment.objects.upsert(text=text))
        quoted = Part.objects.create(message=message, role="quoted", type="message/rfc822", position=3)
        Part.objects.create(message=message, parent=quoted, role="header", name="From",
                            fragment=Fragment.objects.upsert(text="quoted@example.test"))
    return owner, outsider, message


def test_source_headers_prefer_original_metadata_and_preserve_repeated_values(header_message):
    owner, _outsider, message = header_message
    message.metadata = {"headers": {
        "From": ["First <first@example.test>", "First <first@example.test>"],
        "fRoM": "second@example.test",
    }}
    assert message.source_header_values("FROM", actor=owner) == (
        "First <first@example.test>", "First <first@example.test>", "second@example.test",
    )


@pytest.mark.parametrize("value, expected", [
    (17, ()), (None, ()), ({"address": "invented@example.test"}, ()),
    (["approved@example.test", 17], ()),
    (["", "malformed <", "last@example.test"], ("", "malformed <", "last@example.test")),
])
def test_malformed_original_header_never_falls_back_to_a_different_part(header_message, value, expected):
    owner, _outsider, message = header_message
    message.metadata = {"headers": {"From": value}}
    assert message.source_header_values("from", actor=owner) == expected


@pytest.mark.parametrize("metadata", [{}, {"headers": {"Subject": "Only subject"}}, ["legacy metadata"]])
def test_source_headers_fall_back_to_top_level_parts_with_pinned_reader(header_message, metadata):
    owner, _outsider, message = header_message
    message.metadata = metadata
    assert message.with_actor(owner).source_header_values("from") == (
        "Top Level <top@example.test>", "second@example.test",
    )


def test_source_header_values_require_message_read_for_metadata_and_parts(header_message):
    _owner, outsider, message = header_message
    for metadata in (message.metadata, {}):
        message.metadata = metadata
        with pytest.raises(PermissionDenied):
            message.source_header_values("From", actor=outsider)


def test_outbound_email_envelope_uses_canonical_source_header_fallback(header_message):
    owner, _outsider, message = header_message
    transport = AnymailEmailChannelBackend(message.channel)
    with actor_context(owner):
        rendered = transport.email_message(message)
    assert rendered.from_email == "Original <original@example.test>"
    assert rendered.to == ["Recipient <recipient@example.test>"]
    assert rendered.cc == [] and rendered.bcc == []


@override_settings(MAILERS=TRANSPORT)
def test_outbound_email_keeps_native_sender_over_source_fallback(header_message):
    """A sender handle the channel does not configure keeps its own address, ahead of the source's From."""

    owner, _outsider, message = header_message
    with actor_context(owner):
        handle = Handle.objects.create(platform="email", value="live@example.test")
        message.sender = handle
    transport = AnymailEmailChannelBackend(message.channel)
    with actor_context(owner):
        assert transport._sender(message) == "live@example.test"


def test_source_header_fallback_preserves_an_explicit_system_reader(header_message):
    _owner, _outsider, message = header_message
    with system_context(reason="native email rendering source read"):
        assert message.source_header_values("to") == ("Recipient <recipient@example.test>",)


@override_settings(MAILERS=TRANSPORT, DEFAULT_FROM_EMAIL="Default <default@example.test>")
@pytest.mark.parametrize(
    "config,expected",
    [
        ({"from_email": "Billing <billing@example.test>"}, ("Billing", "billing@example.test")),
        ({}, ("Default", "default@example.test")),
        (None, ("Default", "default@example.test")),
    ],
)
def test_default_sender_is_one_validated_identity(config: object, expected: tuple[str, str]) -> None:
    """The channel's configured sender wins over the deployment default, and the envelope reuses it."""

    backend = AnymailEmailChannelBackend(SimpleNamespace(config=config))
    assert backend.outbound_sender() == expected
    unaddressed = SimpleNamespace(sender_id=None, source_header_values=lambda _name: ())
    assert backend._sender(unaddressed) == f"{expected[0]} <{expected[1]}>"


@override_settings(MAILERS=TRANSPORT)
def test_default_sender_refuses_an_invalid_configured_address() -> None:
    backend = AnymailEmailChannelBackend(SimpleNamespace(config={"from_email": "invalid"}))
    with pytest.raises(ValidationError, match="Configure a valid sender address"):
        backend.outbound_sender()


@override_settings(MAILERS=TRANSPORT, DEFAULT_FROM_EMAIL="Jörg <jörg@example.test>")
def test_a_default_sender_the_transport_cannot_carry_is_refused_before_any_mail_is_written() -> None:
    """The mailbox writer's rule refuses the sender from settings, where the delivery would otherwise fail."""

    for backend in (AnymailEmailChannelBackend, SystemChannelBackend):
        with pytest.raises(ValidationError) as refused:
            backend.default_sender()
        assert [error.code for error in refused.value.error_dict["channel"]] == ["cannot_carry"]
    assert not SystemChannelBackend.system_mail_configured()
    assert not carries_address("jörg@example.test") and carries_address("ada@exämple.test")


@override_settings(MAILERS=NO_TRANSPORT)
def test_default_sender_requires_a_configured_transport() -> None:
    backend = AnymailEmailChannelBackend(SimpleNamespace(config={"from_email": "sender@example.test"}))
    with pytest.raises(OutboundEmailNotConfigured) as refused:
        backend.outbound_sender()
    assert refused.value.code == "OUTBOUND_EMAIL_NOT_CONFIGURED"
    assert refused.value.message_dict == {"channel": ["Outbound email delivery is not configured."]}


def test_outbound_email_admits_every_connected_backend_declaring_configured_email(db: None) -> None:
    """Email and IMAP channels qualify while email is configured; paused and non-email channels never do."""

    del db
    billing = make_integration(
        "billing",
        model=Channel,
        backend_class="email",
        config={"from_email": "Billing <billing@example.test>"},
    )
    mailbox = make_integration("mailbox", model=Channel, backend_class="imap", config={"host": "192.0.2.10"})
    make_integration("manual-chat", model=Channel, backend_class="manual")
    paused = make_integration("paused-mail", model=Channel, backend_class="email")
    with system_context(reason="test.messaging.outbound_email"):
        paused.pause()
        with override_settings(MAILERS=TRANSPORT):
            assert set(Channel.objects.outbound_email()) == {billing, mailbox}
            assert billing.outbound_sender() == ("Billing", "billing@example.test")
            with pytest.raises(OutboundEmailChannelMissing):
                paused.outbound_sender()


def test_outbound_email_names_why_its_selection_is_empty(db: None) -> None:
    """An empty selection is refused with a coded reason: no transport, or no connected channel."""

    del db
    billing = make_integration("billing", model=Channel, backend_class="email")
    mailbox = make_integration("mailbox", model=Channel, backend_class="imap", config={"host": "192.0.2.10"})
    paused = make_integration("paused-mail", model=Channel, backend_class="email")
    with system_context(reason="test.messaging.outbound_email"):
        paused.pause()
        with override_settings(MAILERS=TRANSPORT):
            assert set(Channel.objects.require_outbound_email(backend_class="email")) == {billing}
            assert not Channel.objects.filter(pk=paused.pk).outbound_email(backend_class="email").exists()
            assert not Channel.objects.outbound_email(backend_class="manual").exists()
            with pytest.raises(OutboundEmailNotConfigured):
                Channel.objects.require_outbound_email(backend_class="manual")
            with pytest.raises(OutboundEmailChannelMissing) as missing:
                Channel.objects.filter(pk=paused.pk).require_outbound_email(backend_class="email")
            with pytest.raises(OutboundEmailChannelMissing):
                Channel.objects.filter(pk=mailbox.pk).require_outbound_email(backend_class="email")
        with override_settings(MAILERS=NO_TRANSPORT):
            assert not Channel.objects.outbound_email().exists()
            with pytest.raises(OutboundEmailNotConfigured) as off:
                Channel.objects.require_outbound_email()
    assert missing.value.code == "OUTBOUND_EMAIL_CHANNEL_MISSING"
    assert missing.value.message_dict == {"channel": ["Select a connected email channel."]}
    assert off.value.code == "OUTBOUND_EMAIL_NOT_CONFIGURED"


def test_a_system_only_channel_is_never_selected_to_send_from(db: None) -> None:
    """The system channel delivers email, yet nobody chooses it: every outbound selection leaves it out."""

    del db
    billing = make_integration("billing", model=Channel, backend_class="email")
    system = make_integration("system-mail", model=Channel, backend_class="system")
    with system_context(reason="test.messaging.outbound_email"), override_settings(MAILERS=TRANSPORT):
        assert billing.lifecycle == system.lifecycle == Channel.Lifecycle.CONNECTED
        assert set(Channel.objects.outbound_email()) == {billing}
        assert set(Channel.objects.require_outbound_email(backend_class="email")) == {billing}
        assert not Channel.objects.outbound_email(backend_class="system").exists()
        with pytest.raises(OutboundEmailNotConfigured):
            Channel.objects.require_outbound_email(backend_class="system")


@override_settings(MAILERS=TRANSPORT)
def test_outbound_email_predicate_is_lazy_and_chainable() -> None:
    # No database fixture: constructing either selection must not execute SQL.
    selected = Channel.objects.outbound_email(backend_class="email").filter(pk=41)
    assert selected.model is Channel
    assert selected._result_cache is None
    assert Channel.objects.outbound_email(backend_class="manual")._result_cache is None



def _mail(
    *,
    message_id: str,
    subject: str,
    sender: str | None,
    body: str,
    reply_to: str = "",
    in_reply_to: str = "",
    references: str = "",
) -> bytes:
    """Write a plain RFC 822 message to the desk the way a mail client sends it."""

    message = EmailMessage()
    if sender is not None:
        message["From"] = sender
    if reply_to:
        message["Reply-To"] = reply_to
    message["To"] = "Desk <desk@example.test>"
    message["Subject"] = subject
    message["Message-ID"] = message_id
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = references
    message.set_content(body)
    return message.as_bytes()


def _receive(channel: Any, raw: bytes, *, uid: int) -> Any:
    """Land one received message through the email parser and messaging's ingest."""

    with system_context(reason="tests.messaging.email_reply.receive"):
        (message,) = Message.objects.ingest(
            [parse_message(raw, mailbox="INBOX", uid=uid, uidvalidity=1)],
            channel=channel,
        )
    return message


@pytest.fixture
def mail_desk(db: None, settings: Any) -> tuple[Any, Any]:
    """An email channel whose default sender is the desk, with delivery configured, and one request it received."""

    del db
    settings.MAILERS = TRANSPORT
    channel = make_integration(
        "reply-desk", model=Channel, backend_class="email", config={"from_email": "Desk <desk@example.test>"},
    )
    request = _receive(
        channel,
        _mail(
            message_id="<request@example.test>",
            subject="Re: Access request",
            sender="Requester <requester@example.test>",
            body="Please add me to the project.",
        ),
        uid=1,
    )
    return channel, request


def test_an_email_reply_is_addressed_titled_and_threaded_so_the_answer_lands_on_its_conversation(mail_desk):
    channel, request = mail_desk
    reply = Message.objects.compose(parent=request, text="Which team are you on?", actor=channel.owner)
    assert "@" in reply.external_id and "<" not in reply.external_id
    with system_context(reason="tests.messaging.email_reply.render"):
        assert list(reply.recipients.values_list("handle_id", "role")) == [(request.sender_id, "to")]
        # The thread title is the normalised subject, so the reply's prefix never stacks.
        assert reply.title() == "Re: Access request"
        wire = channel.backend.email_message(reply).message()
    assert (wire["From"], wire["To"]) == ("Desk <desk@example.test>", "Requester <requester@example.test>")
    assert wire["Subject"] == "Re: Access request"
    assert wire["Message-ID"] == f"<{reply.external_id}>"
    assert wire["In-Reply-To"] == wire["References"] == "<request@example.test>"

    # The requester answers under a subject of their own, so only the threading headers can place it.
    answer = _receive(
        channel,
        _mail(
            message_id="<answer@example.test>",
            subject="Our team",
            sender="Requester <requester@example.test>",
            in_reply_to=wire["Message-ID"],
            references=f"{wire['References']} {wire['Message-ID']}",
            body="The platform team.",
        ),
        uid=2,
    )
    assert (answer.thread_id, answer.parent_id) == (request.thread_id, reply.pk)
    assert reply.thread_id == request.thread_id

    # A provider that rewrites the reply's Message-ID leaves References naming the request.
    rewritten = "<rewritten@provider.example.test>"
    follow_up = _receive(
        channel,
        _mail(
            message_id="<follow-up@example.test>",
            subject="One more thing",
            sender="Requester <requester@example.test>",
            in_reply_to=rewritten,
            references=f"{wire['References']} {rewritten}",
            body="And the data team.",
        ),
        uid=3,
    )
    assert follow_up.thread_id == request.thread_id


def test_an_email_reply_requires_a_parent_reply_to_or_sender_to_address(mail_desk):
    channel, _request = mail_desk
    unsigned = _receive(
        channel,
        _mail(
            message_id="<unsigned@example.test>",
            subject="No sender",
            sender=None,
            body="Who sent this?",
        ),
        uid=4,
    )
    with pytest.raises(ValidationError) as refused:
        Message.objects.compose(parent=unsigned, text="Who are you?", actor=channel.owner)
    assert refused.value.error_dict["parent"][0].code == "reply_unaddressed"
    assert not Message._base_manager.filter(parent=unsigned).exists()


def test_an_email_reply_goes_to_the_parents_reply_to_over_its_sender(mail_desk):
    channel, _request = mail_desk
    relayed = _receive(
        channel,
        _mail(
            message_id="<relayed@example.test>",
            subject="Form submission",
            sender="Forms Relay <relay@example.test>",
            reply_to="Applicant <Applicant@Example.test>",
            body="Submitted on behalf of the applicant.",
        ),
        uid=5,
    )
    reply = Message.objects.compose(parent=relayed, text="Thanks, which start date?", actor=channel.owner)
    with system_context(reason="tests.messaging.email_reply.reply_to"):
        (addressee,) = Handle.objects.filter(recipient_rows__message=reply, recipient_rows__role="to")
        wire = channel.backend.email_message(reply).message()
    # The address lands, as the Reply-To spells it, on the handle a received message from it would use.
    assert (addressee.value, addressee.display_name) == ("Applicant@Example.test", "Applicant")
    assert addressee.pk != relayed.sender_id
    assert wire["To"] == "Applicant <Applicant@Example.test>"


def test_reply_addresses_parse_the_reply_to_in_order_once_per_address_with_display_names(mail_desk):
    channel, request = mail_desk
    listed = _receive(
        channel,
        _mail(
            message_id="<listed@example.test>",
            subject="List post",
            sender="List <list@example.test>",
            reply_to='"Doe, Jane" <Jane.Doe@Example.test>, bob@EXAMPLE.test, Jane <jane.doe@example.test>',
            body="Posted to the list.",
        ),
        uid=6,
    )
    addresses = listed.reply_addresses(actor=channel.owner)
    # An address repeated in any letter case keeps its first spelling and display name; order follows the header.
    assert list(addresses.items()) == [("Jane.Doe@Example.test", "Doe, Jane"), ("bob@EXAMPLE.test", "")]
    # Without a Reply-To there is nothing: falling back to the sender is the caller's decision.
    assert request.reply_addresses(actor=channel.owner) == {}
    with system_context(reason="tests.messaging.reply_addresses.stranger"):
        stranger = get_user_model().objects.create_user(username="reply-addresses-stranger")
    with pytest.raises(PermissionDenied):
        listed.reply_addresses(actor=stranger)


def test_retained_identity_and_threading_header_parts_never_render_beside_the_owned_ones(mail_desk):
    channel, request = mail_desk
    reply = Message.objects.compose(parent=request, text="Which team?", actor=channel.owner)
    with system_context(reason="tests.messaging.email_reply.headers"):
        for position, name in enumerate(("message-id", "in-reply-to", "references"), start=10):
            Part.objects.create(
                message=reply,
                role="header",
                name=name,
                position=position,
                fragment=Fragment.objects.upsert(text="<forged@example.test>"),
            )
        headers = channel.backend.email_message(reply).extra_headers
    assert sorted(headers) == ["In-Reply-To", "Message-ID", "References", "X-Angee-External-ID"]
    assert "<forged@example.test>" not in headers.values()


def test_a_reply_of_another_kind_keeps_its_client_token_and_writes_no_envelope(db: None) -> None:
    del db
    channel = make_integration("reply-chat", model=Channel, backend_class="manual")
    with system_context(reason="tests.messaging.chat_reply"):
        (question,) = Message.objects.ingest(
            [
                ParsedMessage(
                    external_id="room/1",
                    platform="slack",
                    subject="Standup",
                    sender=ParsedHandle(platform="slack", value="U-asker"),
                    thread=ParsedThread(external_id="room"),
                    body=ParsedPart(text="Ready?"),
                )
            ],
            channel=channel,
        )
        desk = Handle.objects.upsert(platform="slack", value="U-desk")
    assert question.message_type == Message.MessageKind.CHAT
    reply = Message.objects.compose(parent=question, text="Yes.", sender=desk, actor=channel.owner)
    UUID(reply.external_id)
    with system_context(reason="tests.messaging.chat_reply.read"):
        assert not reply.recipients.exists()
        assert list(reply.parts.values_list("role", flat=True)) == ["body"]


@pytest.mark.parametrize(
    ("transport", "reported"),
    [
        pytest.param(LOCMEM, True, id="locmem"),
        pytest.param({"BACKEND": "django.core.mail.backends.console.EmailBackend"}, True, id="console"),
        pytest.param({"BACKEND": "django.core.mail.backends.smtp.EmailBackend"}, False, id="smtp"),
        pytest.param(None, False, id="no-transport"),
    ],
)
def test_the_deploy_check_reports_a_development_transport(settings: Any, transport: Any, reported: bool) -> None:
    settings.MAILERS = {"default": LOCMEM, **({"transport": transport} if transport is not None else {})}
    deploy = {message.id for message in run_checks(include_deployment_checks=True, tags=[Tags.mail])}
    assert ("messaging.E004" in deploy) is reported
    assert "messaging.E004" not in {message.id for message in run_checks(tags=[Tags.mail])}
