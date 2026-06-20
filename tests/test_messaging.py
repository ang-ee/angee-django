"""Tests for the messaging ingest write path (the channel-sync map).

The concrete messaging/parties models are composed here the way the composer folds
each abstract source model onto one runtime table, so the manager write path runs
against real tables. The cases pin the ingest invariants the module docstring
promises: idempotency on ``(platform, external_id)``, null-byte stripping, RFC-5322
thread resolution, the monotonic/never-crashing counter bump, and quote-edge
direction.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from django.core.management import call_command
from django.db import connection
from rebac import system_context

from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedPart, ParsedRecipient
from angee.messaging.managers import normalize_subject, strip_null_bytes
from angee.messaging.models import Fragment as AbstractFragment
from angee.messaging.models import Message as AbstractMessage
from angee.messaging.models import MessageEdge as AbstractMessageEdge
from angee.messaging.models import Part as AbstractPart
from angee.messaging.models import Participant as AbstractParticipant
from angee.messaging.models import Thread as AbstractThread
from angee.parties.models import Directory as AbstractDirectory
from angee.parties.models import Folder as AbstractContactFolder
from angee.parties.models import Handle as AbstractHandle
from angee.parties.models import Party as AbstractParty
from tests.conftest import (
    IAM_CONNECTION_TEST_MODELS,
    INTEGRATE_TEST_MODELS,
    STORAGE_TEST_MODELS,
    Integration,
    _create_missing_tables,
    make_integration,
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


class Thread(AbstractThread):
    """Concrete thread used by messaging tests."""

    class Meta(AbstractThread.Meta):
        """Django model options for the canonical test thread."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_thread"
        rebac_resource_type = "messaging/thread"
        rebac_id_attr = "sqid"


class Message(AbstractMessage):
    """Concrete message used by messaging tests."""

    class Meta(AbstractMessage.Meta):
        """Django model options for the canonical test message."""

        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_message"
        rebac_resource_type = "messaging/message"
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
    Message,
    Part,
    MessageEdge,
    Participant,
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
    """Run the ingest the way the scheduler does — elevated under system_context."""

    with system_context(reason="test messaging ingest"):
        return Message.objects.ingest(messages, channel=channel)


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
def test_ingest_is_idempotent_on_platform_external_id(channel: Any) -> None:
    """Re-syncing the same message resolves to the existing row, not a duplicate."""

    parsed = _parsed("m1", sent_at=_AT)
    assert _ingest([parsed], channel=channel) == 1
    assert _ingest([parsed], channel=channel) == 1
    assert Message._base_manager.filter(external_id="m1").count() == 1
    thread = Thread._base_manager.get()
    # Counters bump only for a newly created message, so a re-sync never inflates them.
    assert thread.message_count == 1


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
