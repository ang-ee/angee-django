"""System mail is recorded on the system channel and sent once through the one transport mailer."""

from __future__ import annotations

import copy
import email.policy
import logging
import logging.config
import smtplib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from email import message_from_bytes
from types import SimpleNamespace
from typing import Any

import pytest
from anymail.message import AnymailMessage
from django.apps import apps
from django.core import mail
from django.core.checks import Tags, run_checks
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage, EmailMultiAlternatives, send_mail
from django.core.mail.backends.base import BaseEmailBackend
from django.db import DatabaseError, connection, transaction
from django.db.models.signals import post_save
from django.template.defaultfilters import pprint
from django.test import override_settings
from django.utils import timezone
from django.utils.log import AdminEmailHandler, configure_logging
from django.views.debug import ExceptionReporter
from rebac import PermissionDenied, actor_context, system_context, to_subject_ref
from rebac.models import PermissionAuditEvent, active_relationship_model

from angee.jobs.enqueue import celery_app
from angee.messaging.backends import OutboundEmailChannelMissing, OutboundEmailNotConfigured, SystemChannelMissing
from angee.messaging.constants import DELIVER_MESSAGE_TASK, SYSTEM_CHANNEL_BACKEND, SYSTEM_MAILER_BACKEND
from angee.messaging.email import SystemChannelBackend, SystemMailer
from angee.messaging.tasks import deliver_message
from angee.messaging.testing.models import Channel, Handle, Message, Part, Recipient, Thread
from angee.provenance.context import changeset, changeset_model
from angee.resources.testing.models import Resource
from tests.conftest import Vendor, create_platform_admin, make_integration
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
        assert Channel.objects.system_mail_configured() is SystemChannelBackend.system_mail_configured() is configured
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
        pytest.param({"cc": ["jörg@example.test"]}, "cc", "cannot_carry", id="uncarriable-cc"),
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
    # The caller's changeset holds the conversation under the causing user; the handles are recorded beside
    # it under the same change reason, and their history names no user.
    assert thread_history == {("tests.system_mail.request", str(to_subject_ref(member)))}
    assert {reason for reason, _actor in handle_history} == {"tests.system_mail.request"}
    user_type = to_subject_ref(member).subject_type
    assert not any(actor.startswith(f"{user_type}:") for _reason, actor in handle_history), handle_history
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


def test_a_django_email_to_an_address_the_transport_cannot_carry_is_refused_and_nothing_is_recorded(
    system_mailer: Any,
) -> None:
    """Never recorded as sent: a sign-in link to such an address would replace the working one and arrive nowhere."""

    rows = _rows()
    with pytest.raises(ValidationError) as refused:
        send_mail("Sign in", "Your link.", None, ["jörg@example.test"])
    assert [error.code for error in refused.value.error_dict["to"]] == ["cannot_carry"]
    assert _rows() == rows


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
    assert settings.LOGGING["formatters"]["django.server"]["format"] == "[{server_time}] {message}"
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


@pytest.fixture
def host_logging(settings: Any) -> Iterator[Any]:
    """State a host's ``LOGGING`` and build it as ``django.setup()`` does; restore the logger tree after."""

    loggers = [logging.getLogger(), *(item for item in logging.root.manager.loggerDict.values()
                                      if isinstance(item, logging.Logger))]
    before = {id(logger): (logger, logger.handlers[:], logger.level, logger.propagate, logger.disabled)
              for logger in loggers}
    contributed = copy.deepcopy(settings.LOGGING)

    def state(block: dict[str, Any]) -> None:
        settings.LOGGING = block
        configure_logging(settings.LOGGING_CONFIG, block)

    yield SimpleNamespace(state=state, contributed=contributed)
    for item in list(logging.root.manager.loggerDict.values()):
        if isinstance(item, logging.Logger) and id(item) not in before:
            item.handlers, item.propagate, item.disabled = [], True, False
            item.setLevel(logging.NOTSET)
    for logger, handlers, level, propagate, disabled in before.values():
        logger.handlers, logger.propagate, logger.disabled = handlers, propagate, disabled
        logger.setLevel(level)


def _with_app_handler(block: dict[str, Any], **handler: str) -> dict[str, Any]:
    """``block`` with one more ``AdminEmailHandler``, on the ``myapp`` logger."""

    block = copy.deepcopy(block)
    block["handlers"]["ops"] = {"class": "django.utils.log.AdminEmailHandler", "level": "ERROR", **handler}
    block.setdefault("loggers", {})["myapp"] = {"handlers": ["ops"], "level": "ERROR"}
    return block


_BARE = {"version": 1, "disable_existing_loggers": False}


@pytest.mark.parametrize(
    ("default", "block", "warned"),
    [
        pytest.param(SYSTEM, lambda contributed: _BARE, ["django"], id="django-own-handler-kept"),
        pytest.param(SYSTEM, lambda contributed: _with_app_handler(contributed), ["myapp"], id="no-using"),
        pytest.param(SYSTEM, lambda contributed: _with_app_handler(contributed, using="default"), ["myapp"],
                     id="using-default"),
        pytest.param(SYSTEM, lambda contributed: _with_app_handler(contributed, using="transport"), [],
                     id="using-transport"),
        pytest.param(LOCMEM, lambda contributed: _BARE, [], id="default-is-not-the-system-mailer"),
    ],
)
def test_a_configured_logger_that_would_record_error_mail_is_reported(
    settings: Any, host_logging: Any, default: dict[str, str], block: Any, warned: list[str],
) -> None:
    """Django applies its own logging first, so a host block that leaves the django logger keeps its handler."""

    settings.MAILERS = {"default": default, "transport": LOCMEM}
    host_logging.state(block(host_logging.contributed))
    reported = [message for message in run_checks(tags=[Tags.mail]) if message.id == "messaging.W001"]
    assert [[name for name in ("django", "myapp") if f"'{name}'" in message.msg] for message in reported] == (
        [warned] if warned else []
    )


def test_messagings_own_logging_is_not_reported(settings: Any) -> None:
    settings.MAILERS = {"default": SYSTEM, "transport": LOCMEM}
    assert "messaging.W001" not in {message.id for message in run_checks(tags=[Tags.mail])}


SECRET = "https://example.test/sign-in#token=SECRET-7f3a"
"""A credential in a system mail's subject, text and HTML, as a sign-in link or a one-time code carries one."""


def _reported_secret(
    error: BaseException, *, below: str | None = None, needle: str = "SECRET-7f3a",
) -> list[tuple[str, str]]:
    """Each ``(function, variable)`` of an error report that shows ``needle``, as Django's reporter renders it.

    With ``below``, only the frames from that function's down.
    """

    frames = ExceptionReporter(None, type(error), error, error.__traceback__).get_traceback_frames()
    if below is not None:
        functions = [frame["function"] for frame in frames]
        assert below in functions
        frames = frames[functions.index(below):]
    return sorted({
        (frame["function"], name) for frame in frames for name, value in frame["vars"]
        if needle in pprint(value)
    })


def test_an_error_report_of_an_email_the_system_mailer_refuses_shows_none_of_its_body(
    system_mailer: Any, settings: Any,
) -> None:
    """Refused while the mailer reads it: the reported frames hold the body under the mailer's marked names."""

    settings.DEBUG = False
    email = EmailMultiAlternatives(f"Sign in: {SECRET}", SECRET, None, ["reader@example.test"])
    email.attach_alternative(f"<p>{SECRET}</p>", "text/calendar")
    with pytest.raises(ValueError) as refused:
        email.send()
    assert _reported_secret(refused.value) == []


def test_an_error_report_of_system_mail_with_no_system_channel_shows_none_of_its_text(
    db: None, settings: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refused inside the writer, below the mailer: neither the recorded arguments nor the writer's text show."""

    del db
    settings.DEBUG = False
    settings.MAILERS = {"default": SYSTEM, "transport": LOCMEM}
    settings.DEFAULT_FROM_EMAIL = SENDER
    monkeypatch.setattr(celery_app, "send_task", lambda name, **options: None)
    email = EmailMultiAlternatives(f"Sign in: {SECRET}", SECRET, None, ["reader@example.test"])
    email.attach_alternative(f"<p>{SECRET}</p>", "text/html")
    with pytest.raises(SystemChannelMissing) as missing:
        email.send()
    assert _reported_secret(missing.value) == []


def test_an_error_report_of_system_mail_its_arguments_refuse_shows_none_of_its_text(
    system_channel: Any, settings: Any,
) -> None:
    settings.DEBUG = False
    with pytest.raises(ValidationError) as refused:
        Message.objects.compose_as_system(reason="tests.messaging.system_mail.report", to=["reader@example.test"],
                                          subject=f"Sign in:\n{SECRET}", text=SECRET, html=f"<p>{SECRET}</p>")
    assert _reported_secret(refused.value) == []


def test_an_error_report_of_a_system_mail_write_that_fails_shows_none_of_its_text(
    system_channel: Any, settings: Any,
) -> None:
    """Failing while its recipients land, after the row is written: the writer's frame holds the text and the row."""

    settings.DEBUG = False

    def fail(sender: Any, instance: Any, **kwargs: Any) -> None:
        if instance.value == "failing@example.test":
            raise RuntimeError("The recipient cannot be landed.")

    post_save.connect(fail, sender=Handle, weak=False)
    try:
        with pytest.raises(RuntimeError) as failed:
            Message.objects.compose_as_system(reason="tests.messaging.system_mail.report", to=["failing@example.test"],
                                              subject=f"Sign in: {SECRET}", text=SECRET, html=f"<p>{SECRET}</p>")
    finally:
        post_save.disconnect(fail, sender=Handle)
    assert _reported_secret(failed.value) == []



def _flat(params: Any) -> list[Any]:
    """A statement's parameters, one batch's rows flattened."""

    return [value for row in params for value in (row if isinstance(row, list | tuple) else (row,))] if params else []


@contextmanager
def _failing(statement: Callable[[str, Any], bool]) -> Iterator[None]:
    """Fail each statement ``statement`` matches with a database error, before the database runs it."""

    def execute(run: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        if statement(sql, params):
            raise DatabaseError("The statement failed.")
        return run(sql, params, many, context)

    with connection.execute_wrapper(execute):
        yield


def _body_fragment_insert(sql: str, params: Any) -> bool:
    table = apps.get_model("messaging", "Fragment")._meta.db_table
    return sql.startswith(f'INSERT INTO "{table}"') and any(
        isinstance(value, str) and value.startswith("Body ") for value in _flat(params)
    )


def _message_insert(sql: str, params: Any) -> bool:
    return sql.startswith(f'INSERT INTO "{Message._meta.db_table}" ')


def _delivery_reservation(sql: str, params: Any) -> bool:
    return sql.startswith(f'UPDATE "{Message._meta.db_table}" ') and "delivery_lease_until" in sql


_STATEMENTS = [
    pytest.param(_body_fragment_insert, id="body-fragment-insert"),
    pytest.param(_message_insert, id="message-insert"),
    pytest.param(_delivery_reservation, id="delivery-reservation"),
]


@pytest.mark.parametrize("statement", _STATEMENTS)
def test_an_error_report_of_a_failing_statement_in_the_system_mail_write_shows_none_of_the_mail(
    system_channel: Any, settings: Any, statement: Callable[[str, Any], bool],
) -> None:
    """A real statement fails inside the write: no frame, the ORM's and the SQL's included, shows the mail."""

    settings.DEBUG = False
    with _failing(statement), pytest.raises(DatabaseError) as failed:
        Message.objects.compose_as_system(reason="tests.messaging.system_mail.report", to=["reader@example.test"],
                                          subject=f"Sign in: {SECRET}", text=f"Body {SECRET}", html=f"<p>{SECRET}</p>")
    assert _reported_secret(failed.value) == []


@pytest.mark.parametrize("statement", _STATEMENTS)
def test_an_error_report_of_a_failing_compose_hides_every_frame_below_the_shared_writer(
    db: None, settings: Any, monkeypatch: pytest.MonkeyPatch, statement: Callable[[str, Any], bool],
) -> None:
    """A person's own mail: ``compose``'s frame is not marked; from the writer down, nothing shows."""

    del db
    settings.DEBUG = False
    settings.MAILERS = {"transport": LOCMEM}
    monkeypatch.setattr(celery_app, "send_task", lambda name, **options: None)
    desk = make_integration("report-desk", model=Channel, backend_class="email",
                            config={"from_email": "Desk <desk@example.test>"})
    with _failing(statement), pytest.raises(DatabaseError) as failed:
        Message.objects.compose(channel=desk, to=["reader@example.test"], subject=f"Sign in: {SECRET}",
                                text=f"Body {SECRET}", html=f"<p>{SECRET}</p>", actor=desk.owner, queued=True)
    assert ("compose", "text") in _reported_secret(failed.value)
    assert _reported_secret(failed.value, below="_write_outgoing") == []



class FailingTransport(BaseEmailBackend):
    """A transport that fails each send with the failure its options name."""

    def __init__(self, *, failure: BaseException, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.failure = failure

    def send_messages(self, email_messages: Any) -> int:
        raise self.failure


def _queued_system_mail(monkeypatch: pytest.MonkeyPatch) -> Any:
    """One system mail, queued, its subject, text and HTML carrying ``SECRET``; nothing reaches a broker."""

    monkeypatch.setattr(celery_app, "send_task", lambda name, **options: None)
    return Message.objects.compose_as_system(
        reason="tests.messaging.system_mail.report", to=["reader@example.test"], subject=f"Sign in: {SECRET}",
        text=f"Body {SECRET}", html=f"<p>{SECRET}</p>",
    )


def _lost(message: Any) -> None:
    """Expire the message's dispatch lease, as a lost broker submission leaves it: due to be sent again."""

    with system_context(reason="tests.messaging.system_mail.lost"):
        Message._base_manager.filter(pk=message.pk).update(delivery_lease_until=timezone.now() - timedelta(hours=1))


def test_an_error_report_of_a_failing_transport_shows_none_of_the_system_mail(
    system_channel: Any, settings: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker's path from the claim to the transport: the transport fails, then recording it fails too.

    The report chains both: the recording's frames, the worker's, the backend's and the transport's. None
    of them shows the mail, and this transport's own exception carries none of it, so neither does its
    message; an exception that does carry some shows it (the known-limit test below).
    """

    settings.DEBUG = False
    failure = ConnectionError("The transport is down.")
    settings.MAILERS = {"transport": {"BACKEND": f"{FailingTransport.__module__}.FailingTransport",
                                      "OPTIONS": {"failure": failure}}}
    label, pk, token = (lambda message: (message._meta.label_lower, message.pk, message.external_id))(
        _queued_system_mail(monkeypatch),
    )
    table = Message._meta.db_table

    def recording(sql: str, params: Any) -> bool:
        return failure.__traceback__ is not None and sql.startswith(f'UPDATE "{table}" ')

    with _failing(recording), pytest.raises(DatabaseError) as failed:
        deliver_message(label, pk, token)
    chained, error = [], failed.value
    while error is not None:
        chained.append(error)
        error = error.__cause__ or error.__context__
    assert failure in chained
    assert _reported_secret(failed.value) == []
    assert [str(error) for error in chained if "SECRET-7f3a" in str(error)] == []



def test_a_known_limit_an_error_report_shows_what_a_transport_exception_itself_carries(
    system_channel: Any, settings: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not a guarantee: Django filters frames, never an exception's own text.

    An SMTP refusal carries the recipient it refused (a server can echo a line of the mail as well), and a
    report of a failed recording chains it: its text shows there, while no frame shows it.
    """

    settings.DEBUG = False
    settings.MAILERS = {"transport": {"BACKEND": f"{FailingTransport.__module__}.FailingTransport", "OPTIONS": {
        "failure": smtplib.SMTPRecipientsRefused({"reader@example.test": (550, b"5.1.1 No such user")}),
    }}}
    label, pk, token = (lambda message: (message._meta.label_lower, message.pk, message.external_id))(
        _queued_system_mail(monkeypatch),
    )
    table = Message._meta.db_table

    def recording(sql: str, params: Any) -> bool:
        raised = settings.MAILERS["transport"]["OPTIONS"]["failure"].__traceback__ is not None
        return raised and sql.startswith(f'UPDATE "{table}" ')

    with _failing(recording), pytest.raises(DatabaseError) as failed:
        deliver_message(label, pk, token)
    in_frames = _reported_secret(failed.value, needle="reader@example.test")
    assert in_frames == []
    chained, error = [], failed.value
    while error is not None:
        chained.append(error)
        error = error.__cause__ or error.__context__
    assert [type(error) for error in chained if "reader@example.test" in str(error)] == [
        smtplib.SMTPRecipientsRefused,
    ]


def test_an_error_report_of_a_failing_reservation_hides_every_frame_from_deliver_down(
    system_channel: Any, settings: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Message.deliver`` holds the row, whose str is its preview; its caller's frame is the caller's."""

    settings.DEBUG = False
    message = _queued_system_mail(monkeypatch)
    _lost(message)
    with system_context(reason="tests.messaging.system_mail.report"):
        row = Message._base_manager.get(pk=message.pk)
    with _failing(_delivery_reservation), pytest.raises(DatabaseError) as failed:
        row.deliver()
    assert _reported_secret(failed.value, below="deliver") == []


def test_an_error_report_of_a_failing_release_shows_none_of_the_system_mail(
    system_channel: Any, settings: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The release task sends a lost system mail again; its frame holds the rows it sends."""

    settings.DEBUG = False
    message = _queued_system_mail(monkeypatch)
    _lost(message)
    with _failing(_delivery_reservation), pytest.raises(DatabaseError) as failed:
        Message.objects.release_held_messages()
    assert _reported_secret(failed.value, below="release_held_messages") == []




def _acknowledgement(sql: str, params: Any) -> bool:
    return sql.startswith(f'UPDATE "{Message._meta.db_table}" ') and "acknowledged_by" in sql


def _retried_reservation(sql: str, params: Any) -> bool:
    return _delivery_reservation(sql, params) and "acknowledged_by" not in sql


@pytest.mark.parametrize(
    ("verb", "status", "statement"),
    [
        pytest.param("retry_delivery", "failed", _retried_reservation, id="retry-failed"),
        pytest.param("acknowledge_delivery", "uncertain", _acknowledgement, id="acknowledge-uncertain"),
    ],
)
def test_an_error_report_of_a_failing_operator_verb_hides_every_frame_from_the_verb_down(
    db: None, settings: Any, monkeypatch: pytest.MonkeyPatch, verb: str, status: str,
    statement: Callable[[str, Any], bool],
) -> None:
    """An operator retries or acknowledges an ordinary mail and a statement fails: nothing shows from the verb down."""

    del db
    settings.DEBUG = False
    settings.MAILERS = {"transport": LOCMEM}
    monkeypatch.setattr(celery_app, "send_task", lambda name, **options: None)
    desk = make_integration("operator-desk", model=Channel, backend_class="email",
                            config={"from_email": "Desk <desk@example.test>"})
    sent = Message.objects.compose(channel=desk, to=["reader@example.test"], subject=f"Sign in: {SECRET}",
                                   text=f"Body {SECRET}", html=f"<p>{SECRET}</p>", actor=desk.owner, queued=True)
    with system_context(reason="tests.messaging.system_mail.operator"):
        Message._base_manager.filter(pk=sent.pk).update(
            status=status, delivery_possibly_accepted=True, delivery_lease_until=None, delivery_claim=None,
        )
        revision = Message._base_manager.get(pk=sent.pk).revision
    with _failing(statement), pytest.raises(DatabaseError) as failed:
        getattr(sent, verb)(actor=desk.owner, expected_revision=revision)
    assert _reported_secret(failed.value, below=verb) == []


@pytest.mark.parametrize("retry", [True, False], ids=["retry", "acknowledge"])
def test_nobody_retries_or_acknowledges_a_system_mail_whose_delivery_is_uncertain(
    system_channel: Any, monkeypatch: pytest.MonkeyPatch, retry: bool,
) -> None:
    """Both write the message, whose seal admits nobody, administrators included; the release sends a lost one."""

    admin = create_platform_admin("delivery-admin")
    message = _queued_system_mail(monkeypatch)
    with system_context(reason="tests.messaging.system_mail.uncertain"):
        Message._base_manager.filter(pk=message.pk).update(
            status="uncertain", delivery_possibly_accepted=True, delivery_lease_until=None, delivery_claim=None,
        )
        revision = Message._base_manager.get(pk=message.pk).revision
    with pytest.raises(PermissionDenied), transaction.atomic():
        Message.objects.resolve_uncertain_delivery(message, actor=admin, expected_revision=revision, retry=retry)
    with system_context(reason="tests.messaging.system_mail.uncertain"):
        assert Message._base_manager.get(pk=message.pk).status == "uncertain"


class UnconfiguredSystemBackend(SystemChannelBackend):
    """A host's own system backend, bound to the system key, that never has system mail configured."""

    @classmethod
    def system_mail_configured(cls) -> bool:
        return False


def test_a_rebound_system_backend_gives_callers_and_the_writer_one_answer(system_channel: Any, settings: Any) -> None:
    """The registry decides: callers ask the channel manager, never a backend class by name."""

    assert Channel.objects.system_mail_configured()
    settings.ANGEE_CHANNEL_BACKEND_CLASSES = {
        **settings.ANGEE_CHANNEL_BACKEND_CLASSES,
        SYSTEM_CHANNEL_BACKEND: "tests.test_messaging_system_mail.UnconfiguredSystemBackend",
    }
    assert SystemChannelBackend.system_mail_configured()
    assert not Channel.objects.system_mail_configured()
    with _refused(ValidationError, key="channel", code="OUTBOUND_EMAIL_NOT_CONFIGURED"):
        Message.objects.compose_as_system(reason="tests.system_mail", to=["reader@example.test"], text="Hello.")


@override_settings(MAILERS={"transport": LOCMEM}, DEFAULT_FROM_EMAIL=SENDER)
def test_whether_system_mail_is_configured_and_can_reach_an_address_is_answered_with_no_database() -> None:
    assert Channel.objects.system_mail_configured()
    assert Message.objects.clean_system_recipient("Reader <reader@example.test>") == ("Reader", "reader@example.test")
    with pytest.raises(ValidationError) as refused:
        Message.objects.clean_system_recipient("jörg@example.test")
    assert [error.code for error in refused.value.error_dict["to"]] == ["cannot_carry"]


def _refusals(call: Any) -> list[tuple[str, str]]:
    try:
        call()
    except ValidationError as error:
        return [(key, item.code) for key, items in error.error_dict.items() for item in items]
    return []


@pytest.mark.parametrize(
    ("entry", "refusal"),
    [
        pytest.param("reader@example.test", None, id="ordinary"),
        pytest.param("jörg@example.test", "cannot_carry", id="local-part-outside-ascii"),
        pytest.param('"a b"@example.test', "invalid", id="quoted-local-part-with-a-space"),
        pytest.param("", "invalid", id="blank"),
    ],
)
def test_system_mail_refuses_exactly_the_recipients_its_recipient_rule_refuses(
    system_channel: Any, entry: str, refusal: str | None,
) -> None:
    """Django's email validator refuses the first odd address and admits the second; system mail's rule decides."""

    expected = [] if refusal is None else [("cc", refusal)]
    assert _refusals(lambda: Message.objects.clean_system_recipient(entry, key="cc")) == expected
    assert _refusals(lambda: Message.objects.compose_as_system(
        reason="tests.system_mail", to=["reader@example.test"], cc=[entry], text="Hello.",
    )) == expected
