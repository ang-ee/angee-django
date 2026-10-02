"""Ownership and dependent permissions, checked both as objects and SQL scopes."""

import pytest
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import transaction
from rebac import PermissionDenied, actor_context, system_context
from rebac.backends import backend
from rebac.evaluator import evaluator_scope
from rebac.resources import model_resource_type

from angee.messaging.testing.models import (
    Channel,
    Handle,
    Message,
    MessageEdge,
    MessageStar,
    Part,
    Participant,
    Party,
    Reaction,
    Thread,
    ThreadActivity,
    ThreadAttachment,
    ThreadFollower,
    ThreadNotification,
    TrackingValue,
)
from tests.conftest import Vendor
from tests.messaging_campaign import grant, make_user
from tests.test_messaging_access import messaging_access_schema as messaging_access_schema
from tests.test_spaces import spaces_tables as spaces_tables

pytestmark = pytest.mark.django_db(transaction=True)


def assert_access(row, actor, action, allowed):
    """Object checks and SQL scopes must agree on the same row."""
    assert row.with_actor(actor).has_access(action) is allowed, (row._meta.label, actor.username, action)
    assert type(row).objects.with_actor(actor).with_action(action).scoped().filter(pk=row.pk).exists() is allowed


def test_thread_transfer_requires_owner_and_clear_preserves_attribution(composed_tables, messaging_access_schema):
    owner, editor, successor = (make_user(name) for name in ("owner", "editor", "successor"))
    with actor_context(owner):
        thread = Thread.objects.create()
    grant(thread, "editor", editor)
    assert_access(thread, editor, "write", True)
    assert_access(thread, editor, "transfer", False)
    with pytest.raises(PermissionDenied):
        thread.with_actor(editor).transfer_ownership(successor)
    thread.with_actor(owner).transfer_ownership(successor)
    assert thread.owner_id == successor.pk and thread.created_by_id == owner.pk
    assert_access(thread, owner, "read", False)
    assert_access(thread, successor, "transfer", True)
    thread.with_actor(successor).transfer_ownership(None)
    assert thread.owner_id is None and thread.created_by_id == owner.pk
    assert_access(thread, successor, "read", False)


def test_owner_column_uses_transfer_gate_when_declared_on_the_base(composed_tables, messaging_access_schema):
    definition = backend().schema().get_definition("messaging/thread")
    if "write__owner" not in {permission.name for permission in definition.permissions}:
        pytest.skip("Merged messaging schema has no write__owner gate; owner sweep recorded in HANDOFF.md")
    owner, editor, successor = (make_user(name) for name in ("column-owner", "column-editor", "column-next"))
    with actor_context(owner):
        thread = Thread.objects.create()
    grant(thread, "editor", editor)
    assert_access(thread, editor, "write__owner", False)
    with actor_context(editor), pytest.raises(PermissionDenied):
        thread = Thread.objects.get(pk=thread.pk)
        thread.owner = successor
        thread.save(update_fields=("owner",))


@pytest.mark.parametrize("label", [
    "thread", "message", "thread_attachment", "thread_follower", "thread_activity", "thread_notification",
    "part", "tracking_value", "message_edge", "participant", "reaction", "message_star",
])
def test_dependents_inherit_parent_permissions_without_creator_access(activity_catalog, messaging_access_schema, label):
    owner, poster, reader, author, recipient = (
        make_user(name) for name in ("root-owner", "poster", "reader", "attributed-author", "recipient")
    )
    with system_context(reason="test.messaging.permission-tree"):
        thread = Thread.objects.create(owner=owner, created_by=owner)
        message = Message.objects.create(thread=thread, created_by=author)
        other = Message.objects.create(thread=thread)
        party = Party.objects.for_user(recipient)
        handle = Handle.objects.create(platform="email", value="recipient@example.com")
        attachment = ThreadAttachment.objects.create(
            thread=thread, role="source", content_type=ContentType.objects.get_for_model(Thread), object_id=thread.pk,
        )
        rows = {
            "thread": thread,
            "message": message,
            "thread_attachment": attachment,
            "thread_follower": ThreadFollower.objects.create(thread=thread, party=party, created_by=author),
            "thread_activity": ThreadActivity.objects.create(
                thread=thread, attachment=attachment, user=recipient, created_by=author,
            ),
            "thread_notification": ThreadNotification.objects.create(
                thread=thread, message=message, user=recipient, created_by=author,
            ),
            "part": Part.objects.create(message=message, position=0, created_by=author),
            "tracking_value": TrackingValue.objects.create(message=message, field_name="status", created_by=author),
            "message_edge": MessageEdge.objects.create(src=message, dst=other, kind="quote", created_by=author),
            "participant": Participant.objects.create(thread=thread, message=message, handle=handle, created_by=author),
            "reaction": Reaction.objects.create(message=message, handle=handle, reaction="ok", created_by=author),
            "message_star": MessageStar.objects.create(message=message, user=recipient, created_by=author),
        }
    grant(thread, "editor", poster)
    grant(thread, "reader", reader)
    row = rows[label]
    # As in a real request, share schema/evaluator metadata across this read-only
    # matrix. All fixtures and grants are committed before the scope begins.
    with evaluator_scope():
        for actor in (owner, poster, reader):
            assert_access(row, actor, "read", True)
        for action in ("read", "write", "delete"):
            assert_access(row, author, action, False)
        recipient_only = label in {"thread_notification", "message_star"}
        for actor in (owner, poster):
            assert_access(row, actor, "write", not recipient_only)
        assert_access(row, reader, "write", False)
        assert_access(row, reader, "delete", False)
        assert_access(row, owner, "delete", not recipient_only)
        assert_access(row, poster, "delete", label in {
            "thread_attachment", "thread_follower", "thread_activity", "tracking_value",
        })
    if label != "thread":
        with pytest.raises(FieldDoesNotExist):
            row._meta.get_field("owner")


def test_channel_thread_is_ownerless_and_channel_transfer_moves_delete_access(composed_tables, messaging_access_schema):
    owner, successor = make_user("channel-owner"), make_user("channel-successor")
    with system_context(reason="test.messaging.channel-thread"):
        vendor = Vendor.objects.create(slug="campaign", display_name="Campaign")
        channel = Channel.objects.create(vendor=vendor, owner=owner, backend_class="manual")
        thread, created = Thread.objects.get_or_create_by_external_id(
            platform="email", external_id="campaign-conversation",
            defaults={"channel": channel, "created_by": owner},
        )
        again, repeated = Thread.objects.get_or_create_by_external_id(
            platform="email", external_id="campaign-conversation", defaults={"channel": channel},
        )
    assert created and not repeated and thread.pk == again.pk
    assert thread.owner_id is None and thread.created_by_id == owner.pk
    assert_access(thread, owner, "delete", True)
    with system_context(reason="test.messaging.channel-transfer"):
        Channel._base_manager.filter(pk=channel.pk).update(owner=successor)
    assert_access(thread, owner, "delete", False)
    assert_access(thread, successor, "delete", True)
    with actor_context(successor):
        Thread.objects.get(pk=thread.pk).delete()
    assert not Thread._base_manager.filter(pk=thread.pk).exists()


def test_channel_identity_creation_rolls_back_as_one_transaction(composed_tables):
    owner = make_user("rollback-owner")
    with system_context(reason="test.messaging.rollback"):
        vendor = Vendor.objects.create(slug="rollback", display_name="Rollback")
        channel = Channel.objects.create(vendor=vendor, owner=owner, backend_class="manual")
        with pytest.raises(RuntimeError, match="abort"), transaction.atomic():
            Thread.objects.get_or_create_by_external_id(
                platform="email", external_id="rolled-back", defaults={"channel": channel, "created_by": owner},
            )
            raise RuntimeError("abort")
    assert not Thread._base_manager.filter(external_id="rolled-back").exists()


def test_channel_bound_thread_refuses_an_explicit_personal_owner(composed_tables):
    """The channel owns its threads even outside provider identity resolution."""
    owner = make_user("channel-invariant-owner")
    with system_context(reason="test.messaging.channel-invariant"):
        vendor = Vendor.objects.create(slug="channel-invariant", display_name="Channel invariant")
        channel = Channel.objects.create(vendor=vendor, owner=owner, backend_class="manual")
    with actor_context(owner), pytest.raises(ValidationError, match="owner"):
        Thread.objects.create(channel=channel, owner=owner)


def test_cross_channel_quote_edge_reads_intersection_and_deletes_union(composed_tables, messaging_access_schema):
    owners = [make_user(f"endpoint-{index}") for index in range(2)]
    with system_context(reason="test.messaging.cross-channel"):
        vendor = Vendor.objects.create(slug="endpoints", display_name="Endpoints")
        channels = [Channel.objects.create(vendor=vendor, owner=user, backend_class="manual") for user in owners]
        messages = [Message.objects.create(channel=channel) for channel in channels]
        edge = MessageEdge.objects.create(src=messages[0], dst=messages[1], kind="quote")
    for owner in owners:
        assert_access(edge, owner, "read", False)
        assert_access(edge, owner, "write", False)
        assert_access(edge, owner, "delete", True)
    both = make_user("both-endpoints")
    for message in messages:
        grant(message, "reader", both)
    assert_access(edge, both, "read", True)
    assert_access(edge, both, "delete", False)


def test_participant_mutation_follows_message_or_thread_membership_only(composed_tables, messaging_access_schema):
    thread_owner, message_owner, poster = (
        make_user(name) for name in ("thread-owner", "message-owner", "thread-poster")
    )
    with system_context(reason="test.messaging.participants"):
        thread = Thread.objects.create(owner=thread_owner)
        other = Thread.objects.create(owner=message_owner)
        message = Message.objects.create(thread=other)
        handle = Handle.objects.create(platform="email", value="participant@example.com")
        participant = Participant.objects.create(thread=thread, message=message, handle=handle)
        membership = Participant.objects.create(thread=thread, handle=handle)
        orphan = Participant.objects.create(handle=handle, created_by=thread_owner)
    grant(thread, "editor", poster)
    for actor in (thread_owner, poster):
        assert_access(participant, actor, "read", True)
        assert_access(participant, actor, "write", False)
        assert_access(participant, actor, "delete", False)
        assert_access(membership, actor, "write", True)
        assert_access(membership, actor, "delete", actor == thread_owner)
        for action in ("read", "write", "delete"):
            assert_access(orphan, actor, action, False)
    for action in ("read", "write", "delete"):
        assert_access(participant, message_owner, action, True)


def test_every_messaging_permission_compiles_without_enumeration(spaces_tables, messaging_access_schema,
                                                              django_assert_num_queries):
    reader = make_user("sql-reader")
    models = (Thread, Message, ThreadAttachment, ThreadFollower, ThreadActivity, ThreadNotification,
              Part, TrackingValue, MessageEdge, Participant, Reaction, MessageStar)
    # Generic record arms resolve Django content types during SQL rendering.
    # Warm that native metadata cache as well as REBAC's schema before measuring.
    ContentType.objects.get_for_models(*apps.get_models())
    with actor_context(reader), evaluator_scope():
        schema = backend().schema()
        for model in models:
            resource_type = model_resource_type(model)
            definition = schema.get_definition(resource_type)
            for permission in definition.permissions:
                scoped = model.objects.with_actor(reader).with_action(permission.name).scoped()
                assert list(scoped.values_list("pk", flat=True)) == []
