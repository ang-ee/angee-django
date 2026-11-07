"""Outbound email composes Django's native mailer lifecycle without real I/O."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from anymail.message import AnymailMessage
from django.core import mail
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
