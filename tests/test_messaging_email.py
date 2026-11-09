"""Outbound email composes Django's native mailer lifecycle without real I/O."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from anymail.message import AnymailMessage
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import override_settings
from rebac import PermissionDenied, actor_context, system_context

from angee.integrate.errors import IntegrationError
from angee.messaging.backends import OutboundEmailChannelMissing, OutboundEmailNotConfigured
from angee.messaging.email import AnymailEmailChannelBackend
from angee.messaging.testing.models import Channel, Fragment, Handle, Message, Part
from tests.conftest import Vendor, make_integration

pytestmark = pytest.mark.filterwarnings("error::django.utils.deprecation.RemovedInDjango2028Warning")


@override_settings(
    ANGEE_EMAIL_DELIVERY_CONFIGURED=True,
    MAILERS={"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}},
)
def test_native_default_mailer_delivers_anymail_message_without_legacy_api(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = AnymailEmailChannelBackend(None)
    email = AnymailMessage("Subject", "Body", "sender@example.test", ["reader@example.test"])
    monkeypatch.setattr(transport, "email_message", lambda message: email)
    outcome = transport.deliver(SimpleNamespace(pk="native-mailer"))
    assert outcome.accepted
    delivered = mail.outbox[-1]
    assert delivered.subject == "Subject"
    assert delivered.to == ["reader@example.test"]
    assert delivered.sent_using == "default"


@override_settings(ANGEE_EMAIL_DELIVERY_CONFIGURED=True)
def test_native_mailer_open_failure_closes_without_entering_send(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = AnymailEmailChannelBackend(None)
    email = AnymailMessage("Subject", "Body", "sender@example.test", ["reader@example.test"])
    monkeypatch.setattr(transport, "email_message", lambda message: email)
    connection = Mock()
    connection.open.side_effect = TimeoutError("Connection never opened")
    monkeypatch.setattr("angee.messaging.email.mailers", SimpleNamespace(default=connection))
    with pytest.raises(IntegrationError) as failure:
        transport.deliver(SimpleNamespace(pk="native-mailer"))
    assert failure.value.transient
    connection.send_messages.assert_not_called()
    connection.close.assert_called_once()


@pytest.fixture
def header_message(composed_tables):
    """Retain original metadata and divergent top-level and quoted header Parts."""
    del composed_tables
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


def test_outbound_email_keeps_native_sender_over_source_fallback(header_message):
    owner, _outsider, message = header_message
    with actor_context(owner):
        handle = Handle.objects.create(platform="email", value="live@example.test")
        message.sender = handle
    transport = AnymailEmailChannelBackend(message.channel)
    with actor_context(owner):
        assert transport._sender(message, []) == "live@example.test"


def test_source_header_fallback_preserves_an_explicit_system_reader(header_message):
    _owner, _outsider, message = header_message
    with system_context(reason="native email rendering source read"):
        assert message.source_header_values("to") == ("Recipient <recipient@example.test>",)


@override_settings(ANGEE_EMAIL_DELIVERY_CONFIGURED=True, DEFAULT_FROM_EMAIL="Default <default@example.test>")
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
    assert backend._sender(unaddressed, []) == f"{expected[0]} <{expected[1]}>"


@override_settings(ANGEE_EMAIL_DELIVERY_CONFIGURED=True)
def test_default_sender_refuses_an_invalid_configured_address() -> None:
    backend = AnymailEmailChannelBackend(SimpleNamespace(config={"from_email": "invalid"}))
    with pytest.raises(ValidationError, match="Configure a valid sender address"):
        backend.outbound_sender()


@override_settings(ANGEE_EMAIL_DELIVERY_CONFIGURED=False)
def test_default_sender_requires_a_configured_transport() -> None:
    backend = AnymailEmailChannelBackend(SimpleNamespace(config={"from_email": "sender@example.test"}))
    with pytest.raises(OutboundEmailNotConfigured) as refused:
        backend.outbound_sender()
    assert refused.value.code == "OUTBOUND_EMAIL_NOT_CONFIGURED"
    assert refused.value.message_dict == {"channel": ["Outbound email delivery is not configured."]}


@pytest.mark.django_db(transaction=True)
def test_outbound_email_admits_every_connected_backend_declaring_configured_email(composed_tables: None) -> None:
    """Email and IMAP channels qualify while email is configured; paused and non-email channels never do."""

    del composed_tables
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
        with override_settings(ANGEE_EMAIL_DELIVERY_CONFIGURED=True):
            assert set(Channel.objects.outbound_email()) == {billing, mailbox}
            assert billing.outbound_sender() == ("Billing", "billing@example.test")
            with pytest.raises(OutboundEmailChannelMissing):
                paused.outbound_sender()


@pytest.mark.django_db(transaction=True)
def test_outbound_email_names_why_its_selection_is_empty(composed_tables: None) -> None:
    """An empty selection is refused with a coded reason: no transport, or no connected channel."""

    del composed_tables
    billing = make_integration("billing", model=Channel, backend_class="email")
    mailbox = make_integration("mailbox", model=Channel, backend_class="imap", config={"host": "192.0.2.10"})
    paused = make_integration("paused-mail", model=Channel, backend_class="email")
    with system_context(reason="test.messaging.outbound_email"):
        paused.pause()
        with override_settings(ANGEE_EMAIL_DELIVERY_CONFIGURED=True):
            assert set(Channel.objects.outbound_email(backend_class="email")) == {billing}
            with pytest.raises(OutboundEmailNotConfigured):
                Channel.objects.outbound_email(backend_class="manual")
            with pytest.raises(OutboundEmailChannelMissing) as missing:
                Channel.objects.filter(pk=paused.pk).outbound_email(backend_class="email")
            with pytest.raises(OutboundEmailChannelMissing):
                Channel.objects.filter(pk=mailbox.pk).outbound_email(backend_class="email")
        with override_settings(ANGEE_EMAIL_DELIVERY_CONFIGURED=False), pytest.raises(OutboundEmailNotConfigured) as off:
            Channel.objects.outbound_email()
    assert missing.value.code == "OUTBOUND_EMAIL_CHANNEL_MISSING"
    assert missing.value.message_dict == {"channel": ["Select a connected email channel."]}
    assert off.value.code == "OUTBOUND_EMAIL_NOT_CONFIGURED"
