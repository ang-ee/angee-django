"""PostgreSQL owner-lock races for the Channel manager's per-owner factories."""

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from rebac import system_context

from angee.messaging.testing.models import Channel
from tests.conftest import Vendor
from tests.lock_waits import submit, wait_for_lock
from tests.pairing_backend import FakePairingBackend

pytest_plugins = ("tests.test_messaging_pairing_graphql",)

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


@pytest.fixture
def channel_owner(pairing_graphql: list[dict[str, Any]]) -> Any:
    """Seed the live backend's vendor and one channel owner."""

    del pairing_graphql
    with system_context(reason="test.messaging.channel.concurrency.owner"):
        Vendor.objects.create(slug=FakePairingBackend.key, display_name=FakePairingBackend.label)
        return get_user_model().objects.create_user(username="channel-race-owner")


def test_concurrent_pairing_attempts_reuse_one_channel(channel_owner: Any) -> None:
    """A second attempt waits on the owner row, then reuses the first attempt's channel."""

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(), system_context(reason="test.messaging.pairing.race"):
            get_user_model().system_queryset().lock_if_supported(no_key=True).get(pk=channel_owner.pk)
            contender, pid = submit(
                pool,
                lambda: Channel.objects.resume_or_create_pairing(
                    channel_owner,
                    name="Second attempt",
                    backend_class=FakePairingBackend.key,
                ),
            )
            wait_for_lock(pid, contender)
            first = Channel.objects.resume_or_create_pairing(
                channel_owner,
                name="First attempt",
                backend_class=FakePairingBackend.key,
            )
        second = contender.result(timeout=10)
    assert second.pk == first.pk
    with system_context(reason="test.messaging.pairing.race.verify"):
        assert list(Channel.objects.filter(owner=channel_owner).values_list("display_name", flat=True)) == [
            "First attempt",
        ]


def test_concurrent_named_imports_create_one_channel(channel_owner: Any) -> None:
    """A second first-import waits on the owner row, then reuses the committed channel."""

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(), system_context(reason="test.messaging.named_import.race"):
            get_user_model().system_queryset().lock_if_supported(no_key=True).get(pk=channel_owner.pk)
            contender, pid = submit(
                pool,
                lambda: Channel.objects.named_import_channel(
                    channel_owner,
                    name="Archive",
                    backend_class=FakePairingBackend.key,
                ),
            )
            wait_for_lock(pid, contender)
            first = Channel.objects.named_import_channel(
                channel_owner,
                name="Archive",
                backend_class=FakePairingBackend.key,
            )
        second = contender.result(timeout=10)
    assert second.pk == first.pk
    with system_context(reason="test.messaging.named_import.race.verify"):
        assert Channel.objects.filter(owner=channel_owner).count() == 1
