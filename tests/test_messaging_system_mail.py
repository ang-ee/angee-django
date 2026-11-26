"""Django's own mail is recorded on the system channel and sent once through the one transport mailer."""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings
from rebac import system_context
from rebac.models import active_relationship_model

from angee.messaging.backends import SystemChannelMissing
from angee.messaging.constants import SYSTEM_CHANNEL_BACKEND
from angee.messaging.email import SystemChannelBackend
from angee.messaging.testing.models import Channel
from angee.resources.testing.models import Resource
from tests.conftest import Vendor

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
