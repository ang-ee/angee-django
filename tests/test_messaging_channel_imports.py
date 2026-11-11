"""The Channel manager's named import factory and backend-constrained import targets."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from rebac import system_context

from angee.messaging.testing.models import Channel
from tests.conftest import Vendor


@pytest.fixture
def import_owner(db: None) -> Any:
    """Seed the manual backend's vendor and one channel owner."""

    del db
    with system_context(reason="test.messaging.channel.import.owner"):
        Vendor.objects.create(slug="manual", display_name="Manual")
        return get_user_model().objects.create_user(username="channel-import-owner")


def test_named_import_reuses_the_owners_channel_by_name_and_backend(import_owner: Any) -> None:
    """Re-running an import lands on the same channel; another name gets its own."""

    with system_context(reason="test.messaging.channel.import.named"):
        first = Channel.objects.named_import_channel(import_owner, name="  Archive  ", backend_class="manual")
        again = Channel.objects.named_import_channel(import_owner, name="Archive", backend_class="manual")
        other = Channel.objects.named_import_channel(import_owner, name="Other", backend_class="manual")
        assert again.pk == first.pk
        assert other.pk != first.pk
        assert first.display_name == "Archive"
        assert first.lifecycle == Channel.Lifecycle.DISCONNECTED
        assert Channel.objects.filter(owner=import_owner).count() == 2


def test_channel_factories_refuse_a_blank_name(import_owner: Any) -> None:
    """Every factory composes the one display-name rule before writing."""

    with system_context(reason="test.messaging.channel.import.blank"):
        with pytest.raises(ValueError, match="channel name is required"):
            Channel.objects.create_disconnected(import_owner, name="  ", backend_class="manual")
        with pytest.raises(ValueError, match="channel name is required"):
            Channel.objects.named_import_channel(import_owner, name="", backend_class="manual")
        assert not Channel.objects.exists()


def test_import_target_resolves_only_a_channel_of_the_named_backend(import_owner: Any) -> None:
    """A missing id and another backend's channel are refused with the same target error."""

    with system_context(reason="test.messaging.channel.import.target"):
        channel = Channel.objects.create_disconnected(import_owner, name="Archive", backend_class="manual")
        assert list(Channel.objects.filter(pk=channel.pk).for_backend("webform")) == []
    assert Channel.objects.require_backend_target(channel.sqid, backend_class="manual").pk == channel.pk
    with pytest.raises(ValidationError, match="No Public Webform channel"):
        Channel.objects.require_backend_target(channel.sqid, backend_class="webform")
    with pytest.raises(ValidationError, match="No Manual channel 'chn_missing'"):
        Channel.objects.require_backend_target("chn_missing", backend_class="manual")
