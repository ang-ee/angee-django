"""Message visibility inherited from user-owned channels."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import override_settings
from rebac import RelationshipTuple, system_context, to_object_ref, to_subject_ref, write_relationships
from rebac.backends import backend
from rebac.evaluator import evaluator_scope

from angee.compose.permissions import apply_schema_paths, extension_source_map
from angee.fs import write_atomic
from angee.messaging.testing.models import Channel, Message, Part, Thread, ThreadAttachment
from angee.testing.permissions import installed_field_owners
from tests.chatterdemo.models import ChatterDoc
from tests.conftest import File, Vendor
from tests.test_messaging import _storage_drive


@pytest.fixture(params=("denormalized", "registry"))
def messaging_access_schema(request: pytest.FixtureRequest, tmp_path: Path, transactional_db: None) -> Any:
    """Synchronize messaging's field-backed access chain in either local store."""

    configs = list(apps.get_app_configs())
    originals = {config: getattr(config, "rebac_schema", None) for config in configs}
    sources = extension_source_map(configs, field_owners=installed_field_owners(configs))
    runtime = tmp_path / "messaging-access-runtime"
    for relative, source in sources.items():
        write_atomic(runtime / relative, source)
    apply_schema_paths(configs, runtime, sources=sources)
    try:
        with override_settings(REBAC_LOCAL_BACKEND_STORAGE=request.param):
            call_command("rebac", "sync", verbosity=0)
            yield request.param
    finally:
        for config, original in originals.items():
            if original is None:
                if hasattr(config, "rebac_schema"):
                    delattr(config, "rebac_schema")
            else:
                config.rebac_schema = original


def test_record_read_reaches_only_chatter_and_compiles_dependents(messaging_access_schema: str) -> None:
    """Record readers inherit read alone, through the filtered chatter attachment."""

    with system_context(reason="tests.messaging.record_arm"):
        users = [apps.get_model("iam", "User").objects.create_user(username=f"record-arm-{i}") for i in range(5)]
        reader, writer, owner, outsider, other_reader = users
        record = ChatterDoc.objects.create(title="Record A")
        other = ChatterDoc.objects.create(title="Record B")
        write_relationships([
            RelationshipTuple(to_object_ref(record), role, to_subject_ref(actor))
            for role, actor in (("reader", reader), ("writer", writer), ("owner", owner))
        ] + [RelationshipTuple(to_object_ref(other), "reader", to_subject_ref(other_reader))])
        attachment = ThreadAttachment.objects.for_record(record)
        chatter = record.message_thread()
        other_chatter = other.message_thread()
        source = Thread.objects.create()
        ThreadAttachment.objects.create(
            thread=source, content_type_id=attachment.content_type_id, object_id=record.pk, role="source",
        )
        unattached = Thread.objects.create()
        message = Message._base_manager.filter(thread=chatter).first()

    for actor in (*users, AnonymousUser()):
        subject = to_subject_ref(actor)
        expected = actor in (reader, writer, owner)
        for row, readable in (
            (chatter, expected), (other_chatter, actor == other_reader), (source, False), (unattached, False),
            (attachment, expected), (message, expected),
        ):
            for action in ("read", "write", "delete") + (("share", "transfer") if isinstance(row, Thread) else ()):
                allowed = readable and action == "read"
                assert backend().check_access(
                    subject=subject, action=action, resource=to_object_ref(row),
                ).allowed == allowed
                scoped = type(row).objects.with_actor(actor).with_action(action).scoped()
                assert scoped.filter(pk=row.pk).exists() == allowed

    with evaluator_scope():
        scoped = Thread.objects.with_actor(reader).scoped()
        sql, params = scoped.order_by().query.sql_with_params()
        assert "SELECT" in sql and params
        assert scoped.filter(pk=chatter.pk).exists()
        assert not scoped.filter(pk=source.pk).exists()
        # These paths include attachment -> thread -> task -> project -> group.
        for name in ("ThreadFollower", "ThreadNotification", "ThreadActivity", "Part", "Reaction"):
            model = apps.get_model("messaging", name)
            sql = model.objects.with_actor(reader).scoped().query.sql_with_params()[0]
            assert "SELECT" in sql, (messaging_access_schema, name)


@pytest.mark.django_db(transaction=True)
def test_channel_owner_reaches_threads_and_messages(messaging_access_schema: str) -> None:
    """Person and service owners inherit channel access through both message links."""

    del messaging_access_schema
    user_model = apps.get_model("iam", "User")
    person = user_model.objects.create_user(username="message-channel-person", kind="person")
    service = user_model.objects.create_user(username="message-channel-service", kind="service")
    author = user_model.objects.create_user(username="message-channel-author", kind="person")
    outsider = user_model.objects.create_user(username="message-channel-outsider", kind="person")
    reader = user_model.objects.create_user(username="message-channel-reader", kind="service")
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
    with system_context(reason="tests.messaging.channel_owner_refused"), pytest.raises(ValidationError):
        Thread.objects.create(channel=service_channel, owner=author)
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

    person_channel.with_actor(person).grant_record_access("reader", reader)
    assert person_channel.with_actor(reader).has_access("read")
    assert not person_channel.integration_ptr.with_actor(reader).has_access("read")
    assert thread_message.with_actor(reader).has_access("read")
    assert channel_message.with_actor(reader).has_access("read")
    person_channel.with_actor(person).revoke_record_access("reader", reader)
    assert not thread_message.with_actor(reader).has_access("read")
    assert not channel_message.with_actor(reader).has_access("read")

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


@pytest.mark.django_db(transaction=True)
def test_channel_reader_reads_only_message_attachment_file_content(
    messaging_access_schema: str, tmp_path: Path,
) -> None:
    """A channel reader reaches referenced bytes without a drive grant."""

    del messaging_access_schema
    user_model = apps.get_model("iam", "User")
    owner = user_model.objects.create_user(username="attachment-owner", kind="person")
    reader = user_model.objects.create_user(username="attachment-reader", kind="service")
    content = b"Message attachment content"
    with system_context(reason="tests.messaging.attachment_access"):
        vendor = Vendor.objects.create(slug="attachment-access", display_name="Attachment access")
        channel = Channel.objects.create(vendor=vendor, owner=owner, backend_class="manual")
        message = Message.objects.create(channel=channel, created_by=owner, updated_by=owner)
        drive = _storage_drive(tmp_path, owner=owner)
        attached = File.objects.ingest_bytes(
            content, filename="attached.txt", owner_id=owner.pk, drive_id=str(drive.sqid),
        )
        unrelated = File.objects.ingest_bytes(
            b"Private content", filename="private.txt", owner_id=owner.pk, drive_id=str(drive.sqid),
        )
        part = Part.objects.create(message=message, file=attached, created_by=owner, updated_by=owner)

    assert not drive.with_actor(reader).has_access("read")
    assert not attached.with_actor(reader).has_access("read")
    channel.with_actor(owner).grant_record_access("reader", reader)
    visible_message = Message.objects.with_actor(reader).get(pk=message.pk)
    visible_part = Part.objects.with_actor(reader).get(message=visible_message, pk=part.pk)
    visible_file = File.objects.with_actor(reader).get(pk=visible_part.file_id)
    assert visible_file.read_verified(max_bytes=len(content)) == content
    assert not drive.with_actor(reader).has_access("read")
    assert not attached.with_actor(reader).has_access("write")
    assert not attached.with_actor(reader).has_access("delete")
    assert not File.objects.with_actor(reader).filter(pk=unrelated.pk).exists()
    channel.with_actor(owner).revoke_record_access("reader", reader)
    assert not File.objects.with_actor(reader).filter(pk=attached.pk).exists()
