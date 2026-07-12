"""Feed backend contract — poll an external platform for public posts.

A :class:`~angee.social.models.Feed` (an ``integrate.Integration`` child + ``Bridge``)
selects one ``FeedBackend`` by registry key. The backend does the per-platform
*transport* + *parse* — ``fetch_posts`` returns neutral :class:`ParsedPost` rows.
Each post's *core* (thread/message/parts) reuses messaging's neutral
:class:`~angee.messaging.backends.ParsedMessage`, so the idempotent channel-scoped
external-id upsert, the Part/Fragment tree, and thread resolution stay owned by
``Message.objects.ingest`` — social never forks that write path. A
post adds the social *overlay*: rolled-up :class:`ParsedMetrics`, per-actor
:class:`ParsedReaction`\\s, and cross-post :class:`ParsedRelation`\\s that the
:class:`~angee.social.models.Feed` maps onto ``PostMetrics``, the reused
``messaging.Reaction`` table, and the shared ``messaging.MessageEdge`` graph.

Source addons (``social_integrate_youtube``/``…_facebook``) contribute concrete
backends; the ``manual`` null-object keeps ``ANGEE_SOCIAL_FEED_BACKEND_CLASSES``
non-empty when no source is installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from angee.integrate.http import HttpClientMixin
from angee.integrate.impl import BridgeImpl
from angee.messaging.backends import ParsedHandle, ParsedMessage


@dataclass(frozen=True)
class ParsedMetrics:
    """Rolled-up public engagement counters parsed for one post."""

    view_count: int = 0
    like_count: int = 0
    repost_count: int = 0
    quote_count: int = 0
    reply_count: int = 0
    bookmark_count: int = 0
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedReaction:
    """One attributed reaction on a post (a like/repost or an emoji)."""

    handle: ParsedHandle
    reaction: str


@dataclass(frozen=True)
class ParsedRelation:
    """One declared cross-post relation from this post to another post.

    ``dst_external_id`` names the related post by its platform id; the map resolves
    both endpoints and writes the edge onto the shared ``messaging.MessageEdge``
    graph. ``kind`` is a ``messaging.MessageEdge.EdgeKind`` value
    (mention / crosspost / forward / quote).
    """

    dst_external_id: str
    kind: str = "crosspost"


@dataclass(frozen=True)
class ParsedPost:
    """One public post parsed from a feed — the message core plus the social overlay.

    ``message`` is the neutral messaging shape (its ``external_id`` is the
    idempotency key, ``in_reply_to`` carries the parent post id). ``is_original_post``
    marks a top-level post (no parent). The overlay is optional and applied after the
    core lands.
    """

    message: ParsedMessage
    is_original_post: bool = False
    subject_url: str = ""
    tags: tuple[str, ...] = ()
    metrics: ParsedMetrics | None = None
    reactions: tuple[ParsedReaction, ...] = ()
    relations: tuple[ParsedRelation, ...] = ()


class FeedBackend(BridgeImpl, HttpClientMixin):
    """Abstract backend that fetches and parses a public feed source.

    ``self.bridge`` is the ``Feed`` row — its ``config`` carries the source settings
    and ``self.bridge.credential`` authenticates — and ``self.http`` is the shared
    SSRF-pinned client. Incremental state lives on ``self.bridge.cursor``.
    """

    category = "feed"
    label = "Feed"
    icon = "rss"

    def fetch_posts(self) -> list[ParsedPost]:
        """Return new posts since the feed cursor as neutral dataclasses."""

        raise NotImplementedError("FeedBackend subclasses must implement fetch_posts().")


class ManualFeedBackend(FeedBackend):
    """The null-object default: a feed with no source backend ingests nothing.

    Keeps ``ANGEE_SOCIAL_FEED_BACKEND_CLASSES`` non-empty when no source addon is
    installed (``ImplClassField`` requires a non-empty registry), so the GraphQL
    enum is never empty and a draft feed always has a selectable backend.
    """

    key = "manual"
    label = "Manual"

    def fetch_posts(self) -> list[ParsedPost]:
        """Return no posts — a manual feed is populated by hand."""

        return []
