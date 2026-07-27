"""Tests for Facebook takeout parsing and owner-composed importing."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from django.core.management import call_command
from django.db import connection
from rebac import system_context

from angee.messaging.backup_ingest import ContentKeyCounter
from angee.messaging_integrate_facebook.archive import (
    FacebookArchive,
    FacebookCommentRecord,
    FacebookPostRecord,
)
from angee.messaging_integrate_facebook.importer import import_archive
from angee.messaging_integrate_facebook.parser import (
    FacebookPostMatcher,
    parsed_comment,
    parsed_message,
)
from tests.conftest import _clear_model_tables, _create_missing_tables, make_integration
from tests.test_messaging import (
    Handle,
    Message,
    Party,
    PartyHandle,
    Person,
    Relationship,
    RelationshipKind,
    Thread,
)
from tests.test_messaging_graphql import MESSAGING_GRAPHQL_MODELS, Channel

_AT = datetime(2026, 1, 2, tzinfo=timezone.utc)


@pytest.fixture
def facebook_tables(transactional_db: object) -> Iterator[None]:
    """Create messaging/posts/parties concrete tables for importer integration."""

    del transactional_db
    # The graphql model set includes the concrete Channel this file creates —
    # a fixture that creates a row must own that row's table end to end, or
    # teardown leaves an orphan that fails the schema editor's FK check and
    # poisons every later module in the same process.
    created = _create_missing_tables(MESSAGING_GRAPHQL_MODELS)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        _clear_model_tables(MESSAGING_GRAPHQL_MODELS)
        if created:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created):
                    schema_editor.delete_model(model)


def test_message_parser_maps_direction_identity_and_thread_folder(tmp_path: Path) -> None:
    """Facebook names become confidence-stamped handles and stable thread hints."""

    root = _facebook_export(tmp_path)
    archive = FacebookArchive(root)
    record = next(iter(archive.messages(counter=ContentKeyCounter())))
    parsed = parsed_message(record, own_name="Alex Example")

    assert parsed.direction == "inbound"
    assert parsed.sender is not None
    assert parsed.sender.value == "Bob Friend"
    assert parsed.sender.metadata == {
        "identity_confidence": "name_only",
        "source": "facebook_takeout",
    }
    assert parsed.thread is not None
    # The thread keys on Meta's stable directory slug; the folder is an
    # export-run fact and rides metadata only (a folder move must not fork).
    assert parsed.thread.external_id == "bob_chat"
    assert parsed.thread.metadata["folder"] == "inbox"
    assert parsed.metadata["sender_identity"]["identity_confidence"] == "name_only"


def test_comment_matching_distinguishes_strong_weak_and_unmatched() -> None:
    """Two-signal matches are strong; a sole unique signal is weak."""

    first = FacebookPostRecord(
        data={"title": "Shared title"},
        external_id="post-1",
        thread_key="feed:posts",
        thread_title="Posts",
        sent_at=_AT,
    )
    second = FacebookPostRecord(
        data={"title": "Other title"},
        external_id="post-2",
        thread_key="feed:posts",
        thread_title="Posts",
        sent_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
    )
    matcher = FacebookPostMatcher([first, second])
    strong = FacebookCommentRecord(
        data={"title": "Shared title", "timestamp": int(_AT.timestamp())},
        sent_at=_AT,
    )
    weak = FacebookCommentRecord(
        data={"title": "Other title", "timestamp": int(_AT.timestamp()) + 10},
        sent_at=datetime.fromtimestamp(int(_AT.timestamp()) + 10, tz=timezone.utc),
    )
    unmatched = FacebookCommentRecord(
        data={"title": "Unknown", "timestamp": int(_AT.timestamp()) + 20},
        sent_at=datetime.fromtimestamp(int(_AT.timestamp()) + 20, tz=timezone.utc),
    )

    assert matcher.match(strong).strength == "strong"
    assert matcher.match(weak).strength == "weak"
    assert matcher.match(unmatched).strength == "unmatched"
    parsed = parsed_comment(
        strong,
        own_name="Alex Example",
        counter=ContentKeyCounter(),
        matcher=matcher,
    )
    assert parsed.message.in_reply_to == "post-1"
    assert parsed.message.thread is None
    assert parsed.message.metadata["post_match"] == "strong"


@pytest.mark.django_db(transaction=True)
def test_importer_is_idempotent_and_lands_every_section_through_owners(
    facebook_tables: None,
    tmp_path: Path,
) -> None:
    """A repeated synthetic export keeps row counts and public semantics stable."""

    del facebook_tables
    root = _facebook_export(tmp_path)
    with system_context(reason="test facebook importer"):
        channel = make_integration(
            "facebook-import",
            backend_class="facebook",
            display_name="Facebook",
            model=Channel,
        )
        RelationshipKind._base_manager.create(slug="acquaintance", name="Acquaintance")
        first_result = import_archive(root, channel)
        first_counts = _row_counts()
        second_result = import_archive(root, channel)
        second_counts = _row_counts()
        post = Message._base_manager.get(metadata__source="facebook_takeout", is_original_post=True)
        comment = Message._base_manager.get(parent=post)
        direct = Message._base_manager.get(thread__modality=Thread.Modality.DIRECT)
        friend_handle = Handle._base_manager.get(platform="facebook", value="Bob Friend")

    assert first_result == second_result == {
        "messages": 1,
        "posts": 1,
        "comments": 1,
        "connections": 1,
    }
    assert second_counts == first_counts
    assert Message._base_manager.count() == 3
    assert post.message_type == Message.MessageKind.COMMENT
    assert post.thread.modality == Thread.Modality.PUBLIC_THREAD
    assert post.thread.visibility == Thread.Visibility.PUBLIC
    assert post.is_original_post is True
    assert comment.message_type == Message.MessageKind.COMMENT
    assert comment.is_original_post is False
    assert direct.message_type == Message.MessageKind.CHAT
    assert friend_handle.party_link_confirmed is True
    assert friend_handle.metadata["identity_confidence"] == "name_only"
    assert Relationship._base_manager.get(kind__slug="acquaintance").started_at is not None


def _row_counts() -> tuple[int, ...]:
    """Return the importer-owned row counts used for rerun convergence."""

    return (
        Message._base_manager.count(),
        Thread._base_manager.count(),
        Handle._base_manager.count(),
        Party._base_manager.count(),
        Person._base_manager.count(),
        PartyHandle._base_manager.count(),
        Relationship._base_manager.count(),
    )


def _facebook_export(tmp_path: Path) -> Path:
    """Write a minimal export containing every pass-1 Facebook section."""

    root = tmp_path / "export"
    activity = root / "your_facebook_activity"
    profile = (
        activity
        / "personal_information"
        / "profile_information"
        / "profile_information.json"
    )
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(
        json.dumps({"profile_v2": {"name": {"full_name": "Alex Example"}}}),
        encoding="utf-8",
    )

    message_path = activity / "messages" / "inbox" / "bob_chat" / "message_1.json"
    message_path.parent.mkdir(parents=True, exist_ok=True)
    message_path.write_text(
        json.dumps(
            {
                "title": "Bob Friend",
                "participants": [{"name": "Alex Example"}, {"name": "Bob Friend"}],
                "messages": [
                    {
                        "sender_name": "Bob Friend",
                        "timestamp_ms": 1_700_000_000_000,
                        "content": "Hello from Facebook",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    posts = activity / "posts" / "your_posts_1.json"
    posts.parent.mkdir(parents=True, exist_ok=True)
    posts.write_text(
        json.dumps(
            [
                {
                    "timestamp": 1_700_000_100,
                    "title": "Shared title",
                    "data": [{"post": "My #first post"}],
                }
            ]
        ),
        encoding="utf-8",
    )

    comments = activity / "comments_and_reactions" / "comments.json"
    comments.parent.mkdir(parents=True, exist_ok=True)
    comments.write_text(
        json.dumps(
            {
                "comments_v2": [
                    {
                        "timestamp": 1_700_000_100,
                        "title": "Shared title",
                        "data": [{"comment": {"comment": "A reply"}}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    friends = activity / "connections" / "friends" / "your_friends.json"
    friends.parent.mkdir(parents=True, exist_ok=True)
    friends.write_text(
        json.dumps(
            {
                "friends_v2": [
                    {
                        "name": "Bob Friend",
                        "timestamp": 1_600_000_000,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return root
