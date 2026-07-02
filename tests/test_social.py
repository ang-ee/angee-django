"""Tests for the social overlay social owns on top of messaging's ingest.

Social reuses one messaging write path (a public post *is* a ``messaging.Message``)
and adds only the overlay it owns. These cases pin that owned surface: the atomic
API-unit ledger (:class:`~angee.social.managers.QuotaManager`), the open/close
following edge (:class:`~angee.social.managers.FeedFollowManager`), the rolled-up
:class:`~angee.social.models.PostMetrics` an ingest overlays, and
:meth:`~angee.social.models.Feed.sync` delegating to ``Message.objects.ingest`` under
the public-thread modality with the email quotation builder gated off. The concrete
test models (composed the way the composer folds each source model onto one runtime
table) and the ``stub`` feed backend live in ``tests.conftest``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import pytest
from django.core.management import call_command
from django.db import connection
from rebac import (
    RelationshipTuple,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedPart
from angee.social.backends import ParsedMetrics, ParsedPost, ParsedReaction
from tests.conftest import (
    SOCIAL_TEST_MODELS,
    Feed,
    FeedFollow,
    PostMetrics,
    Quota,
    StubFeedBackend,
    _clear_model_tables,
    _create_missing_tables,
    create_user,
    make_integration,
)
from tests.test_messaging import (
    MESSAGING_TEST_MODELS,
    Handle,
    Message,
    MessageEdge,
    Reaction,
    Thread,
)

_AT = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def social_tables() -> Iterator[None]:
    """Create the messaging + social concrete tables and sync the REBAC schema."""

    table_models = MESSAGING_TEST_MODELS + SOCIAL_TEST_MODELS
    created = _create_missing_tables(table_models)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        StubFeedBackend.reset()
        _clear_model_tables(table_models)
        if created:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created):
                    schema_editor.delete_model(model)


def _handle(value: str = "chan-1") -> Any:
    """Create one YouTube handle the social edges/ledger hang off."""

    return Handle._base_manager.create(platform="youtube", value=value, display_name=value)


def _feed(slug: str = "feed") -> Any:
    """Create a Feed (an Integration child + Bridge) bound to the stub backend."""

    return make_integration(slug, model=Feed, backend_class="stub")


def _post(
    external_id: str,
    *,
    text: str = "Body",
    subject: str = "Post",
    is_original_post: bool = True,
    subject_url: str = "",
    tags: tuple[str, ...] = (),
    metrics: ParsedMetrics | None = None,
) -> ParsedPost:
    """Build a neutral public ParsedPost with a single text body part."""

    return ParsedPost(
        message=ParsedMessage(
            external_id=external_id,
            platform="youtube",
            subject=subject,
            sender=ParsedHandle(platform="youtube", value="chan-1", display_name="Channel One"),
            sent_at=_AT,
            body=ParsedPart(type="text/plain", role="body", text=text),
        ),
        is_original_post=is_original_post,
        subject_url=subject_url,
        tags=tags,
        metrics=metrics,
    )


# --- Quota ledger ---------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_quota_consume_debits_the_budget(social_tables: None) -> None:
    """consume() opens the period and debits quota_used by the spent units."""

    del social_tables
    with system_context(reason="test quota consume"):
        handle = _handle()
        assert Quota.objects.consume(handle=handle, platform="youtube", units=3000, limit=10000, now=_AT) is True
        ledger = Quota._base_manager.get(handle=handle, platform="youtube")

    assert ledger.quota_used == 3000
    assert ledger.quota_limit == 10000
    assert ledger.last_updated == _AT


@pytest.mark.django_db(transaction=True)
def test_quota_consume_refuses_when_exhausted_and_leaves_ledger_untouched(social_tables: None) -> None:
    """A spend that would exceed the budget returns False without debiting."""

    del social_tables
    with system_context(reason="test quota exhaustion"):
        handle = _handle()
        assert Quota.objects.consume(handle=handle, platform="youtube", units=6000, limit=10000, now=_AT) is True
        # 6000 + 5000 > 10000 → refused, ledger stays at the prior 6000.
        assert Quota.objects.consume(handle=handle, platform="youtube", units=5000, limit=10000, now=_AT) is False
        after_refusal = Quota._base_manager.get(handle=handle).quota_used
        # 6000 + 4000 == 10000 → the budget is spendable up to the limit.
        assert Quota.objects.consume(handle=handle, platform="youtube", units=4000, limit=10000, now=_AT) is True
        exhausted = Quota._base_manager.get(handle=handle).quota_used

    assert after_refusal == 6000
    assert exhausted == 10000


# --- Following edge -------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_feed_follow_opens_the_edge_and_is_idempotent(social_tables: None) -> None:
    """follow() opens one dated interval and re-following is a no-op on the same row."""

    del social_tables
    with system_context(reason="test follow"):
        feed = _feed()
        handle = _handle()
        first = FeedFollow.objects.follow(feed=feed, handle=handle)
        second = FeedFollow.objects.follow(feed=feed, handle=handle)
        active = list(FeedFollow.objects.for_feed(feed).active())

    assert first.pk == second.pk
    assert first.started_at is not None
    assert first.ended_at is None
    assert active == [first]


@pytest.mark.django_db(transaction=True)
def test_feed_unfollow_closes_the_interval_then_follow_reopens_it(social_tables: None) -> None:
    """unfollow() closes the open interval (retained), and follow() reopens the row."""

    del social_tables
    with system_context(reason="test unfollow"):
        feed = _feed()
        handle = _handle()
        opened = FeedFollow.objects.follow(feed=feed, handle=handle)

        closed = FeedFollow.objects.unfollow(feed=feed, handle=handle)
        # A second unfollow finds no open interval to close.
        again = FeedFollow.objects.unfollow(feed=feed, handle=handle)
        after_close = FeedFollow._base_manager.get(pk=opened.pk)
        active_after_close = list(FeedFollow.objects.for_feed(feed).active())

        reopened = FeedFollow.objects.follow(feed=feed, handle=handle)
        after_reopen = FeedFollow._base_manager.get(pk=opened.pk)

    assert closed == 1
    assert again == 0
    assert after_close.ended_at is not None
    assert active_after_close == []
    # Reopening updates the retained row in place rather than minting a new one.
    assert reopened.pk == opened.pk
    assert after_reopen.ended_at is None
    assert FeedFollow._base_manager.count() == 1


# --- Feed sync: ingest delegation + engagement overlay --------------------------


@pytest.mark.django_db(transaction=True)
def test_feed_sync_overlays_post_metrics_and_public_payload(social_tables: None) -> None:
    """sync() lands the message core, then overlays metrics and the public payload."""

    del social_tables
    feed = _feed()
    metrics = ParsedMetrics(view_count=99, like_count=12, repost_count=3, reply_count=4, bookmark_count=1)
    StubFeedBackend.queue(
        feed,
        [_post("p1", subject_url="https://youtu.be/p1", tags=("news", "live"), metrics=metrics)],
    )

    with system_context(reason="test feed sync"):
        ingested = feed.sync()
        message = Message._base_manager.get(external_id="p1")
        row = PostMetrics._base_manager.get(message=message)
        thread = message.thread

    assert ingested == 1
    assert (row.view_count, row.like_count, row.repost_count, row.reply_count, row.bookmark_count) == (99, 12, 3, 4, 1)
    # A public post lands under the COMMENT kind the feed passes, not the ingest's
    # email default — the messaging owner writes the column from message_kind.
    assert message.message_type == Message.MessageKind.COMMENT
    # The public payload folds onto the shared messaging rows (same-row extension).
    assert message.is_original_post is True
    assert thread.subject_url == "https://youtu.be/p1"
    assert thread.tags == ["news", "live"]


@pytest.mark.django_db(transaction=True)
def test_feed_sync_lands_public_threads_without_minting_quote_edges(social_tables: None) -> None:
    """Posts are born PUBLIC_THREAD; the email quotation builder is gated off."""

    del social_tables
    feed = _feed()
    # Distinctive verbatim text shared by two posts would mint an email quote edge;
    # the feed sync passes quote_edges=False, so no spurious quote edge is written.
    shared = "A distinctive shared paragraph that both posts quote verbatim."
    StubFeedBackend.queue(feed, [_post("p1", text=shared), _post("p2", text=shared)])

    with system_context(reason="test feed sync modality"):
        ingested = feed.sync()
        threads = {message.thread.modality for message in Message._base_manager.all()}
        quote_edges = MessageEdge._base_manager.filter(kind="quote").count()

    assert ingested == 2
    assert threads == {Thread.Modality.PUBLIC_THREAD}
    assert quote_edges == 0


@pytest.mark.django_db(transaction=True)
def test_feed_sync_lands_public_visibility(social_tables: None) -> None:
    """A public feed's threads are born PUBLIC, not the private email-thread default."""

    del social_tables
    feed = _feed()
    StubFeedBackend.queue(feed, [_post("p1")])

    with system_context(reason="test feed sync visibility"):
        feed.sync()
        thread = Message._base_manager.get(external_id="p1").thread

    # visibility rides the messaging owner (threaded through ingest/resolve), so a
    # PUBLIC_THREAD is not mis-scoped to the private default.
    assert thread.visibility == Thread.Visibility.PUBLIC


@pytest.mark.django_db(transaction=True)
def test_feed_sync_threads_public_posts_by_subject(social_tables: None) -> None:
    """Public posts thread by normalized subject — the email semantics ``subject="Post"`` masks.

    Documents today's behavior: ``Feed.sync`` lands posts through messaging's
    subject-normalized thread resolution, so two posts sharing a normalized subject
    MERGE into one thread while a distinct subject stays separate. Whether public posts
    should thread on subject at all is open for a real feed-source backend (see the lift
    plan C/D); this pins the current behavior so a change there is deliberate, not silent.
    """

    del social_tables
    feed = _feed()
    StubFeedBackend.queue(
        feed,
        [
            _post("p1", subject="Shared subject"),
            _post("p2", subject="Shared subject"),
            _post("p3", subject="A distinct subject"),
        ],
    )

    with system_context(reason="test feed sync threading"):
        ingested = feed.sync()
        thread_by_post = {message.external_id: message.thread_id for message in Message._base_manager.all()}

    assert ingested == 3
    # p1 and p2 share a normalized subject → one thread; p3's distinct subject → its own.
    assert thread_by_post["p1"] == thread_by_post["p2"]
    assert thread_by_post["p3"] != thread_by_post["p1"]
    assert len(set(thread_by_post.values())) == 2


@pytest.mark.django_db(transaction=True)
def test_feed_sync_attributes_reactions_through_the_reaction_owner(social_tables: None) -> None:
    """Post reactions land on the reused messaging.Reaction table, batched + idempotent.

    The overlay routes attributed reactions through ``ReactionManager.attribute`` (the
    messaging owner's batched write, not a hand-rolled get_or_create), so a re-sync
    re-landing the same reactions is a no-op on the partial unique constraint.
    """

    del social_tables
    feed = _feed()
    reactor = ParsedHandle(platform="youtube", value="fan-1", display_name="Fan One")
    post = ParsedPost(
        message=ParsedMessage(
            external_id="p1",
            platform="youtube",
            subject="Post",
            sender=ParsedHandle(platform="youtube", value="chan-1", display_name="Channel One"),
            sent_at=_AT,
            body=ParsedPart(type="text/plain", role="body", text="Body"),
        ),
        is_original_post=True,
        reactions=(
            ParsedReaction(handle=reactor, reaction="like"),
            ParsedReaction(handle=reactor, reaction="repost"),
        ),
    )

    with system_context(reason="test reaction overlay"):
        StubFeedBackend.queue(feed, [post])
        feed.sync()
        first = Reaction._base_manager.filter(message__external_id="p1").count()
        StubFeedBackend.queue(feed, [post])
        feed.sync()
        rows = Reaction._base_manager.filter(message__external_id="p1")
        second = rows.count()
        values = set(rows.values_list("reaction", flat=True))
        attributed = all(row.handle_id is not None for row in rows)

    assert first == 2
    assert second == 2  # re-sync did not duplicate the attributed reactions
    assert values == {"like", "repost"}
    assert attributed is True


# --- Engagement visibility: post metrics ride the message ------------------------


def _grant(resource: Any, relation: str, user: Any) -> None:
    """Write one direct relationship tuple for ``user`` on ``resource``."""

    write_relationships(
        [
            RelationshipTuple(
                resource=to_object_ref(resource),
                relation=relation,
                subject=to_subject_ref(user),
            )
        ]
    )


@pytest.mark.django_db(transaction=True)
def test_post_metrics_read_derives_from_the_message(social_tables: None) -> None:
    """A user who can read the post can read its rolled-up engagement counts.

    ``social/post_metrics`` read derives from ``messaging/message->read`` (like the
    sibling message_star/tracking_value projections), so a message reader is not denied
    the public engagement just because the feed owner wrote the counters.
    """

    del social_tables
    feed = _feed()
    StubFeedBackend.queue(feed, [_post("p1", metrics=ParsedMetrics(view_count=7, like_count=2))])
    reader = create_user("post-reader")
    with system_context(reason="test metrics read setup"):
        feed.sync()
        message = Message._base_manager.get(external_id="p1")
        row = PostMetrics._base_manager.get(message=message)
        _grant(message, "reader", reader)

    with actor_context(reader):
        visible = PostMetrics.objects.filter(pk=row.pk).exists()

    assert visible is True
