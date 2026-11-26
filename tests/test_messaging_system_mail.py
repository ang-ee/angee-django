"""Django's own mail is recorded on the system channel and sent once through the one transport mailer."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings
from rebac import actor_context, system_context, to_subject_ref
from rebac.models import PermissionAuditEvent, active_relationship_model

from angee.messaging.backends import OutboundEmailChannelMissing, SystemChannelMissing
from angee.messaging.constants import SYSTEM_CHANNEL_BACKEND
from angee.messaging.email import SystemChannelBackend
from angee.messaging.managers import _OUTGOING_ACTOR
from angee.messaging.testing.models import Channel, Handle, Message, Part, Recipient, Thread
from angee.provenance.context import changeset, changeset_model
from angee.resources.testing.models import Resource
from tests.conftest import Vendor
from tests.messaging_campaign import make_user

pytestmark = pytest.mark.filterwarnings("error::django.utils.deprecation.RemovedInDjango2028Warning")

LOCMEM = {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}
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
