"""Tests for the messaging ingest write path (the channel-sync map).

The concrete messaging/parties models are composed here the way the composer folds
each abstract source model onto one runtime table, so the manager write path runs
against real tables. The cases pin the ingest invariants the module docstring
promises: idempotency on ``(platform, external_id)``, null-byte stripping, RFC-5322
thread resolution, the monotonic/never-crashing counter bump, and quote-edge
direction.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.db import connection, models
from django.db.models.signals import post_save
from django.test.utils import CaptureQueriesContext
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

from angee.base.mixins import AuditMixin, SqidMixin
from angee.base.models import AngeeModel
from angee.graphql import publishing
from angee.graphql.access import ChangeReadGate
from angee.graphql.events import ChangePayload
from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedPart, ParsedRecipient
from angee.messaging.managers import normalize_subject, strip_null_bytes
from angee.messaging.models import Fragment as AbstractFragment
from angee.messaging.models import Message as AbstractMessage
from angee.messaging.models import MessageEdge as AbstractMessageEdge
from angee.messaging.models import MessageStar as AbstractMessageStar
from angee.messaging.models import MessageSubtype as AbstractMessageSubtype
from angee.messaging.models import Part as AbstractPart
from angee.messaging.models import Participant as AbstractParticipant
from angee.messaging.models import Reaction as AbstractReaction
from angee.messaging.models import Thread as AbstractThread
from angee.messaging.models import ThreadActivity as AbstractThreadActivity
from angee.messaging.models import ThreadAttachment as AbstractThreadAttachment
from angee.messaging.models import ThreadedModelMixin
from angee.messaging.models import ThreadFollower as AbstractThreadFollower
from angee.messaging.models import ThreadNotification as AbstractThreadNotification
from angee.messaging.models import TrackingValue as AbstractTrackingValue
from angee.parties.models import Directory as AbstractDirectory
from angee.parties.models import Folder as AbstractContactFolder
from angee.parties.models import Handle as AbstractHandle
from angee.parties.models import Party as AbstractParty
from angee.social.models import MessagePublic, ThreadPublic
from tests.chatterdemo.models import ChatterDoc, TrackedRecordChild, TrackedRecordParent
from tests.conftest import (
    IAM_CONNECTION_TEST_MODELS,
    INTEGRATE_TEST_MODELS,
    STORAGE_TEST_MODELS,
    Backend,
    Drive,
    Integration,
    MimeType,
    PostMetrics,
    _clear_model_tables,
    _create_missing_tables,
    make_integration,
)
from tests.conftest import (
    File as StorageFile,
)


class Directory(Integration, AbstractDirectory):
    """Concrete contacts directory (Integration child) used by messaging tests."""

    class Meta(AbstractDirectory.Meta):
        """Django model options for the canonical test directory."""

        abstract = False
        app_label = "parties"
        db_table = "test_parties_directory"
        rebac_resource_type = "parties/directory"
        rebac_id_attr = "sqid"


class Folder(AbstractContactFolder):
    """Concrete parties folder used by messaging tests."""

    class Meta(AbstractContactFolder.Meta):
        """Django model options for the canonical test contacts folder."""

        abstract = False
        app_label = "parties"
        db_table = "test_parties_folder"
        rebac_resource_type = "parties/folder"
        rebac_id_attr = "sqid"


class Party(AbstractParty):
    """Concrete party used by messaging tests."""

    class Meta(AbstractParty.Meta):
        """Django model options for the canonical test party."""

        abstract = False
        app_label = "parties"
        db_table = "test_parties_party"
        rebac_resource_type = "parties/party"
        rebac_id_attr = "sqid"


class Handle(AbstractHandle):
    """Concrete handle (a message sender/recipient) used by messaging tests."""

    class Meta(AbstractHandle.Meta):
        """Django model options for the canonical test handle."""

        abstract = False
        app_label = "parties"
        db_table = "test_parties_handle"
        rebac_resource_type = "parties/handle"
        rebac_id_attr = "sqid"


class Fragment(AbstractFragment):
    """Concrete content-addressed fragment used by messaging tests.

    Unscoped substrate (no REBAC type), like the abstract source model.
    """

    class Meta(AbstractFragment.Meta):
        """Django model options for the canonical test fragment."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_fragment"


class Thread(ThreadPublic, AbstractThread):
    """Concrete thread used by messaging tests.

    Folds social's same-row ``ThreadPublic`` extension (``subject_url``/``body``/
    ``tags``/``parent``) onto the one table, the way the composer emits
    ``Thread(ThreadExtension1, AbstractThread)`` now that social is a composed base
    addon — so the public-post payload rides the shared thread row.
    """

    class Meta(AbstractThread.Meta):
        """Django model options for the canonical test thread."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_thread"
        rebac_resource_type = "messaging/thread"
        rebac_id_attr = "sqid"


class ThreadAttachment(AbstractThreadAttachment):
    """Concrete record-thread attachment used by messaging tests."""

    class Meta(AbstractThreadAttachment.Meta):
        """Django model options for the canonical test thread attachment."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_thread_attachment"
        rebac_resource_type = "messaging/thread_attachment"
        rebac_id_attr = "sqid"


class ThreadFollower(AbstractThreadFollower):
    """Concrete record-thread follower used by messaging tests."""

    class Meta(AbstractThreadFollower.Meta):
        """Django model options for the canonical test thread follower."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_thread_follower"
        rebac_resource_type = "messaging/thread_follower"
        rebac_id_attr = "sqid"


class ThreadActivity(AbstractThreadActivity):
    """Concrete record-thread activity used by messaging tests."""

    class Meta(AbstractThreadActivity.Meta):
        """Django model options for the canonical test thread activity."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_thread_activity"
        rebac_resource_type = "messaging/thread_activity"
        rebac_id_attr = "sqid"


class MessageSubtype(AbstractMessageSubtype):
    """Concrete message subtype used by messaging tests."""

    class Meta(AbstractMessageSubtype.Meta):
        """Django model options for the canonical test message subtype."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_message_subtype"


class Message(MessagePublic, AbstractMessage):
    """Concrete message used by messaging tests.

    Folds social's same-row ``MessagePublic`` extension (``is_original_post``) onto
    the one table, the way the composer emits ``Message(MessageExtension1,
    AbstractMessage)`` now that social is a composed base addon.
    """

    class Meta(AbstractMessage.Meta):
        """Django model options for the canonical test message."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_message"
        rebac_resource_type = "messaging/message"
        rebac_id_attr = "sqid"


class ThreadNotification(AbstractThreadNotification):
    """Concrete notification used by messaging tests."""

    class Meta(AbstractThreadNotification.Meta):
        """Django model options for the canonical test notification."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_thread_notification"
        rebac_resource_type = "messaging/thread_notification"
        rebac_id_attr = "sqid"


class Reaction(AbstractReaction):
    """Concrete message reaction used by messaging tests."""

    class Meta(AbstractReaction.Meta):
        """Django model options for the canonical test reaction."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_reaction"
        rebac_resource_type = "messaging/reaction"
        rebac_id_attr = "sqid"


class MessageStar(AbstractMessageStar):
    """Concrete message star used by messaging tests."""

    class Meta(AbstractMessageStar.Meta):
        """Django model options for the canonical test message star."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_message_star"
        rebac_resource_type = "messaging/message_star"
        rebac_id_attr = "sqid"


class TrackingValue(AbstractTrackingValue):
    """Concrete tracking value used by messaging tests."""

    class Meta(AbstractTrackingValue.Meta):
        """Django model options for the canonical test tracking value."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_tracking_value"
        rebac_resource_type = "messaging/tracking_value"
        rebac_id_attr = "sqid"


class Part(AbstractPart):
    """Concrete message body part used by messaging tests."""

    class Meta(AbstractPart.Meta):
        """Django model options for the canonical test part."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_part"
        rebac_resource_type = "messaging/part"
        rebac_id_attr = "sqid"


class MessageEdge(AbstractMessageEdge):
    """Concrete cross-message edge used by messaging tests."""

    class Meta(AbstractMessageEdge.Meta):
        """Django model options for the canonical test message edge."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_message_edge"
        rebac_resource_type = "messaging/message_edge"
        rebac_id_attr = "sqid"


class Participant(AbstractParticipant):
    """Concrete participant used by messaging tests."""

    class Meta(AbstractParticipant.Meta):
        """Django model options for the canonical test participant."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_participant"
        rebac_resource_type = "messaging/participant"
        rebac_id_attr = "sqid"


class ThreadedTicket(SqidMixin, AuditMixin, ThreadedModelMixin, AngeeModel):
    """Concrete model that opts into record chatter for messaging tests."""

    sqid_prefix = "tkt_"
    thread_tracking_fields = ("title", "status")
    thread_suggested_recipient_fields = ("assigned_user",)

    title = models.CharField(max_length=160)
    assigned_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    status = models.CharField(
        max_length=32,
        choices=(("open", "Open"), ("closed", "Closed")),
        default="open",
    )

    class Meta:
        """Django model options for the canonical threaded test record."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_threaded_ticket"

    def __str__(self) -> str:
        """Return the ticket title for thread subjects."""

        return self.title


class BroadcastRoom(SqidMixin, AuditMixin, ThreadedModelMixin, AngeeModel):
    """A threaded host that opts its chatter into the ``changes(Thread)`` stream.

    Stands in for a chat room: ``thread_broadcasts_changes = True`` flips the F-stream
    opt-in, so a post on this host's thread emits a member-gated ``threadChanged``
    while every non-opted record thread (``ThreadedTicket``) stays silent.
    """

    sqid_prefix = "brm_"
    thread_broadcasts_changes = True

    title = models.CharField(max_length=160)

    class Meta:
        """Django model options for the broadcasting threaded test record."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_broadcast_room"

    def __str__(self) -> str:
        """Return the room title for thread subjects."""

        return self.title


# Parents before children so the on-demand table creation satisfies FK targets.
MESSAGING_TEST_MODELS = (
    *STORAGE_TEST_MODELS,
    *IAM_CONNECTION_TEST_MODELS,
    *INTEGRATE_TEST_MODELS,
    Directory,
    Folder,
    Party,
    Handle,
    Fragment,
    Thread,
    ThreadAttachment,
    ThreadFollower,
    ThreadActivity,
    MessageSubtype,
    Message,
    PostMetrics,
    ThreadNotification,
    Reaction,
    MessageStar,
    TrackingValue,
    Part,
    MessageEdge,
    Participant,
    ThreadedTicket,
    BroadcastRoom,
    ChatterDoc,
    TrackedRecordParent,
    TrackedRecordChild,
)

_AT = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def messaging_tables() -> Iterator[None]:
    """Create the concrete messaging/parties tables and sync the REBAC schema."""

    created_models = _create_missing_tables(MESSAGING_TEST_MODELS)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        _clear_model_tables(MESSAGING_TEST_MODELS)
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


@pytest.fixture
def channel(messaging_tables: None) -> Any:
    """Provide an Integration row to stand in as the ingest channel."""

    del messaging_tables
    return make_integration("msgchan")


def _parsed(
    external_id: str,
    *,
    subject: str = "Hello",
    sent_at: datetime | None = None,
    text: str = "Body text",
    references: tuple[str, ...] = (),
    in_reply_to: str = "",
) -> ParsedMessage:
    """Build a neutral ParsedMessage with a single text body part."""

    return ParsedMessage(
        external_id=external_id,
        platform="email",
        subject=subject,
        sender=ParsedHandle(platform="email", value="alice@example.com", display_name="Alice"),
        recipients=(ParsedRecipient(handle=ParsedHandle(platform="email", value="bob@example.com"), role="to"),),
        sent_at=sent_at,
        in_reply_to=in_reply_to,
        references=references,
        body=ParsedPart(type="text/plain", role="body", text=text),
    )


def _ingest(messages: list[ParsedMessage], *, channel: Any) -> int:
    """Run the ingest the way the scheduler does — elevated under system_context.

    ``ingest`` returns the landed message rows; these cases assert on the count, so
    this helper reports ``len(...)`` — the same shape ``Channel.sync`` takes.
    """

    with system_context(reason="test messaging ingest"):
        return len(Message.objects.ingest(messages, channel=channel))


def _storage_drive(tmp_path: Path, *, owner: Any) -> Any:
    """Create the default storage drive used by attachment tests."""

    backend = Backend._base_manager.create(
        slug="local",
        label="Local",
        backend_class="local",
        backend_config={"root": str(tmp_path), "base_url": "/media/"},
    )
    MimeType._base_manager.get_or_create(
        mime_type="text/plain",
        defaults={"category": "text", "label": "Text"},
    )
    MimeType._base_manager.get_or_create(
        mime_type="application/octet-stream",
        defaults={"category": "other", "label": "Binary file"},
    )
    return Drive._base_manager.create(
        backend=backend,
        slug="assets",
        name="Assets",
        prefix="assets",
        created_by=owner,
    )


def test_strip_null_bytes_recurses_through_containers() -> None:
    """Null bytes are removed from strings nested in dicts/lists/tuples."""

    assert strip_null_bytes("a\x00b") == "ab"
    assert strip_null_bytes({"k": "v\x00"}) == {"k": "v"}
    assert strip_null_bytes(["x\x00", ("y\x00",)]) == ["x", ("y",)]


def test_normalize_subject_strips_reply_prefixes() -> None:
    """Repeated Re:/Fwd: prefixes are stripped and whitespace collapsed for matching."""

    assert normalize_subject("Re: Fwd: Hello") == "Hello"
    assert normalize_subject("  RE: re: Status  ") == "Status"
    assert normalize_subject("No prefix") == "No prefix"


@pytest.mark.django_db(transaction=True)
def test_threaded_model_resolves_one_chatter_thread(messaging_tables: None) -> None:
    """A threaded model row owns one stable chatter thread attachment."""

    del messaging_tables
    with system_context(reason="test threaded model setup"):
        ticket = ThreadedTicket.objects.create(title="Escalation")
        first = ticket.message_thread()
        second = ticket.message_thread()

    assert first is not None
    assert first.pk == second.pk
    assert first.subject == "Escalation"
    assert ThreadAttachment._base_manager.count() == 1
    attachment = ThreadAttachment._base_manager.get()
    assert attachment.thread_id == first.pk
    assert attachment.object_id == ticket.pk
    assert attachment.role == "chatter"


@pytest.mark.django_db(transaction=True)
def test_threaded_model_posts_internal_message(messaging_tables: None) -> None:
    """Posting on a threaded model writes a message body and advances the thread."""

    del messaging_tables
    with system_context(reason="test threaded model post"):
        ticket = ThreadedTicket.objects.create(title="Customer reply")
        message = ticket.message_post("Please follow up with the customer.")

    thread = Thread._base_manager.get()
    assert message.thread_id == thread.pk
    assert message.direction == "internal"
    assert message.status == "sent"
    assert message.message_type == "comment"
    assert message.subtype is not None
    assert message.subtype.key == "comment"
    assert message.subtype.model_label == "messaging.ThreadedTicket"
    assert message.preview == "Please follow up with the customer."
    assert thread.message_count == 1
    assert thread.last_message_at == message.sent_at
    part = Part._base_manager.select_related("fragment").get(message=message)
    assert part.role == "body"
    assert part.fragment.text == "Please follow up with the customer."


@pytest.mark.django_db(transaction=True)
def test_threaded_model_logs_internal_note(messaging_tables: None) -> None:
    """Logging a note writes an Odoo-style notification with the note subtype."""

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test threaded model log note setup"):
        user = user_model.objects.create_user(username="note-author", email="note-author@example.com")
        ticket = ThreadedTicket.objects.create(title="Internal memo")
    with actor_context(user):
        message = ticket.message_log("Keep this internal.")

    assert message.message_type == "notification"
    assert message.subtype is not None
    assert message.subtype.key == "note"
    assert message.preview == "Keep this internal."
    assert not ThreadFollower._base_manager.filter(user=user).exists()


@pytest.mark.django_db(transaction=True)
def test_threaded_model_posts_reply(messaging_tables: None) -> None:
    """Posting a reply stores the parent message inside the same chatter thread."""

    del messaging_tables
    with system_context(reason="test threaded model reply"):
        ticket = ThreadedTicket.objects.create(title="Reply case")
        parent = ticket.message_post("Original message.")
        reply = ticket.message_post("Reply body.", parent=parent)
        other = ThreadedTicket.objects.create(title="Other case")
        other_parent = other.message_post("Different thread.")

    assert reply.parent_id == parent.pk
    assert list(Message._base_manager.filter(parent=parent).values_list("pk", flat=True)) == [reply.pk]
    with system_context(reason="test threaded model reply guard"):
        with pytest.raises(ValueError, match="Parent message does not belong to this thread."):
            ticket.message_post("Wrong parent.", parent=other_parent)


@pytest.mark.django_db(transaction=True)
def test_threaded_model_toggles_message_reaction(messaging_tables: None) -> None:
    """Reacting to a chatter message uses a stable user handle and same-thread guard."""

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test threaded model reaction setup"):
        user = user_model.objects.create_user(username="reactor", email="reactor@example.com")
        ticket = ThreadedTicket.objects.create(title="Reaction case")
        message = ticket.message_post("Original message.")
        other = ThreadedTicket.objects.create(title="Other reaction case")
        other_message = other.message_post("Different thread.")

    with system_context(reason="test threaded model reaction add"):
        ticket.message_reaction(message, reaction="👍", user=user)

    reaction = Reaction._base_manager.select_related("handle").get(message=message)
    assert reaction.reaction == "👍"
    assert reaction.created_by_id == user.pk
    assert reaction.handle is not None
    assert reaction.handle.platform == "email"
    assert reaction.handle.value == "reactor@example.com"

    with system_context(reason="test threaded model reaction remove"):
        ticket.message_reaction(message, reaction="👍", user=user)
    assert not Reaction._base_manager.filter(message=message).exists()

    with system_context(reason="test threaded model reaction guard"):
        with pytest.raises(ValueError, match="Message does not belong to this record thread."):
            ticket.message_reaction(other_message, reaction="👍", user=user)


@pytest.mark.django_db(transaction=True)
def test_threaded_model_toggles_message_star(messaging_tables: None) -> None:
    """Starring a chatter message is per-user and same-thread guarded."""

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test threaded model star setup"):
        user = user_model.objects.create_user(username="starred-user", email="starred@example.com")
        other_user = user_model.objects.create_user(username="other-starred-user", email="other-starred@example.com")
        ticket = ThreadedTicket.objects.create(title="Star case")
        message = ticket.message_post("Important message.")
        second = ticket.message_post("Another important message.")
        other = ThreadedTicket.objects.create(title="Other star case")
        other_message = other.message_post("Different thread.")

    with system_context(reason="test threaded model star add"):
        assert ticket.message_set_starred(message, user=user) is True

    star = MessageStar._base_manager.get(message=message)
    assert star.user_id == user.pk
    assert star.created_by_id == user.pk
    with system_context(reason="test threaded model star read"):
        assert ticket.message_starred(message, user=user) is True
        assert ticket.message_starred(message, user=other_user) is False

    with system_context(reason="test threaded model star remove"):
        assert ticket.message_set_starred(message, user=user) is False
    assert not MessageStar._base_manager.filter(message=message, user=user).exists()

    with system_context(reason="test threaded model star unstar all"):
        ticket.message_set_starred(message, user=user, starred=True)
        ticket.message_set_starred(second, user=user, starred=True)
        ticket.message_set_starred(second, user=other_user, starred=True)
        assert ticket.message_unstar_all(user=user) == 2

    assert not MessageStar._base_manager.filter(user=user).exists()
    assert MessageStar._base_manager.filter(user=other_user, message=second).exists()

    with system_context(reason="test threaded model star guard"):
        with pytest.raises(ValueError, match="Message does not belong to this record thread."):
            ticket.message_set_starred(other_message, user=user)


@pytest.mark.django_db(transaction=True)
def test_threaded_model_unlinks_chatter_message(messaging_tables: None) -> None:
    """Deleting a chatter message removes its owned rows and repairs thread counters."""

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test threaded model unlink setup"):
        user = user_model.objects.create_user(username="unlinker", email="unlinker@example.com")
        ticket = ThreadedTicket.objects.create(title="Unlink case")
        first = ticket.message_post("First message.")
        second = ticket.message_post("Second message.")
        ticket.message_reaction(first, reaction="👍", user=user)
        other = ThreadedTicket.objects.create(title="Other unlink case")
        other_message = other.message_post("Different thread.")

    with system_context(reason="test threaded model unlink"):
        thread = ticket.message_unlink(first)

    thread.refresh_from_db()
    assert thread.message_count == 1
    assert thread.last_message_at == second.sent_at
    assert not Message._base_manager.filter(pk=first.pk).exists()
    assert not Part._base_manager.filter(message_id=first.pk).exists()
    assert not Reaction._base_manager.filter(message_id=first.pk).exists()
    assert list(Message._base_manager.values_list("pk", flat=True).order_by("pk")) == [
        second.pk,
        other_message.pk,
    ]

    with system_context(reason="test threaded model unlink guard"):
        with pytest.raises(ValueError, match="Message does not belong to this record thread."):
            ticket.message_unlink(other_message)


@pytest.mark.django_db(transaction=True)
def test_threaded_record_delete_tears_down_chatter_graph(messaging_tables: None) -> None:
    """Hard-deleting a chattered record collects its whole private thread subtree (M1).

    The record's chatter thread is private to it, so deleting the record must remove its
    thread, attachment, messages, followers, notifications, and activities rather than
    orphaning them (a leftover attachment on a reused primary key would mis-resolve). A
    sibling record's chatter is untouched.
    """

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test threaded model delete cascade setup"):
        author = user_model.objects.create_user(username="cascade-author", email="cascade-author@example.com")
        watcher = user_model.objects.create_user(username="cascade-watcher", email="cascade-watcher@example.com")
        ticket = ThreadedTicket.objects.create(title="Cascade case")
        ticket.message_subscribe(user=watcher)
        ticket.message_post("Body to be collected.")
        ticket.activity_schedule(user=author, summary="Follow up", due_date=_AT.date())
        survivor = ThreadedTicket.objects.create(title="Survivor case")
        survivor_message = survivor.message_post("Untouched body.")

    thread = ticket.message_thread(create=False)
    assert thread is not None
    thread_pk = thread.pk
    attachment_pk = ThreadAttachment._base_manager.get(object_id=ticket.pk).pk
    assert Message._base_manager.filter(thread_id=thread_pk).exists()
    assert ThreadFollower._base_manager.filter(thread_id=thread_pk).exists()
    assert ThreadActivity._base_manager.filter(thread_id=thread_pk).exists()

    with system_context(reason="test threaded model delete cascade"):
        ticket.delete()

    assert not Thread._base_manager.filter(pk=thread_pk).exists()
    assert not ThreadAttachment._base_manager.filter(pk=attachment_pk).exists()
    assert not Message._base_manager.filter(thread_id=thread_pk).exists()
    assert not ThreadFollower._base_manager.filter(thread_id=thread_pk).exists()
    assert not ThreadNotification._base_manager.filter(thread_id=thread_pk).exists()
    assert not ThreadActivity._base_manager.filter(thread_id=thread_pk).exists()
    survivor_thread = survivor.message_thread(create=False)
    assert survivor_thread is not None
    assert Message._base_manager.filter(pk=survivor_message.pk).exists()
    assert Thread._base_manager.filter(pk=survivor_thread.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_threaded_record_bulk_delete_tears_down_chatter_graph(messaging_tables: None) -> None:
    """A bulk ``QuerySet.delete()`` tears down the thread subtree too, not just the row (M1).

    The ``GenericForeignKey`` the attachment binds through points *at* the thread, so the
    delete collector can never cascade from a deleted record up to its private ``Thread``
    or that thread's messages. The ``pre_delete`` teardown receiver fires per collected
    row on the bulk path exactly as on the instance path, so filtering-then-deleting a
    chattered record leaves no orphaned thread, message, follower, or activity behind.
    """

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test threaded model bulk delete setup"):
        author = user_model.objects.create_user(username="bulk-author", email="bulk-author@example.com")
        ticket = ThreadedTicket.objects.create(title="Bulk cascade case")
        ticket.message_post("Body to be collected in bulk.")
        ticket.activity_schedule(user=author, summary="Follow up", due_date=_AT.date())
        survivor = ThreadedTicket.objects.create(title="Bulk survivor case")
        survivor_message = survivor.message_post("Untouched bulk body.")

    thread = ticket.message_thread(create=False)
    assert thread is not None
    thread_pk = thread.pk
    assert Message._base_manager.filter(thread_id=thread_pk).exists()
    assert ThreadActivity._base_manager.filter(thread_id=thread_pk).exists()

    with system_context(reason="test threaded model bulk delete"):
        deleted, _details = ThreadedTicket.objects.filter(pk=ticket.pk).delete()

    assert deleted
    assert not ThreadedTicket._base_manager.filter(pk=ticket.pk).exists()
    assert not Thread._base_manager.filter(pk=thread_pk).exists()
    assert not ThreadAttachment._base_manager.filter(object_id=ticket.pk).exists()
    assert not Message._base_manager.filter(thread_id=thread_pk).exists()
    assert not ThreadFollower._base_manager.filter(thread_id=thread_pk).exists()
    assert not ThreadActivity._base_manager.filter(thread_id=thread_pk).exists()
    survivor_thread = survivor.message_thread(create=False)
    assert survivor_thread is not None
    assert Message._base_manager.filter(pk=survivor_message.pk).exists()
    assert Thread._base_manager.filter(pk=survivor_thread.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_activity_agenda_lists_assignee_activities_across_records(messaging_tables: None) -> None:
    """The actor's assigned activities across records, ordered by due date, windowed (F-act).

    The agenda rides the ``messaging/thread_activity.read`` ``user`` (assignee) arm: the
    activities are scheduled elevated (``created_by`` is not the assignee), so the actor
    reaches its own rows through the assignee arm alone, with no parent-record grant. The
    window is the whole bound — ``window_start`` inclusive, ``window_end`` exclusive — and
    another actor's assignment, plus a company-B actor with no assignments, see nothing of
    it. Each row carries its parent pointer (label + sqid + model_label) through the
    attachment's owning model, computed without loading the target row.
    """

    del messaging_tables
    user_model = get_user_model()
    window_start, window_end = date(2026, 3, 1), date(2026, 4, 1)
    with system_context(reason="agenda across-records setup"):
        assignee = user_model.objects.create_user(username="agenda-assignee", email="agenda-assignee@example.com")
        other = user_model.objects.create_user(username="agenda-other", email="agenda-other@example.com")
        company_b = user_model.objects.create_user(username="agenda-company-b", email="agenda-company-b@example.com")
        alpha = ThreadedTicket.objects.create(title="Alpha")
        beta = ThreadedTicket.objects.create(title="Beta")
        # Assignee's activities across two records, out of due-date order.
        beta.activity_schedule(user=assignee, summary="Call Beta", due_date=date(2026, 3, 10))
        alpha.activity_schedule(user=assignee, summary="Email Alpha", due_date=date(2026, 3, 5))
        # Window boundaries: start is inclusive, end is exclusive.
        alpha.activity_schedule(user=assignee, summary="Kickoff", due_date=window_start)
        alpha.activity_schedule(user=assignee, summary="Boundary", due_date=window_end)
        # Out of window, another assignee, and an unassigned company-B actor — all absent.
        alpha.activity_schedule(user=assignee, summary="Later", due_date=date(2026, 4, 15))
        alpha.activity_schedule(user=other, summary="Other task", due_date=date(2026, 3, 7))

    with actor_context(assignee):
        rows = list(ThreadActivity.objects.agenda(assignee, window_start, window_end))
        empty = list(ThreadActivity.objects.agenda(company_b, window_start, window_end))

    assert [row.summary for row in rows] == ["Kickoff", "Email Alpha", "Call Beta"]
    assert {row.attachment.object_id for row in rows} == {alpha.pk, beta.pk}
    assert empty == []

    email_alpha = next(row for row in rows if row.summary == "Email Alpha")
    assert email_alpha.attachment.label == "Alpha"
    assert email_alpha.attachment.target_model_label == "messaging.ThreadedTicket"
    assert email_alpha.attachment.target_public_id == alpha.public_id


@pytest.mark.django_db(transaction=True)
def test_activity_agenda_excludes_done_unless_included(messaging_tables: None) -> None:
    """Done/canceled rows drop out of the agenda by default and return under include_done (F-act)."""

    del messaging_tables
    user_model = get_user_model()
    window_start, window_end = date(2026, 5, 1), date(2026, 6, 1)
    with system_context(reason="agenda done-filter setup"):
        assignee = user_model.objects.create_user(username="agenda-done", email="agenda-done@example.com")
        ticket = ThreadedTicket.objects.create(title="Case")
        ticket.activity_schedule(user=assignee, summary="Open task", due_date=date(2026, 5, 10))
        done = ticket.activity_schedule(user=assignee, summary="Done task", due_date=date(2026, 5, 12))
        ThreadActivity.objects.complete(done, post_message=False)

    with actor_context(assignee):
        default_summaries = [row.summary for row in ThreadActivity.objects.agenda(assignee, window_start, window_end)]
        with_done_summaries = [
            row.summary
            for row in ThreadActivity.objects.agenda(assignee, window_start, window_end, include_done=True)
        ]

    assert default_summaries == ["Open task"]
    assert with_done_summaries == ["Open task", "Done task"]


@pytest.mark.django_db(transaction=True)
def test_activity_agenda_row_reports_overdue_state_without_stored_flag(messaging_tables: None) -> None:
    """An overdue agenda row derives ``state == "overdue"`` from its due date, storing no flag (F-act)."""

    del messaging_tables
    user_model = get_user_model()
    window_start, window_end = date(2019, 1, 1), date(2021, 1, 1)
    with system_context(reason="agenda overdue setup"):
        assignee = user_model.objects.create_user(username="agenda-overdue", email="agenda-overdue@example.com")
        ticket = ThreadedTicket.objects.create(title="Escalation")
        ticket.activity_schedule(user=assignee, summary="Chase", due_date=date(2020, 6, 1))

    with actor_context(assignee):
        (row,) = list(ThreadActivity.objects.agenda(assignee, window_start, window_end))

    assert row.status == ThreadActivity.ActivityStatus.TODO
    assert row.activity_state == "overdue"


@pytest.mark.django_db(transaction=True)
def test_activity_agenda_record_pointer_batches_without_per_row_fanout(messaging_tables: None) -> None:
    """Projecting the agenda's record pointer is one batch, not a per-row lazy-load (D5).

    ``with_record_pointers`` primes every row's ``attachment`` in a single elevated query
    and each pointer field reads the process-cached ``ContentType``, so projecting the
    label + model_label + record_id for the whole agenda costs a constant query count —
    not the 1+2N a per-row ``attachment``/``content_type`` lazy-load would. Doubling the
    row count keeps the projection query count flat.
    """

    del messaging_tables
    user_model = get_user_model()
    window_start, window_end = date(2026, 3, 1), date(2026, 4, 1)
    with system_context(reason="agenda n+1 setup"):
        assignee = user_model.objects.create_user(username="agenda-n1", email="agenda-n1@example.com")
        for index in range(6):
            ticket = ThreadedTicket.objects.create(title=f"Case {index}")
            ticket.activity_schedule(user=assignee, summary=f"Task {index}", due_date=date(2026, 3, 1 + index))

    def project_query_count() -> tuple[int, int]:
        with system_context(reason="agenda n+1 measure"), CaptureQueriesContext(connection) as captured:
            rows = ThreadActivity.objects.agenda(assignee, window_start, window_end).with_record_pointers()
            for row in rows:
                _ = (row.attachment.label, row.attachment.target_model_label, row.attachment.target_public_id)
        return len(rows), len(captured.captured_queries)

    # Warm the process-cached ContentType so the flat comparison isolates the attachment batch.
    ContentType.objects.get_for_model(ThreadedTicket)
    small_rows, small_queries = project_query_count()

    with system_context(reason="agenda n+1 grow"):
        for index in range(6, 12):
            ticket = ThreadedTicket.objects.create(title=f"Case {index}")
            ticket.activity_schedule(user=assignee, summary=f"Task {index}", due_date=date(2026, 3, 1 + index))
    large_rows, large_queries = project_query_count()

    assert (small_rows, large_rows) == (6, 12)
    assert large_queries == small_queries


@pytest.mark.django_db(transaction=True)
def test_threaded_model_create_autofollows_and_logs_author(messaging_tables: None) -> None:
    """Creating a threaded row follows Odoo's creator subscription and log behavior."""

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test threaded model creation setup"):
        user = user_model.objects.create_user(username="creator", email="creator@example.com")

    with actor_context(user):
        ticket = ThreadedTicket.objects.create(title="Created case")

    follower = ThreadFollower._base_manager.get()
    messages = list(Message._base_manager.select_related("subtype").order_by("id"))
    notifications = list(ThreadNotification._base_manager.order_by("id"))
    creation_message, tracking_message = messages
    assert follower.user_id == user.pk
    assert creation_message.message_type == "notification"
    assert creation_message.subtype is not None
    assert creation_message.subtype.key == "record_created"
    assert creation_message.preview == "Threaded ticket created"
    assert creation_message.thread_id == ticket.message_thread().pk
    assert tracking_message.message_type == "auto_comment"
    assert tracking_message.subtype is not None
    assert tracking_message.subtype.key == "record_updated"
    assert tracking_message.preview == "Title:  -> Created case"
    tracking = TrackingValue._base_manager.get(message=tracking_message)
    assert tracking.field_name == "title"
    assert tracking.old_display == ""
    assert tracking.new_display == "Created case"
    assert [notification.user_id for notification in notifications] == [user.pk, user.pk]
    assert [notification.message_id for notification in notifications] == [
        creation_message.pk,
        tracking_message.pk,
    ]
    assert all(notification.is_read for notification in notifications)


@pytest.mark.django_db(transaction=True)
def test_materialized_child_transition_yields_one_tracking_note(messaging_tables: None) -> None:
    """A ``child_overrides_parent`` materialized child tracks a transition save once.

    The child-first (flipped) MRO places ``ThreadedModelMixin.save`` once in the
    chain, and MTI saves both tables inside that single ``save()`` — so a
    ``StateTransitions`` ``save_state`` edge over a tracked field posts exactly one
    tracking note, never one per MRO level. Pins the arch-review gap in the
    materialized-child + record-chatter tracking interaction.
    """

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="tracked child setup"):
        user = user_model.objects.create_user(username="flip-tracker", email="flip@example.com")

    with actor_context(user):
        record = TrackedRecordChild.objects.create(title="Flip case", note="child column")
        # status defaults to open, so creation logs no tracking note; the transition
        # is the one status change that must be tracked, once.
        record.close()

    thread = record.message_thread(create=False)
    tracking_messages = [
        message
        for message in Message._base_manager.select_related("subtype").filter(thread=thread)
        if message.subtype is not None and message.subtype.key == "record_updated"
    ]
    assert len(tracking_messages) == 1
    tracking_value = TrackingValue._base_manager.get(message=tracking_messages[0])
    assert tracking_value.field_name == "status"
    assert tracking_value.old_display == "Open"
    assert tracking_value.new_display == "Closed"
    # The transition persisted the guarded state through save_state.
    with system_context(reason="tracked child assertions"):
        assert TrackedRecordChild.objects.get(pk=record.pk).status == "closed"
        # The child column rode the same row (materialized child MTI).
        assert TrackedRecordChild.objects.get(pk=record.pk).note == "child column"


@pytest.mark.django_db(transaction=True)
def test_threaded_model_subscribe_and_unsubscribe(messaging_tables: None) -> None:
    """A threaded model row owns Odoo-style user followers."""

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test threaded model follower setup"):
        user = user_model.objects.create_user(username="follower", email="follower@example.com")
        ticket = ThreadedTicket.objects.create(title="Subscription")

    with actor_context(user):
        follower = ticket.message_subscribe(
            notification_policy="email",
            subtype_keys=("comment", "activity"),
        )
        again = ticket.message_subscribe(notification_policy="email", subtype_keys=("comment", "activity"))

    follower.refresh_from_db()
    again.refresh_from_db()
    assert follower.pk == again.pk
    assert follower.user_id == user.pk
    assert follower.thread_id == ThreadAttachment._base_manager.get().thread_id
    assert follower.notification_policy == "email"
    assert follower.subtype_keys == ["comment", "activity"]
    with actor_context(user):
        assert ticket.message_is_follower() is True
        assert list(ticket.message_followers()) == [follower]

    with actor_context(user):
        assert ticket.message_unsubscribe() is True
        assert ticket.message_is_follower() is False


@pytest.mark.django_db(transaction=True)
def test_threaded_model_post_autofollows_author(messaging_tables: None) -> None:
    """Posting a chatter comment subscribes the author for replies."""

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test threaded model autofollow setup"):
        user = user_model.objects.create_user(username="author", email="author@example.com")
        ticket = ThreadedTicket.objects.create(title="Autofollow")

    with actor_context(user):
        ticket.message_post("I am following this now.")

    follower = ThreadFollower._base_manager.get()
    follower.refresh_from_db()
    assert follower.user_id == user.pk
    assert follower.thread_id == Thread._base_manager.get().pk
    with actor_context(user):
        assert ticket.message_is_follower() is True


@pytest.mark.django_db(transaction=True)
def test_threaded_model_updates_comment_content(messaging_tables: None) -> None:
    """A record chatter comment can be edited without creating another message."""

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test threaded model message edit setup"):
        user = user_model.objects.create_user(username="editor", email="editor@example.com")
        ticket = ThreadedTicket.objects.create(title="Editable")

    with actor_context(user):
        message = ticket.message_post("Original body")

    notification_count = ThreadNotification._base_manager.count()
    with actor_context(user):
        edited = ticket.message_update_content(message, body="Updated body")

    edited.refresh_from_db()
    thread = edited.thread
    assert edited.pk == message.pk
    assert edited.status == "edited"
    assert edited.preview == "Updated body"
    assert edited.metadata["edited_by_id"] == user.pk
    assert Part._base_manager.select_related("fragment").get(message=edited).fragment.text == "Updated body"
    assert Message._base_manager.count() == 1
    assert ThreadNotification._base_manager.count() == notification_count
    assert thread is not None
    thread.refresh_from_db()
    assert thread.message_count == 1


@pytest.mark.django_db(transaction=True)
def test_threaded_model_rejects_system_message_updates(messaging_tables: None) -> None:
    """Odoo-style tracking/system messages are immutable chatter history."""

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test threaded model message edit guard setup"):
        user = user_model.objects.create_user(username="edit-guard", email="guard@example.com")
        ticket = ThreadedTicket.objects.create(title="Immutable")

    with actor_context(user):
        message = ticket.message_track(
            (
                {
                    "field_name": "stage",
                    "field_label": "Stage",
                    "field_type": "selection",
                    "old_value": "new",
                    "new_value": "done",
                    "old_display": "New",
                    "new_display": "Done",
                },
            ),
        )
        with pytest.raises(ValueError, match="Only comment messages can be edited"):
            ticket.message_update_content(message, body="Tampered")


@pytest.mark.django_db(transaction=True)
def test_threaded_model_post_notifies_matching_followers(messaging_tables: None) -> None:
    """Posting a chatter message fans out unread notifications by follower subtype."""

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test threaded model notification setup"):
        author = user_model.objects.create_user(username="notify-author", email="notify-author@example.com")
        watcher = user_model.objects.create_user(username="notify-watcher", email="watcher@example.com")
        muted = user_model.objects.create_user(username="notify-muted", email="muted@example.com")
        activity_only = user_model.objects.create_user(username="notify-activity", email="activity@example.com")
        ticket = ThreadedTicket.objects.create(title="Notification case")
        ticket.message_subscribe(user=watcher, subtype_keys=("comment",))
        ticket.message_subscribe(user=muted, notification_policy="muted")
        ticket.message_subscribe(user=activity_only, subtype_keys=("activity_done",))

    with actor_context(author):
        message = ticket.message_post("Followers should see this.")

    notification = ThreadNotification._base_manager.select_related("message", "user").get()
    assert notification.message_id == message.pk
    assert notification.thread_id == message.thread_id
    assert notification.attachment_id == ThreadAttachment._base_manager.get().pk
    assert notification.user_id == watcher.pk
    assert notification.notification_type == "inbox"
    assert notification.notification_status == "ready"
    assert notification.is_read is False
    assert notification.read_at is None
    assert ThreadNotification._base_manager.filter(user=muted).count() == 0
    assert ThreadNotification._base_manager.filter(user=activity_only).count() == 0


@pytest.mark.django_db(transaction=True)
def test_threaded_model_notification_mark_read(messaging_tables: None) -> None:
    """A recipient owns read state for their record-thread notifications."""

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test threaded model mark-read setup"):
        author = user_model.objects.create_user(username="read-author", email="read-author@example.com")
        watcher = user_model.objects.create_user(username="read-watcher", email="read-watcher@example.com")
        ticket = ThreadedTicket.objects.create(title="Read state")
        ticket.message_subscribe(user=watcher)

    with actor_context(author):
        ticket.message_post("Please read this.")

    assert ThreadNotification.objects.unread_count_for_record(ticket, user=watcher) == 1
    assert ThreadNotification.objects.mark_read_for_record(ticket, user=watcher) == 1
    notification = ThreadNotification._base_manager.get(user=watcher)
    assert notification.is_read is True
    assert notification.read_at is not None
    assert ThreadNotification.objects.unread_count_for_record(ticket, user=watcher) == 0


@pytest.mark.django_db(transaction=True)
def test_threaded_model_marks_one_message_done(messaging_tables: None) -> None:
    """Odoo-style message done clears one unread notification for the actor."""

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test threaded model message-done setup"):
        author = user_model.objects.create_user(username="done-author", email="done-author@example.com")
        watcher = user_model.objects.create_user(username="done-watcher", email="done-watcher@example.com")
        ticket = ThreadedTicket.objects.create(title="Needaction state")
        other_ticket = ThreadedTicket.objects.create(title="Other needaction state")
        ticket.message_subscribe(user=watcher)
        other_ticket.message_subscribe(user=watcher)

    with actor_context(author):
        first = ticket.message_post("First unread message.")
        second = ticket.message_post("Second unread message.")
        other_message = other_ticket.message_post("Other record message.")

    assert ThreadNotification.objects.unread_count_for_record(ticket, user=watcher) == 2
    assert ThreadNotification.objects.needaction_for_message(first, user=watcher) is True
    with system_context(reason="test threaded model message-done mark one"):
        assert ticket.message_set_done(first, user=watcher) == 1
    assert ThreadNotification.objects.needaction_for_message(first, user=watcher) is False
    assert ThreadNotification.objects.needaction_for_message(second, user=watcher) is True
    assert ThreadNotification.objects.unread_count_for_record(ticket, user=watcher) == 1

    first_notification = ThreadNotification._base_manager.get(message=first, user=watcher)
    second_notification = ThreadNotification._base_manager.get(message=second, user=watcher)
    assert first_notification.is_read is True
    assert first_notification.read_at is not None
    assert second_notification.is_read is False
    assert second_notification.read_at is None

    with system_context(reason="test threaded model message-done guard"), pytest.raises(
        ValueError,
        match="Message does not belong to this record thread",
    ):
        ticket.message_set_done(other_message, user=watcher)


@pytest.mark.django_db(transaction=True)
def test_threaded_model_post_notifies_direct_recipient_without_following(messaging_tables: None) -> None:
    """Direct post recipients get notifications even when they are not followers."""

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test threaded model direct-recipient setup"):
        author = user_model.objects.create_user(username="direct-author", email="direct-author@example.com")
        recipient = user_model.objects.create_user(username="direct-recipient", email="direct@example.com")
        ticket = ThreadedTicket.objects.create(title="Direct recipient")

    with actor_context(author):
        message = ticket.message_post("Please look directly.", recipient_user_ids=(recipient.pk,))

    notification = ThreadNotification._base_manager.get(user=recipient)
    assert notification.message_id == message.pk
    assert notification.follower_id is None
    assert notification.is_read is False
    assert ThreadFollower._base_manager.filter(user=recipient).count() == 0


@pytest.mark.django_db(transaction=True)
def test_threaded_model_suggests_record_user_and_latest_direct_recipient(messaging_tables: None) -> None:
    """Recipient suggestions merge declared record users and recent recipients."""

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test threaded model suggested recipients setup"):
        author = user_model.objects.create_user(username="suggest-author", email="suggest-author@example.com")
        assignee = user_model.objects.create_user(username="suggest-assignee", email="assignee@example.com")
        recipient = user_model.objects.create_user(username="suggest-recipient", email="recipient@example.com")
        follower = user_model.objects.create_user(username="suggest-follower", email="follower@example.com")
        ticket = ThreadedTicket.objects.create(title="Suggested recipients", assigned_user=assignee)
        ticket.message_subscribe(user=follower)

    with actor_context(author):
        ticket.message_post(
            "Please include the direct recipient.",
            recipient_user_ids=(recipient.pk, follower.pk),
        )

    with actor_context(author):
        suggestions = ticket.message_suggested_recipients(user=author)

    assert [item["user"] for item in suggestions] == [assignee, recipient]
    assert [item["source"] for item in suggestions] == [
        "assigned_user",
        "recent_message_recipient",
    ]


@pytest.mark.django_db(transaction=True)
def test_threaded_model_post_can_autofollow_direct_recipient(messaging_tables: None) -> None:
    """A direct recipient can be subscribed after a post, like Odoo autofollow."""

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test threaded model direct-recipient-autofollow setup"):
        author = user_model.objects.create_user(username="direct-follow-author", email="follow-author@example.com")
        recipient = user_model.objects.create_user(
            username="direct-follow-recipient",
            email="follow-recipient@example.com",
        )
        ticket = ThreadedTicket.objects.create(title="Direct recipient follow")

    with actor_context(author):
        ticket.message_post(
            "Please follow this too.",
            recipient_user_ids=(recipient.pk,),
            autofollow_recipients=True,
        )

    follower = ThreadFollower._base_manager.get(user=recipient)
    notification = ThreadNotification._base_manager.get(user=recipient)
    assert follower.attachment_id == notification.attachment_id
    assert notification.follower_id is None


@pytest.mark.django_db(transaction=True)
def test_threaded_model_delivery_error_counts_for_author(messaging_tables: None) -> None:
    """Delivery failures roll up to the author-facing chatter error counter."""

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test threaded model delivery-error setup"):
        author = user_model.objects.create_user(username="error-author", email="error-author@example.com")
        recipient = user_model.objects.create_user(username="error-recipient", email="error-recipient@example.com")
        ticket = ThreadedTicket.objects.create(title="Delivery error")

    with actor_context(author):
        message = ticket.message_post("This might bounce.", recipient_user_ids=(recipient.pk,))

    notification = ThreadNotification.objects.mark_failed_for_message(
        message,
        user=recipient,
        status="exception",
        failure_type="mail_smtp",
        failure_reason="SMTP server refused the message.",
    )

    notification.refresh_from_db()
    assert notification.notification_status == "exception"
    assert notification.failure_type == "mail_smtp"
    assert notification.failure_reason == "SMTP server refused the message."
    assert ThreadNotification.objects.error_count_for_record(ticket, user=author) == 1
    assert ThreadNotification.objects.error_count_for_record(ticket, user=recipient) == 0


@pytest.mark.django_db(transaction=True)
def test_threaded_model_activity_completion_notifies_activity_followers(messaging_tables: None) -> None:
    """Activity completion messages notify followers subscribed to that subtype."""

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test threaded model activity notification setup"):
        author = user_model.objects.create_user(username="activity-author", email="activity-author@example.com")
        watcher = user_model.objects.create_user(username="activity-watcher", email="activity-watcher@example.com")
        ticket = ThreadedTicket.objects.create(title="Activity notification")
        ticket.message_subscribe(user=watcher, subtype_keys=("activity_done",))

    with actor_context(author):
        activity = ticket.activity_schedule(user=author, summary="Call customer", due_date=_AT.date())
        ticket.activity_feedback(activity, feedback="Done.")

    notification = ThreadNotification._base_manager.select_related("message", "message__subtype").get(user=watcher)
    assert notification.is_read is False
    assert notification.message.subtype is not None
    assert notification.message.subtype.key == "activity_done"


@pytest.mark.django_db(transaction=True)
def test_threaded_model_post_accepts_storage_attachments(messaging_tables: None, tmp_path: Path) -> None:
    """Posting on a threaded model can attach existing storage files."""

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test threaded model attachment setup"):
        user = user_model.objects.create_user(username="attach-author", email="attach-author@example.com")
        _storage_drive(tmp_path, owner=user)
        file = StorageFile.objects.ingest_bytes(b"Attachment body", filename="brief.txt", owner_id=user.pk)
        ticket = ThreadedTicket.objects.create(title="Attachment case")

    with actor_context(user):
        message = ticket.message_post("See attached.", attachments=(file,))

    body_part = Part._base_manager.select_related("fragment").get(message=message, file__isnull=True)
    attachment_part = Part._base_manager.select_related("file", "file__mime_type").get(
        message=message,
        file__isnull=False,
    )
    assert body_part.fragment.text == "See attached."
    assert attachment_part.disposition == "attachment"
    assert attachment_part.name == "brief.txt"
    assert attachment_part.file_id == file.pk
    assert attachment_part.file.size_bytes == len(b"Attachment body")


@pytest.mark.django_db(transaction=True)
def test_threaded_model_tracks_structured_field_values(messaging_tables: None) -> None:
    """A threaded model can log Odoo-style tracking values without a free-text body."""

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test threaded model tracking setup"):
        user = user_model.objects.create_user(username="tracker", email="tracker@example.com")
        ticket = ThreadedTicket.objects.create(title="Tracking case")

    with actor_context(user):
        message = ticket.message_track(
            (
                {
                    "field_name": "stage",
                    "field_label": "Stage",
                    "field_type": "selection",
                    "old_value": "new",
                    "new_value": "done",
                    "old_display": "New",
                    "new_display": "Done",
                },
            ),
        )

    message.refresh_from_db()
    assert message.message_type == "auto_comment"
    assert message.subtype is not None
    assert message.subtype.key == "record_updated"
    assert message.preview == "Stage: New -> Done"
    assert Part._base_manager.filter(message=message).count() == 0
    tracking = TrackingValue._base_manager.get(message=message)
    assert tracking.field_name == "stage"
    assert tracking.field_label == "Stage"
    assert tracking.field_type == "selection"
    assert tracking.old_value == "new"
    assert tracking.new_value == "done"
    assert tracking.old_display == "New"
    assert tracking.new_display == "Done"
    assert ThreadFollower._base_manager.count() == 0


@pytest.mark.django_db(transaction=True)
def test_threaded_model_autotracks_configured_field_saves(messaging_tables: None) -> None:
    """Saving a threaded row logs configured field changes in the chatter."""

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test threaded model autotrack setup"):
        user = user_model.objects.create_user(username="autotracker", email="autotracker@example.com")
        ticket = ThreadedTicket.objects.create(title="Initial")

    assert Message._base_manager.count() == 0

    with actor_context(user):
        ticket.title = "Escalated"
        ticket.status = "closed"
        ticket.save(update_fields=("title", "status"))

    message = Message._base_manager.get()
    assert message.message_type == "auto_comment"
    assert message.subtype is not None
    assert message.subtype.key == "record_updated"
    assert message.preview == "Title: Initial -> Escalated"
    tracking = list(TrackingValue._base_manager.filter(message=message).order_by("position"))
    assert [
        (item.field_name, item.field_label, item.old_display, item.new_display)
        for item in tracking
    ] == [
        ("title", "Title", "Initial", "Escalated"),
        ("status", "Status", "Open", "Closed"),
    ]


@pytest.mark.django_db(transaction=True)
def test_threaded_model_autotracking_respects_update_fields(messaging_tables: None) -> None:
    """Saves that omit tracked fields do not create chatter noise."""

    del messaging_tables
    with system_context(reason="test threaded model autotrack update-fields"):
        ticket = ThreadedTicket.objects.create(title="Unchanged")
        ticket.status = "closed"
        ticket.save(update_fields=("status",))

    assert Message._base_manager.count() == 1
    assert TrackingValue._base_manager.get().field_name == "status"

    with system_context(reason="test threaded model autotrack no tracked fields"):
        ticket.title = "Ignored in update_fields"
        ticket.save(update_fields=("updated_at",))

    assert Message._base_manager.count() == 1


@pytest.mark.django_db(transaction=True)
def test_threaded_model_schedules_and_completes_activity(messaging_tables: None) -> None:
    """A threaded model row owns Odoo-style scheduled activities."""

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test threaded model activity setup"):
        user = user_model.objects.create_user(username="assignee", email="assignee@example.com")
        ticket = ThreadedTicket.objects.create(title="Activity case")

    with actor_context(user):
        activity = ticket.activity_schedule(
            summary="Call customer",
            note="Ask about the rollout.",
            due_date=_AT.date(),
            activity_type="call",
        )

    activity.refresh_from_db()
    assert activity.user_id == user.pk
    assert activity.thread_id == ThreadAttachment._base_manager.get().thread_id
    assert activity.summary == "Call customer"
    assert activity.note == "Ask about the rollout."
    assert activity.due_date == _AT.date()
    assert activity.activity_type == "call"
    assert activity.status == "todo"
    with actor_context(user):
        assert list(ticket.activity_ids()) == [activity]

    with actor_context(user):
        completed = ticket.activity_feedback(activity, feedback="Customer confirmed.")

    completed.refresh_from_db()
    assert completed.status == "done"
    assert completed.activity_state == "done"
    assert completed.feedback == "Customer confirmed."
    assert completed.completed_at is not None
    message = Message._base_manager.get()
    assert message.thread_id == completed.thread_id
    assert message.direction == "internal"
    assert message.message_type == "auto_comment"
    assert message.subtype is not None
    assert message.subtype.key == "activity_done"
    assert message.preview.startswith("Activity done: Call customer")
    assert Part._base_manager.select_related("fragment").get(message=message).fragment.text == (
        "Activity done: Call customer\n\nCustomer confirmed."
    )


@pytest.mark.django_db(transaction=True)
def test_ingest_is_idempotent_on_platform_external_id(channel: Any) -> None:
    """Re-syncing the same message resolves to the existing row, not a duplicate."""

    parsed = _parsed("m1", sent_at=_AT)
    assert _ingest([parsed], channel=channel) == 1
    assert _ingest([parsed], channel=channel) == 1
    assert Message._base_manager.filter(external_id="m1").count() == 1
    thread = Thread._base_manager.get()
    # Counters bump only for a newly created message, so a re-sync never inflates them.
    assert thread.message_count == 1


def test_email_identifier_and_subject_columns_allow_long_imap_values() -> None:
    """IMAP Message-ID and Subject values can exceed 512 chars and land intact."""

    assert Thread._meta.get_field("external_id").max_length == 4096
    assert Thread._meta.get_field("subject").max_length == 4096
    assert Thread._meta.get_field("subject_normalized").max_length == 4096
    assert Message._meta.get_field("external_id").max_length == 4096
    assert Message._meta.get_field("subject").max_length == 4096


@pytest.mark.django_db(transaction=True)
def test_identical_resync_does_not_churn_message_or_parts(channel: Any) -> None:
    """A second identical ingest rewrites nothing — no re-save, stable Part PKs (M3).

    The prior sync stores a content digest in ``metadata``; an identical re-sync into
    the same thread hashes equal and short-circuits, so it neither re-saves the message
    (which would emit a HistoryMixin row and advance ``updated_at``) nor tears down and
    rebuilds the Part tree (which would churn Part primary keys and re-upsert Fragments).
    """

    parsed = _parsed("m1", sent_at=_AT)
    assert _ingest([parsed], channel=channel) == 1
    message = Message._base_manager.get(external_id="m1")
    assert message.metadata.get("sync_hash")
    first_updated_at = message.updated_at
    part_pks = set(Part._base_manager.filter(message=message).values_list("pk", flat=True))
    fragment_count = Fragment._base_manager.count()
    assert part_pks

    assert _ingest([parsed], channel=channel) == 1

    message.refresh_from_db()
    assert message.updated_at == first_updated_at
    assert set(Part._base_manager.filter(message=message).values_list("pk", flat=True)) == part_pks
    assert Fragment._base_manager.count() == fragment_count
    assert Message._base_manager.filter(external_id="m1").count() == 1
    assert Thread._base_manager.get().message_count == 1


@pytest.mark.django_db(transaction=True)
def test_counter_survives_null_sent_at(channel: Any) -> None:
    """A message with no sent_at bumps the count without crashing (the M1 bug).

    The historical bug wrote ``updated_at`` (NOT NULL ``auto_now``) from a null
    ``sent_at`` via ``.update()`` — an IntegrityError on the first such message.
    """

    assert _ingest([_parsed("m1", sent_at=None)], channel=channel) == 1
    thread = Thread._base_manager.get()
    assert thread.message_count == 1
    assert thread.last_message_at is None  # no sent_at → not advanced
    assert thread.updated_at is not None  # auto_now owned it


@pytest.mark.django_db(transaction=True)
def test_last_message_at_is_monotonic(channel: Any) -> None:
    """Out-of-order ingest never regresses last_message_at."""

    later = _AT
    earlier = _AT - timedelta(days=1)
    _ingest([_parsed("m1", subject="Topic", sent_at=later)], channel=channel)
    _ingest([_parsed("m2", subject="Topic", sent_at=earlier)], channel=channel)
    thread = Thread._base_manager.get()
    assert thread.message_count == 2
    assert thread.last_message_at == later


@pytest.mark.django_db(transaction=True)
def test_null_bytes_are_stripped_on_write(channel: Any) -> None:
    """Null bytes in the subject are stripped before the write (Postgres rejects them)."""

    _ingest([_parsed("m1", subject="Hi\x00there", text="body\x00text", sent_at=_AT)], channel=channel)
    message = Message._base_manager.get(external_id="m1")
    assert "\x00" not in message.subject
    assert message.subject == "Hithere"


@pytest.mark.django_db(transaction=True)
def test_references_resolve_into_one_thread(channel: Any) -> None:
    """References win over subject: a reply with a different subject joins the root thread."""

    _ingest([_parsed("a", subject="Root", sent_at=_AT)], channel=channel)
    _ingest([_parsed("b", subject="Re: Unrelated", references=("a",), sent_at=_AT)], channel=channel)
    assert Thread._base_manager.count() == 1
    assert Thread._base_manager.get().message_count == 2


@pytest.mark.django_db(transaction=True)
def test_resync_rethreads_and_reconciles_both_thread_counters(channel: Any) -> None:
    """Re-threading a message on re-sync moves its count off the old thread onto the new (H1).

    Message ``b`` references ``a`` but is ingested first, so ``a`` is not yet resolvable
    and ``b`` opens its own thread; ``a`` then opens another. Re-syncing ``b`` now resolves
    ``a``'s thread through References, so ``b`` must leave its old thread (count → 0) and
    join ``a``'s (count → 2, ``last_message_at`` advanced to ``b``'s later send time).
    Before the fix both counters drift — the losing thread stays at 1 and the winner never
    gains the message — because reconciliation was gated on ``created``.
    """

    b_sent = _AT + timedelta(days=1)
    _ingest([_parsed("b", subject="Beta topic", references=("a",), sent_at=b_sent)], channel=channel)
    orphan_thread = Message._base_manager.get(external_id="b").thread
    assert orphan_thread.message_count == 1

    _ingest([_parsed("a", subject="Alpha topic", sent_at=_AT)], channel=channel)
    root_thread = Message._base_manager.get(external_id="a").thread
    assert root_thread.pk != orphan_thread.pk
    assert root_thread.message_count == 1

    _ingest([_parsed("b", subject="Beta topic", references=("a",), sent_at=b_sent)], channel=channel)

    assert Message._base_manager.get(external_id="b").thread_id == root_thread.pk
    root_thread.refresh_from_db()
    orphan_thread.refresh_from_db()
    assert root_thread.message_count == 2
    assert root_thread.last_message_at == b_sent
    assert orphan_thread.message_count == 0
    assert orphan_thread.last_message_at is None


@pytest.mark.django_db(transaction=True)
def test_resync_rehomes_null_thread_message_and_bumps_winner(channel: Any) -> None:
    """A thread-less message re-homed on re-sync still bumps the winning thread (H1).

    Deleting a thread ``SET_NULL``s its messages, leaving a live message with no thread.
    A later re-sync that resolves that message onto thread B must bump B's ``message_count``
    even though the prior thread was NULL — the winner gains the message whenever the
    resolved thread differs from the prior one, and there is simply no losing thread to
    recount. Gating the bump on ``created`` alone (or on a non-null prior) dropped this
    re-home, leaving B's count stuck.
    """

    b_sent = _AT + timedelta(days=1)
    _ingest([_parsed("b", subject="Beta topic", references=("a",), sent_at=b_sent)], channel=channel)
    orphan_thread = Message._base_manager.get(external_id="b").thread
    # Delete the message's thread; its FK SET_NULLs, leaving the message thread-less.
    with system_context(reason="test null-thread re-home setup"):
        Thread._base_manager.filter(pk=orphan_thread.pk).delete()
    assert Message._base_manager.get(external_id="b").thread_id is None

    _ingest([_parsed("a", subject="Alpha topic", sent_at=_AT)], channel=channel)
    root_thread = Message._base_manager.get(external_id="a").thread
    assert root_thread.message_count == 1

    _ingest([_parsed("b", subject="Beta topic", references=("a",), sent_at=b_sent)], channel=channel)

    assert Message._base_manager.get(external_id="b").thread_id == root_thread.pk
    root_thread.refresh_from_db()
    assert root_thread.message_count == 2
    assert root_thread.last_message_at == b_sent


@pytest.mark.django_db(transaction=True)
def test_quote_edge_runs_from_earlier_to_later(channel: Any) -> None:
    """Two messages sharing a fragment get one quote edge, earlier → later."""

    shared = "A distinctive shared paragraph that both messages quote verbatim."
    _ingest(
        [
            _parsed("old", subject="One", sent_at=_AT - timedelta(days=1), text=shared),
            _parsed("new", subject="Two", sent_at=_AT, text=shared),
        ],
        channel=channel,
    )
    old = Message._base_manager.get(external_id="old")
    new = Message._base_manager.get(external_id="new")
    edge = MessageEdge._base_manager.get(kind="quote")
    assert (edge.src_id, edge.dst_id) == (old.pk, new.pk)


def _grant(record: Any, relation: str, user: Any) -> None:
    """Write one direct REBAC relationship tuple for ``user`` on ``record``."""

    write_relationships(
        [
            RelationshipTuple(
                resource=to_object_ref(record),
                relation=relation,
                subject=to_subject_ref(user),
            )
        ]
    )


@pytest.mark.django_db(transaction=True)
def test_tracked_field_log_lands_without_post_access(messaging_tables: None) -> None:
    """An automatic tracked-field log is a system write that ignores post access.

    F-v part 1: a ``writer`` grant confers ``write`` (the tracked-field save) but not
    ``post``, so ``ChatterDoc.thread_post_access="post"`` makes ``can_post`` deny the
    actor. The tracked change and its system tracking note must both land — the save
    is not rolled back by a post-access denial — and the thread's message count
    increments by exactly the one tracked change.
    """

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test.chatterdemo.part1.seed"):
        writer = user_model.objects.create_user(username="cdc-writer", email="cdc-writer@example.com")
        doc = ChatterDoc.objects.create(title="Order 1", status="open")
    _grant(doc, "writer", writer)

    with actor_context(writer):
        doc.status = "closed"
        doc.save(update_fields=["status"])

    with system_context(reason="test.chatterdemo.part1.read"):
        doc.refresh_from_db()
        assert doc.status == "closed"
        thread = doc.message_thread(create=False)
        assert thread is not None
        assert thread.message_count == 1
        logs = list(Message._base_manager.filter(thread=thread))
        assert len(logs) == 1
        assert logs[0].message_type == Message.MessageKind.AUTO_COMMENT
        assert [value.field_name for value in logs[0].tracking_values.all()] == ["status"]


@pytest.mark.django_db(transaction=True)
def test_user_authored_post_still_denied_without_post_access(messaging_tables: None) -> None:
    """User-authored chatter still rides the post gate for a no-post actor.

    F-v part 1: only automatic system writes bypass ``can_post``. A ``writer`` (write,
    no ``post``) is still denied posting a comment or logging a note.
    """

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test.chatterdemo.part1b.seed"):
        writer = user_model.objects.create_user(username="cdc-writer2", email="cdc-writer2@example.com")
        doc = ChatterDoc.objects.create(title="Order 2", status="open")
    _grant(doc, "writer", writer)

    with actor_context(writer):
        with pytest.raises(PermissionDenied):
            doc.message_post("Hello")
        with pytest.raises(PermissionDenied):
            doc.message_log("A note")


@contextlib.contextmanager
def _collecting_broadcasts(
    monkeypatch: pytest.MonkeyPatch,
    *models: type[models.Model],
) -> Iterator[list[tuple[Any, dict[str, Any]]]]:
    """Yield the change payloads broadcast for ``models`` while their publishers are wired.

    Runs ``_broadcast`` and ``on_commit`` inline so a post's broadcast is observable
    now, and restores each model's prior publisher wiring on exit through the public
    ``connect_publishers`` / ``disconnect_publishers`` seam.
    """

    sent: list[tuple[Any, dict[str, Any]]] = []
    monkeypatch.setattr(publishing, "_broadcast", lambda model, payload: sent.append((model, payload)))
    monkeypatch.setattr(publishing.transaction, "on_commit", lambda callback: callback())
    already_wired = {model: publishing.disconnect_publishers(model) for model in models}
    for model in models:
        publishing.connect_publishers(model)
    try:
        yield sent
    finally:
        for model in models:
            publishing.disconnect_publishers(model)
            if already_wired[model]:
                publishing.connect_publishers(model)


@contextlib.contextmanager
def _collecting_thread_broadcasts(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[list[tuple[Any, dict[str, Any]]]]:
    """Yield the ``Thread`` change payloads broadcast while its publisher is wired."""

    with _collecting_broadcasts(monkeypatch, Thread) as sent:
        yield sent


@pytest.mark.django_db(transaction=True)
def test_post_bumps_thread_through_an_instance_save(messaging_tables: None) -> None:
    """A post advances the thread with an instance ``save``, so ``post_save`` fires once.

    F-stream part B: the bump moved off the publisher-invisible queryset ``.update()``
    onto ``save(update_fields=…)``, so the ``changes`` publisher (``post_save``) sees a
    new post at all. The denormalised counters land exactly as the queryset bump left
    them — the regression guard.
    """

    del messaging_tables
    saves: list[dict[str, Any]] = []

    def _record(sender: Any, instance: Any, created: bool, update_fields: Any = None, **kwargs: Any) -> None:
        del sender, instance, kwargs
        saves.append({"created": created, "update_fields": set(update_fields or ())})

    post_save.connect(_record, sender=Thread, dispatch_uid="test-thread-bump-probe")
    try:
        with system_context(reason="test thread bump fires post_save"):
            ticket = ThreadedTicket.objects.create(title="Bump case")
            message = ticket.message_post("First post.")
    finally:
        post_save.disconnect(sender=Thread, dispatch_uid="test-thread-bump-probe")

    # One thread INSERT (the lazy get_or_create) and exactly one bump UPDATE for the post.
    bumps = [row for row in saves if not row["created"]]
    assert len(bumps) == 1
    assert {"message_count", "last_message_at"} <= bumps[0]["update_fields"]

    thread = Thread._base_manager.get()
    assert thread.message_count == 1
    assert thread.last_message_at == message.sent_at


@pytest.mark.django_db(transaction=True)
def test_post_on_opted_in_host_emits_one_member_gated_thread_changed(
    messaging_tables: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A post on an opted-in host emits one ``threadChanged``, gated to thread readers.

    F-stream end to end: ``BroadcastRoom`` opts in (``thread_broadcasts_changes = True``),
    so a post fires ``post_save`` (part B) and ``_publish`` broadcasts one ``threadChanged``
    (part A). The event passes ``ChangeReadGate`` for a member holding ``thread.reader``
    and is dropped for a non-member — no existence or activity leak on the socket.
    """

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test opted-in room seed"):
        member = user_model.objects.create_user(username="room-member", email="room-member@example.com")
        stranger = user_model.objects.create_user(username="room-stranger", email="room-stranger@example.com")
        room = BroadcastRoom.objects.create(title="general")
        thread = room.message_thread(create=True)
    _grant(thread, "reader", member)

    with _collecting_thread_broadcasts(monkeypatch) as sent:
        with system_context(reason="test opted-in room post"):
            room.message_post("Live to the room.")

    events = [payload for model, payload in sent if model is Thread]
    assert len(events) == 1
    assert events[0]["model"] == "messaging.Thread"
    assert events[0]["action"] == "update"

    change = ChangePayload.from_mapping(events[0])
    assert ChangeReadGate(Thread, to_subject_ref(member)).filter(change) is not None
    assert ChangeReadGate(Thread, to_subject_ref(stranger)).filter(change) is None


@pytest.mark.django_db(transaction=True)
def test_record_chatter_host_stays_silent_on_a_post(
    messaging_tables: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-opted threaded host streams nothing on a post — F-v isolation intact.

    The F-stream default is ``thread_broadcasts_changes = False``, so a record-chatter
    thread (``ThreadedTicket``) never broadcasts: the bump-save change is inert for a
    silent thread because ``_publish`` short-circuits on ``broadcasts_changes()``.
    """

    del messaging_tables
    with system_context(reason="test record chatter silent seed"):
        ticket = ThreadedTicket.objects.create(title="Silent case")
        ticket.message_thread(create=True)

    with _collecting_thread_broadcasts(monkeypatch) as sent:
        with system_context(reason="test record chatter silent post"):
            ticket.message_post("No one should stream this.")

    assert [payload for model, payload in sent if model is Thread] == []


@pytest.mark.django_db(transaction=True)
def test_room_post_emits_no_message_changed(messaging_tables: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A post on an opted-in room streams ``threadChanged`` only, never ``messageChanged``.

    Room members hold ``messaging/thread.reader``, not ``message.read``, so every
    per-message event would be dropped by ``ChangeReadGate`` — pure gated noise. A
    message on any record-attached thread therefore stays off the generic message
    surface (``Message.broadcasts_changes`` is ``False``); the thread's ``threadChanged``
    is the live contract.
    """

    del messaging_tables
    with system_context(reason="test room message-silence seed"):
        room = BroadcastRoom.objects.create(title="general")
        room.message_thread(create=True)

    with _collecting_broadcasts(monkeypatch, Thread, Message) as sent:
        with system_context(reason="test room message-silence post"):
            message = room.message_post("Live to the room.")

    assert message.broadcasts_changes() is False
    assert [payload for model, payload in sent if model is Message] == []
    assert [payload for model, payload in sent if model is Thread] != []


@pytest.mark.django_db(transaction=True)
def test_resubscribe_preserves_follower_policy(messaging_tables: None) -> None:
    """A re-subscribe leaves an existing follower's create-time state untouched.

    ``notification_policy`` / ``subtype_keys`` are create-time defaults: a bare
    re-subscribe (e.g. autofollow on a later post) must not reset a muted follower
    back to ``inbox``. An explicit value still wins.
    """

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test resubscribe seed"):
        watcher = user_model.objects.create_user(username="resub-watcher", email="resub-watcher@example.com")
        ticket = ThreadedTicket.objects.create(title="Resub case")
        first = ticket.message_subscribe(user=watcher, notification_policy="muted", subtype_keys=("comment",))
        assert first.notification_policy == "muted"

        # A bare re-subscribe preserves the muted policy and subtype filter.
        again = ticket.message_subscribe(user=watcher)
        again.refresh_from_db()
        assert again.pk == first.pk
        assert again.notification_policy == "muted"
        assert again.subtype_keys == ["comment"]

        # An explicit value still updates it.
        changed = ticket.message_subscribe(user=watcher, notification_policy="email")
        changed.refresh_from_db()
        assert changed.notification_policy == "email"
        assert changed.subtype_keys == ["comment"]


@pytest.mark.django_db(transaction=True)
def test_stale_broadcast_flag_heals_on_next_activity(messaging_tables: None) -> None:
    """A record thread minted before its host opted in heals its broadcast flag on next post.

    ``host_broadcasts_changes`` is stamped only in the ``get_or_create`` defaults, so a
    thread created while the host was silent would keep the stale ``False``;
    ``ensure_for_record`` re-stamps it from the host on the next activity.
    """

    del messaging_tables
    with system_context(reason="test broadcast-flag heal seed"):
        room = BroadcastRoom.objects.create(title="stale-room")
        thread = room.message_thread(create=True)
        # Simulate a thread minted before the host opted in.
        Thread._base_manager.filter(pk=thread.pk).update(host_broadcasts_changes=False)
        thread.refresh_from_db()
        assert thread.host_broadcasts_changes is False

        room.message_post("First post after opt-in.")
        thread.refresh_from_db()

    assert thread.host_broadcasts_changes is True
    assert thread.broadcasts_changes() is True


@pytest.mark.django_db(transaction=True)
def test_broadcasting_room_creator_socket_gated_by_membership(messaging_tables: None) -> None:
    """A broadcasting room's thread is system-owned, so membership is the only live gate.

    A member who *created* the room thread would otherwise keep ``thread.read`` forever
    through the field-backed ``owner`` (``created_by``) arm, so an expelled creator's
    ``threadChanged`` socket would never go dark. Minting a broadcasting host's thread
    system-owned (``created_by=None``) makes ``reader`` + admin the live gate.
    """

    del messaging_tables
    user_model = get_user_model()
    with system_context(reason="test expelled-creator seed"):
        creator = user_model.objects.create_user(username="room-creator", email="room-creator@example.com")
        room = BroadcastRoom.objects.create(title="creator-room")

    # The creator mints the thread under their own actor, so the audit stamp would set
    # created_by=creator; minting a broadcasting host's thread system-owned clears it.
    with actor_context(creator):
        thread = room.message_thread(create=True)
    assert thread.created_by_id is None

    with system_context(reason="test expelled-creator read"):
        thread.refresh_from_db()
        assert thread.created_by_id is None

        change = ChangePayload.from_instance(thread, action="update", update_fields=None)
        creator_subject = to_subject_ref(creator)

        # Not a member: the socket is dark despite having created the room.
        assert ChangeReadGate(Thread, creator_subject).filter(change) is None

        # Granted membership through the atomic subscribe verb: the socket is live.
        room.message_subscribe(user=creator, grant_read=True)
        assert ChangeReadGate(Thread, creator_subject).filter(change) is not None

        # Expelled through the mirror revoke verb: the socket goes dark again.
        room.message_unsubscribe(user=creator, revoke_read=True)
        assert ChangeReadGate(Thread, creator_subject).filter(change) is None
