"""Message visibility inherited from user-owned channels."""

from __future__ import annotations

from typing import Any

import pytest
from django.apps import apps
from django.core.management import call_command
from django.test import override_settings
from rebac import system_context

from tests.conftest import Vendor
from tests.messaging_models import Channel, Message, Thread


@pytest.fixture(params=("denormalized", "registry"))
def messaging_access_schema(request: pytest.FixtureRequest) -> Any:
    """Synchronize messaging's field-backed access chain in either local store."""

    with override_settings(REBAC_LOCAL_BACKEND_STORAGE=request.param):
        call_command("rebac", "sync", verbosity=0)
        yield request.param


@pytest.mark.django_db(transaction=True)
def test_channel_owner_reaches_threads_and_messages(messaging_access_schema: str) -> None:
    """Person and service owners inherit channel access through both message links."""

    del messaging_access_schema
    user_model = apps.get_model("iam", "User")
    person = user_model.objects.create_user(username="message-channel-person", kind="person")
    service = user_model.objects.create_user(username="message-channel-service", kind="service")
    author = user_model.objects.create_user(username="message-channel-author", kind="person")
    outsider = user_model.objects.create_user(username="message-channel-outsider", kind="person")
    with system_context(reason="tests.messaging.channel_access"):
        vendor = Vendor.objects.create(slug="message-channel-access", display_name="Message channel access")
        person_channel = Channel.objects.create(vendor=vendor, owner=person, backend_class="manual")
        service_channel = Channel.objects.create(vendor=vendor, owner=service, backend_class="manual")
        person_thread, _ = Thread.objects.get_or_create_by_external_id(
            platform="email", external_id="person-channel-thread",
            defaults={"channel": person_channel, "created_by": person, "updated_by": person},
        )
        service_thread, _ = Thread.objects.get_or_create_by_external_id(
            platform="email", external_id="service-channel-thread",
            defaults={"channel": service_channel, "created_by": author, "updated_by": author},
        )
        thread_message = Message.objects.create(thread=person_thread, created_by=author, updated_by=author)
        channel_message = Message.objects.create(channel=person_channel, created_by=author, updated_by=author)
        service_message = Message.objects.create(thread=service_thread, created_by=author, updated_by=author)

    assert person_channel.with_actor(person).has_access("read")
    assert person_thread.with_actor(person).has_access("read")
    assert person_thread.with_actor(person).has_access("write")
    assert thread_message.with_actor(person).has_access("read")
    assert thread_message.with_actor(person).has_access("write")
    assert channel_message.with_actor(person).has_access("read")
    assert channel_message.with_actor(person).has_access("write")
    assert service_channel.with_actor(service).has_access("read")
    assert service_thread.with_actor(service).has_access("read")
    assert service_thread.with_actor(service).has_access("write")
    assert service_message.with_actor(service).has_access("read")
    assert service_message.with_actor(service).has_access("write")
    for actor, rows in (
        (person, (person_thread, thread_message, channel_message)),
        (service, (service_thread, service_message)),
    ):
        for row in rows:
            assert row.with_actor(actor).has_access("delete")
            assert type(row).objects.with_actor(actor).with_action("delete").scoped().filter(pk=row.pk).exists()
    assert person_thread.owner_id is None and person_thread.created_by_id == person.pk
    assert service_thread.owner_id is None and service_thread.created_by_id == author.pk
    assert not service_thread.with_actor(author).has_access("read")

    candidates = (thread_message.pk, channel_message.pk, service_message.pk)
    assert set(
        Message.objects.with_actor(person)
        .with_action("read")
        .scoped()
        .filter(pk__in=candidates)
        .values_list("pk", flat=True)
    ) == {thread_message.pk, channel_message.pk}
    assert set(
        Message.objects.with_actor(service)
        .with_action("read")
        .scoped()
        .filter(pk__in=candidates)
        .values_list("pk", flat=True)
    ) == {service_message.pk}
    assert not (
        Message.objects.with_actor(outsider)
        .with_action("read")
        .scoped()
        .filter(pk__in=candidates)
        .exists()
    )
    assert not person_channel.with_actor(outsider).has_access("read")
    assert not person_thread.with_actor(outsider).has_access("read")
    assert not thread_message.with_actor(outsider).has_access("read")
    assert not channel_message.with_actor(outsider).has_access("read")
    assert not person_thread.with_actor(outsider).has_access("delete")

    with system_context(reason="tests.messaging.channel_transfer"):
        Channel._base_manager.filter(pk=person_channel.pk).update(owner=outsider)
        person_thread.refresh_from_db()
    assert person_thread.owner_id is None and person_thread.created_by_id == person.pk
    for row in (person_thread, thread_message, channel_message):
        assert not row.with_actor(person).has_access("read")
        assert row.with_actor(outsider).has_access("read")
        assert not row.with_actor(person).has_access("delete")
        assert row.with_actor(outsider).has_access("delete")
        assert not type(row).objects.with_actor(person).with_action("delete").scoped().filter(pk=row.pk).exists()
        assert type(row).objects.with_actor(outsider).with_action("delete").scoped().filter(pk=row.pk).exists()
