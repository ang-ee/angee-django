"""Outbound email composes Django's native mailer lifecycle without real I/O."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from anymail.message import AnymailMessage
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import override_settings

from angee.integrate.errors import IntegrationError
from angee.messaging.email import AnymailEmailChannelBackend

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


@override_settings(ANGEE_EMAIL_DELIVERY_CONFIGURED=True, DEFAULT_FROM_EMAIL="Default <default@example.test>")
@pytest.mark.parametrize("config,expected", [
    ({"from_email": "Billing <billing@example.test>"}, ("Billing", "billing@example.test")),
    ({}, ("Default", "default@example.test")),
    (None, ("Default", "default@example.test")),
])
def test_default_sender_uses_one_native_validated_identity(config, expected):
    backend = AnymailEmailChannelBackend(SimpleNamespace(config=config))
    assert backend.outbound_sender() == expected
    assert backend._sender(SimpleNamespace(sender_id=None, metadata={}), []) == f"{expected[0]} <{expected[1]}>"


@override_settings(ANGEE_EMAIL_DELIVERY_CONFIGURED=True)
def test_default_sender_refuses_invalid_configured_address():
    backend = AnymailEmailChannelBackend(SimpleNamespace(config={"from_email": "invalid"}))
    with pytest.raises(ValidationError, match="Configure a valid sender address"):
        backend.outbound_sender()


@override_settings(ANGEE_EMAIL_DELIVERY_CONFIGURED=False)
def test_default_sender_requires_configured_transport():
    backend = AnymailEmailChannelBackend(SimpleNamespace(config={"from_email": "sender@example.test"}))
    with pytest.raises(ValidationError, match="not configured"):
        backend.outbound_sender()
