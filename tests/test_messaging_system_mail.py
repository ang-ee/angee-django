"""Django's own mail is recorded on the system channel and sent once through the one transport mailer."""

from __future__ import annotations

import email.policy
import logging
import logging.config
from collections.abc import Iterator
from contextlib import contextmanager
from email import message_from_bytes
from typing import Any

import pytest
from anymail.message import AnymailMessage
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage, EmailMultiAlternatives, send_mail
from django.core.mail.backends.base import BaseEmailBackend
from django.db import transaction
from django.test import override_settings
from django.utils.log import AdminEmailHandler
from rebac import actor_context, system_context, to_subject_ref
from rebac.models import PermissionAuditEvent, active_relationship_model

from angee.jobs.enqueue import celery_app
from angee.messaging.backends import OutboundEmailChannelMissing, OutboundEmailNotConfigured, SystemChannelMissing
from angee.messaging.constants import DELIVER_MESSAGE_TASK, SYSTEM_CHANNEL_BACKEND, SYSTEM_MAILER_BACKEND
from angee.messaging.email import SystemChannelBackend, SystemMailer
from angee.messaging.managers import _OUTGOING_ACTOR
from angee.messaging.tasks import deliver_message
from angee.messaging.testing.models import Channel, Handle, Message, Part, Recipient, Thread
from angee.provenance.context import changeset, changeset_model
from angee.resources.testing.models import Resource
from tests.conftest import Vendor
from tests.messaging_campaign import make_user

pytestmark = pytest.mark.filterwarnings("error::django.utils.deprecation.RemovedInDjango2028Warning")

LOCMEM = {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}
SYSTEM = {"BACKEND": SYSTEM_MAILER_BACKEND}
SENDER = "Angee <noreply@example.test>"


def _install() -> Any:
    """Load messaging's shipped rows the system channel needs: its vendor, then the channel."""

    with system_context(reason="tests.messaging.system_mail.install"):
        Resource.objects.load_xref("messaging.email", model=Vendor)
        return Resource.objects.load_xref("messaging.system_channel", model=Channel)


@pytest.fixture
def system_channel(db: None, settings: Any) -> Any:
    """The installed system channel, with a transport mailer and a valid default sender."""

    del db
    settings.MAILERS = {"transport": LOCMEM}
    settings.DEFAULT_FROM_EMAIL = SENDER
    return _install()


def test_the_installed_system_channel_is_ownerless_grant_free_connected_and_found_by_its_one_lookup(
    system_channel: Any,
) -> None:
    found = Channel.objects.system_channel()
    assert found == system_channel
    with system_context(reason="tests.messaging.system_mail.row"):
        row = Channel._base_manager.select_related("vendor").get(pk=found.pk)
    assert (row.backend_class, row.owner_id, row.lifecycle, row.vendor.slug) == (
        SYSTEM_CHANNEL_BACKEND, None, Channel.Lifecycle.CONNECTED, "email",
    )
    assert row.config in ({}, None)
    stored = active_relationship_model().objects.filter(resource_type="messaging/channel", resource_id=str(row.pk))
    assert not stored.exists()
    # A renamed channel keeps its name when the shipped rows load again.
    with system_context(reason="tests.messaging.system_mail.rename"):
        Channel._base_manager.filter(pk=row.pk).update(display_name="Platform mail")
    assert _install().pk == row.pk
    with system_context(reason="tests.messaging.system_mail.row"):
        assert Channel._base_manager.get(pk=row.pk).display_name == "Platform mail"


def test_a_missing_system_channel_is_refused_with_its_code(db: None) -> None:
    del db
    with pytest.raises(SystemChannelMissing) as missing:
        Channel.objects.system_channel()
    assert isinstance(missing.value, ValidationError)
    assert missing.value.code == "SYSTEM_CHANNEL_MISSING"
    assert [error.code for error in missing.value.error_dict["channel"]] == ["SYSTEM_CHANNEL_MISSING"]


@pytest.mark.parametrize(
    ("mailers", "sender", "configured"),
    [
        pytest.param({"transport": LOCMEM}, SENDER, True, id="transport-and-sender"),
        pytest.param({"default": LOCMEM}, SENDER, False, id="no-transport"),
        pytest.param({}, SENDER, False, id="no-mailers"),
        pytest.param({"transport": LOCMEM}, "not an address", False, id="invalid-sender"),
        pytest.param({"transport": LOCMEM}, "Angee <noreply@exa mple.test>", False, id="sender-the-handle-refuses"),
    ],
)
def test_system_mail_is_configured_exactly_when_the_system_channel_has_a_sender(
    system_channel: Any, mailers: dict[str, Any], sender: str, configured: bool,
) -> None:
    """The settings-only predicate and the connected channel's sender answer alike."""

    with override_settings(MAILERS=mailers, DEFAULT_FROM_EMAIL=sender):
        assert SystemChannelBackend.system_mail_configured() is configured
        if configured:
            sender = ("Angee", "noreply@example.test")
            assert system_channel.outbound_sender() == SystemChannelBackend.default_sender() == sender
        else:
            with pytest.raises(ValidationError) as refused:
                system_channel.outbound_sender()
            assert "channel" in refused.value.error_dict


def _rows() -> tuple[int, ...]:
    return tuple(model._base_manager.count() for model in (Message, Part, Recipient, Thread, Handle))


@contextmanager
def _refused(expected: type[Exception], *, key: str | None = None, code: str | None = None) -> Iterator[Any]:
    """Expect a refusal that wrote no row and no permission audit row."""

    rows, audits = _rows(), PermissionAuditEvent.objects.count()
    with pytest.raises(expected) as refused:
        yield refused
    assert (_rows(), PermissionAuditEvent.objects.count()) == (rows, audits)
    if key is not None:
        assert isinstance(refused.value, ValidationError)
        assert key in refused.value.error_dict, refused.value
        if code is not None:
            assert refused.value.error_dict[key][0].code == code


def test_a_blank_reason_is_refused(system_channel: Any) -> None:
    for reason in ("", "   "):
        with _refused(ValueError):
            Message.objects.compose_as_system(reason=reason, to=["reader@example.test"], text="Hello.")


@pytest.mark.parametrize(
    ("values", "key", "code"),
    [
        pytest.param({"to": ["not an address"]}, "to", "invalid", id="invalid-to"),
        pytest.param({"cc": ["a<b@example.test"]}, "cc", "invalid", id="re-read-cc"),
        pytest.param({"to": []}, "to", "required", id="no-recipient"),
        pytest.param({"text": "", "html": ""}, "text", "required", id="empty-body"),
        pytest.param({"subject": "One\nTwo"}, "subject", "invalid", id="two-line-subject"),
        pytest.param({"bcc": [f"r{index}@example.test" for index in range(51)]}, "bcc", "too_many", id="too-many"),
    ],
)
def test_compose_refusals_come_first_and_write_nothing(
    system_channel: Any, values: dict[str, Any], key: str, code: str,
) -> None:
    arguments = {"to": ["reader@example.test"], "text": "Hello.", **values}
    with _refused(ValidationError, key=key, code=code):
        Message.objects.compose_as_system(reason="tests.system_mail", **arguments)


@pytest.mark.parametrize(
    ("mailers", "sender", "code"),
    [
        pytest.param({"default": LOCMEM}, SENDER, "OUTBOUND_EMAIL_NOT_CONFIGURED", id="no-transport"),
        pytest.param({"transport": LOCMEM}, "not an address", "invalid", id="invalid-default-sender"),
    ],
)
def test_unconfigured_system_mail_is_refused_from_settings_before_the_lookup(
    system_channel: Any, mailers: dict[str, Any], sender: str, code: str,
) -> None:
    with override_settings(MAILERS=mailers, DEFAULT_FROM_EMAIL=sender), _refused(ValidationError, key="channel",
                                                                                 code=code):
        Message.objects.compose_as_system(reason="tests.system_mail", to=["reader@example.test"], text="Hello.")


def test_an_absent_or_paused_system_channel_is_refused(db: None, settings: Any) -> None:
    del db
    settings.MAILERS = {"transport": LOCMEM}
    settings.DEFAULT_FROM_EMAIL = SENDER
    rows = _rows()
    with pytest.raises(SystemChannelMissing):
        Message.objects.compose_as_system(reason="tests.system_mail", to=["reader@example.test"], text="Hello.")
    channel = _install()
    with system_context(reason="tests.messaging.system_mail.pause"):
        channel.pause()
    rows = _rows()
    with pytest.raises(OutboundEmailChannelMissing):
        Message.objects.compose_as_system(reason="tests.system_mail", to=["reader@example.test"], text="Hello.")
    assert _rows() == rows


def test_system_mail_records_the_causing_actor_and_lands_its_handles_with_no_creator(system_channel: Any) -> None:
    """The regression of handing the composer a creator's read, write and delete on the handles it lands."""

    member = make_user("system-mail-cause")
    with changeset(fresh=True, change_reason="tests.system_mail.request"), actor_context(member):
        message = Message.objects.compose_as_system(
            reason="tests.system_mail", to=["New Person <new-person@example.test>"], subject="Welcome", text="Hello.",
        )
    assert message.actor() is None
    with system_context(reason="tests.messaging.system_mail.read"):
        row = Message._base_manager.select_related("thread", "sender").get(pk=message.pk)
        (recipient,) = Handle._base_manager.filter(recipient_rows__message=row)
        sender = row.sender
        parts = list(Part._base_manager.filter(message=row))
        def recorded(history: Any) -> set[tuple[str, str]]:
            changesets = set(history.values_list("changeset_id", flat=True))
            return set(changeset_model()._base_manager.filter(pk__in=changesets).values_list(
                "change_reason", "actor_ref",
            ))

        thread_history = recorded(Thread.history.filter(id=row.thread_id))
        handle_history = recorded(Handle.history.filter(id__in=(recipient.pk, sender.pk)))
    assert (row.channel_id, row.status, row.direction) == (system_channel.pk, "queued", "outbound")
    assert (row.created_by_id, row.thread.created_by_id, row.thread.owner_id) == (member.pk, member.pk, None)
    assert (recipient.value, recipient.display_name, recipient.created_by_id) == (
        "new-person@example.test", "New Person", None,
    )
    assert (sender.value, sender.created_by_id) == ("noreply@example.test", None)
    # The caller's changeset holds the conversation under the causing user; the handles, landed under the
    # non-user attribution, are recorded beside them under the same change reason and name no user.
    assert thread_history == {("tests.system_mail.request", str(to_subject_ref(member)))}
    assert handle_history == {("tests.system_mail.request", str(_OUTGOING_ACTOR))}
    for handle in (recipient, sender):
        for action in ("read", "write", "delete"):
            assert not handle.with_actor(member).has_access(action), (handle.value, action)
    for target in (row, row.thread, *parts):
        assert not target.with_actor(member).has_access("read"), target


def test_system_mail_with_no_actor_has_no_creator_anywhere(system_channel: Any) -> None:
    message = Message.objects.compose_as_system(reason="tests.system_mail", to=["solo@example.test"], text="Hello.")
    with system_context(reason="tests.messaging.system_mail.read"):
        row = Message._base_manager.select_related("thread").get(pk=message.pk)
        handles = Handle._base_manager.filter(value__in=("solo@example.test", "noreply@example.test"))
        assert {row.created_by_id, row.thread.created_by_id, *handles.values_list("created_by_id", flat=True)} == {None}


def test_each_call_is_a_new_message_and_no_channel_can_be_named(system_channel: Any) -> None:
    values = {"reason": "tests.system_mail", "to": ["twice@example.test"], "text": "Hello."}
    first, second = (Message.objects.compose_as_system(**values) for _ in range(2))
    assert first.pk != second.pk and first.external_id != second.external_id
    with pytest.raises(TypeError):
        Message.objects.compose_as_system(channel=system_channel, **values)  # type: ignore[call-arg]


@pytest.fixture
def system_mailer(system_channel: Any, settings: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Django's default mailer is the system mailer; the test mailer is the transport; nothing reaches a broker."""

    settings.MAILERS = {"default": SYSTEM, "transport": LOCMEM}
    monkeypatch.setattr(celery_app, "send_task", lambda name, **options: None)
    return system_channel


def _recorded(channel: Any) -> list[Any]:
    with system_context(reason="tests.messaging.system_mail.recorded"):
        return list(Message._base_manager.filter(channel=channel).order_by("pk"))


def _deliver(message: Any) -> Any:
    """Deliver ``message`` as the worker does; return what the transport sent."""

    outcome = deliver_message(message._meta.label_lower, message.pk, message.external_id)
    assert outcome["delivered"], outcome
    return mail.outbox[-1]


def test_a_django_email_is_recorded_on_the_system_channel_and_sent_once_through_the_transport(
    system_mailer: Any,
) -> None:
    assert send_mail("Welcome", "Hello there.", None, ["Reader <reader@example.test>"]) == 1
    assert mail.outbox == []
    (message,) = _recorded(system_mailer)
    with system_context(reason="tests.messaging.system_mail.read"):
        assert (message.status, message.message_type, message.created_by_id) == ("queued", "email", None)
        assert (message.sender.value, message.title()) == ("noreply@example.test", "Welcome")
        assert list(message.recipients.values_list("handle__value", "role")) == [("reader@example.test", "to")]
        assert list(Part._base_manager.filter(message=message, role="body").values_list("type", flat=True)) == [
            "text/plain",
        ]
    sent = _deliver(message)
    wire = sent.message()
    assert sent.sent_using == "transport"
    assert (wire["From"], wire["To"], wire["Subject"]) == (SENDER, "Reader <reader@example.test>", "Welcome")
    assert wire["Message-ID"] == f"<{message.external_id}>"
    # Delivering did not hand the email back to the system mailer.
    assert len(_recorded(system_mailer)) == 1


@pytest.mark.parametrize(
    ("build", "plain", "html"),
    [
        pytest.param(lambda: _alternatives("Text.", "<p>Html.</p>"), "Text.", "<p>Html.</p>", id="text-and-html"),
        pytest.param(lambda: _html_body("<p>Html.</p>"), "", "<p>Html.</p>", id="html-body"),
        pytest.param(lambda: _alternatives("", b"<p>Html.</p>"), "", "<p>Html.</p>", id="html-alternative-only"),
    ],
)
def test_the_bodies_a_django_email_carries_are_the_parts_on_the_wire(
    system_mailer: Any, build: Any, plain: str, html: str,
) -> None:
    assert build().send() == 1
    (message,) = _recorded(system_mailer)
    wire = message_from_bytes(_deliver(message).message().as_bytes(), policy=email.policy.default)
    bodies = {part.get_content_type(): part.get_content().strip() for part in wire.walk() if not part.is_multipart()}
    assert bodies == {key: value for key, value in (("text/plain", plain), ("text/html", html)) if value}


def _alternatives(text: str, html: Any) -> EmailMultiAlternatives:
    email_ = EmailMultiAlternatives("Bodies", text, None, ["reader@example.test"])
    email_.attach_alternative(html, "text/html")
    return email_


def _html_body(html: str) -> EmailMessage:
    email_ = EmailMessage("Bodies", html, None, ["reader@example.test"])
    email_.content_subtype = "html"
    return email_


def _with(**changes: Any) -> Any:
    def build() -> EmailMultiAlternatives:
        email_ = EmailMultiAlternatives("Refused", "Body.", None, ["reader@example.test"])
        for name, value in changes.items():
            if name == "alternatives":
                for content, mimetype in value:
                    email_.attach_alternative(content, mimetype)
            elif name == "attach":
                email_.attach("invoice.txt", b"total", "text/plain")
            else:
                setattr(email_, name, value)
        return email_
    return build


@pytest.mark.parametrize(
    ("build", "refused", "key"),
    [
        pytest.param(_with(attach=True), ValueError, None, id="attachment"),
        pytest.param(_with(reply_to=["other@example.test"]), ValueError, None, id="reply-to"),
        pytest.param(_with(extra_headers={"Reply-To": "other@example.test"}), ValueError, None, id="reply-to-header"),
        pytest.param(_with(extra_headers={"X-Foo": "bar"}), ValueError, None, id="other-header"),
        pytest.param(_with(alternatives=[("BEGIN:VCALENDAR", "text/calendar")]), ValueError, None, id="calendar"),
        pytest.param(_with(alternatives=[("<p>a</p>", "text/html"), ("<p>b</p>", "text/html")]), ValueError, None,
                     id="two-html"),
        pytest.param(_with(content_subtype="html", alternatives=[("<p>b</p>", "text/html")]), ValueError, None,
                     id="html-body-and-alternative"),
        pytest.param(lambda: AnymailMessage("Refused", "Body.", None, ["reader@example.test"]), ValueError, None,
                     id="anymail-message"),
        pytest.param(_with(to=["not an address"]), ValidationError, "to", id="invalid-address"),
        pytest.param(_with(body=""), ValidationError, "text", id="empty-body"),
        pytest.param(_with(subject="One\nTwo"), ValidationError, "subject", id="two-line-subject"),
    ],
)
def test_what_a_django_email_cannot_carry_is_refused_and_nothing_is_recorded(
    system_mailer: Any, build: Any, refused: type[Exception], key: str | None,
) -> None:
    rows = _rows()
    with pytest.raises(refused) as error:
        SystemMailer().send_messages([build()])
    if key is not None:
        assert key in error.value.error_dict
    assert _rows() == rows


def test_a_callers_message_id_date_and_sender_are_not_kept(system_mailer: Any) -> None:
    for _ in range(2):
        EmailMessage(
            "Repeat", "Body.", "Other <other@example.test>", ["reader@example.test"],
            headers={"message-id": "<same@example.test>", "Date": "Mon, 01 Jan 2001 00:00:00 +0000"},
        ).send()
    first, second = _recorded(system_mailer)
    assert len({first.external_id, second.external_id, "same@example.test"}) == 3
    wire = _deliver(first).message()
    assert (wire["Message-ID"], wire["From"]) == (f"<{first.external_id}>", SENDER)
    assert wire["Date"] != "Mon, 01 Jan 2001 00:00:00 +0000"


def test_an_email_with_no_recipient_is_skipped(system_mailer: Any) -> None:
    rows = _rows()
    assert SystemMailer().send_messages([EmailMessage("Nobody", "Body.")]) == 0
    assert _rows() == rows


def test_a_batch_is_recorded_whole_or_not_at_all(system_mailer: Any) -> None:
    rows = _rows()
    batch = [EmailMessage("One", "Body.", None, ["one@example.test"]), _with(extra_headers={"X-Foo": "bar"})()]
    with pytest.raises(ValueError):
        SystemMailer().send_messages(batch)
    assert _rows() == rows
    assert SystemMailer(fail_silently=True).send_messages(batch) == 0
    assert _rows() == rows


def test_mail_sent_in_a_transaction_that_rolls_back_is_never_sent(
    system_mailer: Any, django_capture_on_commit_callbacks: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []
    monkeypatch.setattr(celery_app, "send_task", lambda name, **options: sent.append(name))
    rows = _rows()
    with django_capture_on_commit_callbacks(execute=True), pytest.raises(RuntimeError), transaction.atomic():
        send_mail("Rolled back", "Body.", None, ["reader@example.test"])
        raise RuntimeError("the caller rolls back")
    assert (_rows(), sent) == (rows, [])
    with django_capture_on_commit_callbacks(execute=True):
        send_mail("Committed", "Body.", None, ["reader@example.test"])
    assert sent == [DELIVER_MESSAGE_TASK]


def test_with_no_transport_django_mail_is_refused_and_nothing_is_recorded(system_mailer: Any, settings: Any) -> None:
    settings.MAILERS = {"default": SYSTEM}
    rows = _rows()
    with pytest.raises(OutboundEmailNotConfigured):
        send_mail("No transport", "Body.", None, ["reader@example.test"])
    assert _rows() == rows


def test_with_no_system_channel_django_mail_is_refused_with_its_code(db: None, settings: Any) -> None:
    del db
    settings.MAILERS = {"default": SYSTEM, "transport": LOCMEM}
    settings.DEFAULT_FROM_EMAIL = SENDER
    rows = _rows()
    with pytest.raises(SystemChannelMissing) as missing:
        send_mail("No channel", "Body.", None, ["reader@example.test"])
    assert missing.value.error_dict["channel"][0].code == "SYSTEM_CHANNEL_MISSING"
    assert _rows() == rows


class _HandsBack(SystemMailer):
    """A transport that is the system mailer under another name."""


class _Delegates(BaseEmailBackend):
    """A transport that hands every email to Django's default mailer."""

    def send_messages(self, email_messages: Any) -> int:
        return mail.mailers.default.send_messages(email_messages)


@pytest.mark.parametrize("transport", [_HandsBack, _Delegates], ids=["subclass", "delegating-wrapper"])
def test_a_transport_that_hands_delivery_back_fails_that_delivery_and_records_nothing_again(
    system_mailer: Any, settings: Any, transport: type,
) -> None:
    send_mail("Loop", "Body.", None, ["reader@example.test"])
    (message,) = _recorded(system_mailer)
    settings.MAILERS = {"default": SYSTEM, "transport": {"BACKEND": f"{__name__}.{transport.__name__}"}}
    outcome = deliver_message(message._meta.label_lower, message.pk, message.external_id)
    assert not outcome["delivered"]
    with system_context(reason="tests.messaging.system_mail.loop"):
        message.refresh_from_db()
        assert message.status == "failed"
        assert "hands delivery back" in str(message.metadata.get("local", {}).get("delivery_error", ""))
    assert len(_recorded(system_mailer)) == 1
    # The mark ends with the delivery: the next email is recorded.
    send_mail("After", "Body.", None, ["reader@example.test"])
    assert len(_recorded(system_mailer)) == 2


def test_error_mail_goes_through_the_transport_never_the_system_channel(system_mailer: Any, settings: Any) -> None:
    """Messaging's contributed LOGGING, as dictConfig builds it, sends an error report through the transport."""

    settings.ADMINS = ["ops@example.test"]
    settings.DEBUG = False
    logging.config.dictConfig(settings.LOGGING)
    (handler,) = [item for item in logging.getLogger("django").handlers if isinstance(item, AdminEmailHandler)]
    record = logging.LogRecord("django.request", logging.ERROR, __file__, 1, "Boom", None, None)
    handler.handle(record)
    (sent,) = mail.outbox
    assert (sent.sent_using, sent.to) == ("transport", ["ops@example.test"])
    assert _recorded(system_mailer) == []
    # Django's own handler sends through the default mailer, which records the report on the system channel.
    AdminEmailHandler().emit(record)
    assert len(_recorded(system_mailer)) == 1
