"""The messaging decision table, sender envelopes and channel-owned thread identity."""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from rebac import (
    ObjectRef,
    PermissionDenied,
    RelationshipTuple,
    SubjectRef,
    actor_context,
    system_context,
    write_relationships,
)
from rebac.testing import assert_decisions

from angee.base.models.identity import public_id_of
from angee.graphql.testing.documents import execute_schema, result_data
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
    ThreadAttachment,
    ThreadFollower,
    ThreadNotification,
)
from angee.provenance import schema as provenance_schema
from angee.tags.testing.models import Tag, TagAssignment
from tests.chatterdemo.models import ChatterDoc
from tests.conftest import Backend, Drive, File, Vendor, addon_schema, create_platform_admin
from tests.decision_table import seat_cells
from tests.messaging_campaign import grant, make_user


def assert_access(row, actor, action, allowed):
    """Object checks and SQL scopes must agree on the same row."""
    assert row.with_actor(actor).has_access(action) is allowed, (row._meta.label, actor.username, action)
    assert type(row).objects.with_actor(actor).with_action(action).scoped().filter(pk=row.pk).exists() is allowed


SENDER_HISTORY = "query SenderHistory($model: String!, $id: ID!) { history(model: $model, id: $id) { total } }"


def _handle_history(actor, handle):
    """Read the Handle's changesets through the provenance owner's record gate."""
    result = execute_schema(
        addon_schema(provenance_schema.schemas, "console"), SENDER_HISTORY,
        {"model": "parties.Handle", "id": public_id_of(handle)}, user=actor,
    )
    return result_data(result)["history"]


def test_message_reader_sees_the_sender_envelope_never_the_directory_handle(db):
    owner, reader = (make_user(name) for name in ("sender-source-owner", "sender-source-reader"))
    with system_context(reason="source sender read fixtures"):
        party = Party.objects.create(display_name="Private sender Party", notes="Private", created_by=owner)
        sender = Handle.objects.create(
            platform="email", value="sender@example.test", label="Accounts desk", is_verified=True,
            party=party, party_link_confirmed=True, controller=owner, metadata={"crm": "private"},
            created_by=owner,
        )
        vendor = Vendor.objects.create(slug="sender-source", display_name="Sender source")
        channel = Channel.objects.create(vendor=vendor, owner=owner, backend_class="manual")
        message = Message.objects.create(
            channel=channel, sender=sender, platform="email",
            metadata={"headers": {"From": ["Sender <sender@example.test>"]}},
        )
        tag = Tag.objects.create(name="Operator note")
        (edge,) = TagAssignment.objects.attach("parties/handle", sender.sqid, [tag.sqid])
    channel.with_actor(owner).grant_record_access("reader", reader)
    assert_access(message, reader, "read", True)
    assert message.source_header_values("from", actor=reader) == ("Sender <sender@example.test>",)
    # The envelope belongs to the message; the Handle row and everything gated by
    # its read (curated fields, operator tags, change history) stay directory data.
    assert_access(sender, reader, "read", False)
    assert not Handle.objects.with_actor(reader).filter(pk=sender.pk).values("label", "controller").exists()
    assert not TagAssignment.objects.with_actor(reader).filter(pk=edge.pk).exists()
    assert _handle_history(reader, sender) is None
    assert_access(party, reader, "read", False)

    channel.with_actor(owner).revoke_record_access("reader", reader)
    with pytest.raises(PermissionDenied):
        message.source_header_values("from", actor=reader)

    # Directory access is its own explicit grant on the Handle, never the message's.
    sender.with_actor(owner).grant_record_access("reader", reader)
    assert_access(sender, reader, "read", True)
    assert_access(sender, reader, "write", False)
    assert TagAssignment.objects.with_actor(reader).filter(pk=edge.pk).exists()
    assert _handle_history(reader, sender) is not None
    assert_access(party, reader, "read", False)
    assert_access(message, reader, "read", False)


def test_the_policy_decides_every_messaging_permission(db):
    """One world, every messaging permission and extension arm with an allow and a deny, and its read scopes."""

    seats = {name: make_user(name) for name in (
        "channel-reader", "replier", "annotator", "blind-annotator", "thread-owner", "thread-editor",
        "thread-reader", "message-reader", "message-editor", "both-reader", "follower",
        "recipient", "starrer", "author", "file-owner", "record-reader", "outsider",
    )}
    with system_context(reason="test.messaging.policy.service"):
        seats["channel-owner"] = get_user_model().objects.create_user(username="policy-channel-owner", kind="service")
    seats.update(admin=create_platform_admin("messaging-policy-admin"), anonymous=AnonymousUser())
    author = seats["author"]
    with system_context(reason="test.messaging.policy.world"):
        vendor = Vendor.objects.create(slug="messaging-policy", display_name="Policy")
        channel = Channel.objects.create(vendor=vendor, owner=seats["channel-owner"], backend_class="manual")
        channel_thread = Thread.objects.create(channel=channel, created_by=author)
        channel_message = Message.objects.create(channel=channel, thread=channel_thread, created_by=author)
        trashed = Message.objects.create(channel=channel, thread=channel_thread, is_trashed=True)
        thread = Thread.objects.create(owner=seats["thread-owner"], created_by=seats["thread-owner"])
        message = Message.objects.create(thread=thread, created_by=author)
        handle = Handle.objects.create(platform="email", value="policy@example.test")
        party = Party.objects.for_user(seats["follower"])
        backend_row = Backend.objects.create(slug="messaging-policy", label="Policy", backend_class="local")
        drive = Drive.objects.create(backend=backend_row, slug="messaging-policy", name="Policy")
        source_file = File.objects.create(drive=drive, filename="s.txt", content_hash="a" * 64,
                                          owner=seats["file-owner"], visibility="record")
        part_file = File.objects.create(drive=drive, filename="p.txt", content_hash="b" * 64,
                                        owner=seats["file-owner"], visibility="record")
        source = ThreadAttachment.objects.create(
            thread=thread, role="source", content_type=ContentType.objects.get_for_model(File),
            object_id=source_file.pk,
        )
        follower = ThreadFollower.objects.create(thread=thread, party=party, created_by=author)
        notification = ThreadNotification.objects.create(
            thread=thread, message=message, user=seats["recipient"], created_by=author,
        )
        part = Part.objects.create(message=message, position=0, file=part_file, created_by=author)
        edge = MessageEdge.objects.create(src=channel_message, dst=message, kind="quote", created_by=author)
        membership = Participant.objects.create(thread=thread, handle=handle, created_by=author)
        participant = Participant.objects.create(thread=thread, message=channel_message, handle=handle)
        reaction = Reaction.objects.create(message=message, handle=handle, reaction="ok", created_by=author)
        star = MessageStar.objects.create(message=message, user=seats["starrer"], created_by=author)
        thread_tag, message_tag = (
            TagAssignment.objects.create(
                tag=Tag.objects.create(name=f"Policy {row._meta.model_name}"),
                content_type=ContentType.objects.get_for_model(row), object_id=row.pk,
            )
            for row in (thread, message)
        )
        record = ChatterDoc.objects.create(title="Record")
        chatter = ThreadAttachment.objects.ensure_for_record(record).thread
        record_source = ThreadAttachment.objects.ensure_for_record(
            ChatterDoc.objects.create(title="Source"), role="source",
        ).thread
    for row, relation, seat in (
        (channel, "reader", "channel-reader"), (channel, "replier", "replier"), (channel, "annotator", "annotator"),
        (channel, "reader", "annotator"), (channel, "annotator", "blind-annotator"),
        (thread, "editor", "thread-editor"), (thread, "reader", "thread-reader"),
        (message, "reader", "message-reader"), (message, "editor", "message-editor"),
        (channel_message, "reader", "both-reader"), (message, "reader", "both-reader"),
        (record, "reader", "record-reader"),
    ):
        grant(row, relation, seats[seat])
    # Identity resolution runs as a provenance seed, never as a user.
    seats["identity"] = SubjectRef(ObjectRef("provenance/seed", "messaging-policy"))
    with system_context(reason="test.messaging.policy.identity"):
        write_relationships([RelationshipTuple(
            ObjectRef("provenance/role", "messaging_identity_source"), "member", seats["identity"],
        )])

    cells = seat_cells(
        seats.__getitem__,
        # messaging/channel, owned by a service account
        (channel, "create", "admin", "channel-owner"),
        (channel, "read", "channel-owner channel-reader replier admin", "outsider"),
        (channel, "reply", "channel-owner replier", "channel-reader"),
        (channel, "write", "channel-owner", "replier annotator"),
        (channel, "annotate", "channel-owner annotator", "channel-reader"),
        (channel, "delete", "channel-owner", "replier"),
        (channel, "write__owner", "channel-owner", "replier"),
        # messaging/thread, channel-bound and personal
        (thread, "create", "outsider", "anonymous"),
        (thread, "identity_source", "identity", "outsider"),
        (channel_thread, "read_granted", "channel-owner channel-reader", "outsider"),
        (thread, "read_granted", "thread-owner thread-editor thread-reader identity admin", "outsider author"),
        (thread, "read_inbox", "thread-reader", "outsider"),
        (thread, "read", "thread-reader", "outsider anonymous"),
        (chatter, "read", "record-reader", "outsider"),
        (chatter, "chatter_read", "record-reader", "outsider"),
        (record_source, "read", "", "outsider"),
        (chatter, "write", "", "record-reader"),
        (channel_thread, "write", "channel-owner", "channel-reader"),
        (thread, "write", "thread-owner thread-editor identity", "thread-reader"),
        (channel_thread, "annotate_granted", "annotator blind-annotator", "channel-reader"),
        (channel_thread, "annotate", "annotator", "blind-annotator"),
        (thread, "share", "thread-owner thread-editor", "thread-reader"),
        (thread, "delete", "thread-owner", "thread-editor"),
        (channel_thread, "delete", "channel-owner", "channel-reader author"),
        (thread, "transfer", "thread-owner admin", "thread-editor"),
        (thread, "write__owner", "thread-owner", "thread-editor"),
        # messaging/message, with a trashed channel message
        (message, "create", "outsider", "anonymous"),
        (message, "identity_source", "identity", "outsider"),
        (trashed, "conceal", "outsider", "anonymous"),
        (trashed, "withheld", "outsider", "anonymous"),
        (message, "withheld", "", "outsider"),
        (channel_message, "read_granted", "channel-reader", "outsider"),
        (message, "read_granted", "message-reader", "thread-reader"),
        (trashed, "withheld_keeper", "channel-owner identity", "channel-reader"),
        (trashed, "read", "channel-owner admin", "channel-reader"),
        (message, "read", "thread-reader message-reader", "outsider author"),
        (message, "read_inbox", "thread-reader", "outsider"),
        (channel_message, "send_held", "channel-owner", "replier"),
        (message, "write", "thread-editor message-editor", "thread-reader author"),
        (message, "annotate", "thread-editor", "thread-reader"),
        (message, "delete", "thread-owner message-editor", "thread-reader"),
        # dependents follow their parents, never their creator
        (source, "target_write", "file-owner", "thread-owner"),
        (source, "retains", "outsider", "anonymous"),
        (source, "create", "file-owner", "thread-owner"),
        (source, "read", "thread-reader", "outsider author"),
        (source, "write", "thread-editor", "thread-reader"),
        (source, "delete", "thread-editor file-owner", "thread-reader"),
        (follower, "create", "outsider", "anonymous"),
        (follower, "read", "follower thread-reader", "outsider author"),
        (follower, "write", "follower thread-editor", "thread-reader"),
        (follower, "delete", "follower thread-editor", "thread-reader"),
        (notification, "read_inbox", "recipient", "thread-reader"),
        (notification, "create", "outsider", "anonymous"),
        (notification, "read", "recipient thread-reader", "outsider author"),
        (notification, "write", "recipient", "thread-owner"),
        (notification, "delete", "recipient", "thread-owner"),
        (part, "create", "outsider", "anonymous"),
        (part, "read_inbox", "thread-reader", "outsider"),
        (part, "read", "thread-reader", "outsider author"),
        (part, "write", "thread-editor", "thread-reader"),
        (part, "delete", "thread-owner", "thread-editor"),
        (edge, "create", "outsider", "anonymous"),
        (edge, "read", "both-reader", "channel-owner thread-owner author"),
        (edge, "write", "admin", "channel-owner thread-owner"),
        (edge, "delete", "channel-owner thread-owner", "both-reader"),
        (membership, "create", "outsider", "anonymous"),
        (membership, "read", "thread-reader", "outsider"),
        (membership, "write", "thread-editor", "thread-reader"),
        (membership, "delete", "thread-owner", "thread-editor"),
        (participant, "read", "channel-reader thread-reader", "outsider"),
        (participant, "write", "channel-owner", "thread-editor"),
        (participant, "delete", "channel-owner", "thread-owner"),
        (reaction, "create", "outsider", "anonymous"),
        (reaction, "read", "thread-reader", "outsider author"),
        (reaction, "write", "thread-editor", "thread-reader"),
        (reaction, "delete", "thread-owner", "thread-editor"),
        (star, "create", "outsider", "anonymous"),
        (star, "read", "starrer thread-reader", "outsider author"),
        (star, "write", "starrer", "thread-owner"),
        (star, "delete", "starrer", "thread-owner"),
        # extension arms on parties, files and tags
        (party, "read_granted", "thread-reader", "outsider"),
        (part_file, "read", "thread-reader file-owner", "outsider"),
        (thread_tag, "target_read", "thread-reader", "outsider"),
        (thread_tag, "target_write", "thread-editor", "thread-reader"),
        (message_tag, "target_read", "message-reader", "outsider"),
        (message_tag, "target_write", "thread-editor", "message-reader"),
    )
    assert_decisions(
        checks=cells,
        accessible=(
            (seats["channel-reader"], "read", Message, (channel_message,)),
            (seats["thread-reader"], "read", Thread, (thread,)),
            (seats["recipient"], "read_inbox", ThreadNotification, (notification,)),
            (seats["author"], "read", Message, ()),
        ),
    )


def test_channel_thread_is_ownerless_and_channel_transfer_moves_delete_access(db):
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


def test_channel_identity_creation_rolls_back_as_one_transaction(db):
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


def test_channel_bound_thread_refuses_an_explicit_personal_owner(db):
    """The channel owns its threads even outside provider identity resolution."""
    owner = make_user("channel-invariant-owner")
    with system_context(reason="test.messaging.channel-invariant"):
        vendor = Vendor.objects.create(slug="channel-invariant", display_name="Channel invariant")
        channel = Channel.objects.create(vendor=vendor, owner=owner, backend_class="manual")
    with actor_context(owner), pytest.raises(ValidationError, match="owner"):
        Thread.objects.create(channel=channel, owner=owner)


