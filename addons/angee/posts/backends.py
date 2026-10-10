"""Feed backend contract — poll an external platform for public posts.

A :class:`~angee.posts.models.Feed` (a ``messaging.Channel`` child and ``Bridge``)
selects one ``FeedBackend`` by registry key. The backend does the per-platform
*transport* + *parse* through integrate ``streams``/``extract`` returning
:class:`ParsedPost` records; the shared driver owns cursor commits and quarantine.
Each post's *core* (thread/message/parts) reuses messaging's neutral
:class:`~angee.messaging.backends.ParsedMessage`, so the idempotent channel-scoped
external-id upsert, the Part/Fragment tree, and thread resolution stay owned by
``Message.objects.ingest`` — posts never forks that write path. A
post adds the posts *overlay*: rolled-up :class:`ParsedMetrics`, per-actor
:class:`ParsedReaction`\\s, and cross-post :class:`ParsedRelation`\\s that the
:class:`~angee.posts.models.Feed` maps onto ``PostMetrics``, the reused
``messaging.Reaction`` table, and the shared ``messaging.MessageEdge`` graph.

Source addons (``posts_integrate_youtube``/``…_facebook``) contribute concrete
backends; the ``manual`` null-object keeps ``ANGEE_POSTS_FEED_BACKEND_CLASSES``
non-empty when no source is installed.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any, ClassVar

from django.apps import apps

from angee.integrate.discovery import ConnectionDiscovery
from angee.integrate.errors import IntegrationError
from angee.integrate.http import HttpClientMixin
from angee.integrate.impl import AdapterContractError, BridgeImpl
from angee.integrate.locks import bridge_advisory_lock
from angee.integrate.streams import ApplyResult, SemanticError, StreamDefinition, StreamPage
from angee.messaging.backends import DeliveryOutcome, ManualChannelBackend, ParsedHandle, ParsedMessage
from angee.posts.ingest import land_post_relations, land_posts


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
    """One public post parsed from a feed — the message core plus the posts overlay.

    ``message`` is the neutral messaging shape (its ``external_id`` is the
    idempotency key, ``in_reply_to`` carries the parent post id). ``is_original_post``
    marks a top-level post (no parent). The overlay is optional and applied after the
    core lands.
    """

    message: ParsedMessage
    is_original_post: bool = False
    subject_url: str = ""
    tags: tuple[str, ...] = ()
    """Exact parsed hashtags stored in ``message.metadata["tags"]`` on feed ingest.

    Order, spelling, and duplicates are preserved pending the backlogged
    vocabulary-governance decision; this envelope fact does not create ``Tag`` or
    ``TagAssignment`` rows.
    """
    metrics: ParsedMetrics | None = None
    reactions: tuple[ParsedReaction, ...] = ()
    relations: tuple[ParsedRelation, ...] = ()
    hidden: bool = False
    """Provider moderation state, landed through messaging trash before live events."""


class FeedBackend(BridgeImpl, HttpClientMixin):
    """Abstract backend that fetches and parses a public feed source.

    ``self.bridge`` is the ``Feed`` row — its ``config`` carries the source settings
    and ``self.bridge.credential`` authenticates — and ``self.http`` is the shared
    SSRF-pinned client. Incremental state lives on integrate's durable per-partition ``SyncStream``.
    """
    registry_setting = "ANGEE_POSTS_FEED_BACKEND_CLASSES"
    requires_connection_discovery: ClassVar[bool] = True

    category = "feed"
    label = "Feed"
    icon = "rss"
    defaults = {"backend_class": "feed"}

    def discover_connection(self, credential: Any) -> ConnectionDiscovery:
        """Require source adapters to discover their publishing identity."""

        raise AdapterContractError("Feed adapters must implement discover_connection().")

    def apply_discovery(self, discovery: ConnectionDiscovery) -> None:
        """Apply feed identity through the parties handle owner on the locked feed."""

        fields = []
        for name in ("external_id", "display_name"):
            if name in discovery.data:
                field = self.bridge._meta.get_field(name)
                value = discovery.data[name]
                if name == "display_name":
                    value = str(value or "")[:field.max_length]
                setattr(self.bridge, name, field.clean(value, self.bridge))
                fields.append(name)
        if "handle" in discovery.data:
            parsed = discovery.data["handle"]
            if not isinstance(parsed, ParsedHandle):
                raise AdapterContractError("Feed discovery handle must be a ParsedHandle.")
            self.bridge.handle = apps.get_model("parties", "Handle").objects.upsert(
                platform=parsed.platform, value=parsed.value, external_id=parsed.external_id,
                display_name=parsed.display_name, metadata=parsed.metadata, created_by_id=self.bridge.owner_id,
            )
            fields.append("handle")
        if fields:
            self.bridge.save(update_fields=(*fields, "updated_at"))

    def record_key(self, record: ParsedPost) -> str:
        """Safe event identity for integrate's per-record quarantine."""

        return record.message.external_id

    def apply_record(self, stream: Any, record: ParsedPost) -> ApplyResult:
        """Land one record, with live classification independent of its stream."""

        if not record.message.external_id:
            raise SemanticError("missing_external_id")
        (message,) = land_posts(
            self.bridge, [record], owner_id=self.bridge.owner_id,
            historical=self.bridge.is_historical(record), relations=False,
        )
        return ApplyResult(target=message)

    def finish_page(self, stream: Any, page: StreamPage, outcomes: Sequence[ApplyResult]) -> None:
        """Resolve cross-post relations after all successful records are visible."""

        messages = [outcome.bound_target for outcome in outcomes if outcome.bound_target is not None]
        land_post_relations(self.bridge, page.records, messages, owner_id=self.bridge.owner_id)

    def deliver(self, message: Any) -> DeliveryOutcome:
        """Reply publish; bridges return acceptance or raise a typed refusal."""

        return DeliveryOutcome(accepted=False)


class ManualFeedBackend(FeedBackend):
    """The null-object default: a feed with no source backend ingests nothing.

    Keeps ``ANGEE_POSTS_FEED_BACKEND_CLASSES`` non-empty when no source addon is
    installed (``ImplClassField`` requires a non-empty registry), so the GraphQL
    enum is never empty and a draft feed always has a selectable backend.
    """

    key = "manual"
    label = "Manual"

    requires_connection_discovery: ClassVar[bool] = False

    def streams(self, *, deadline: float | None = None) -> tuple[StreamDefinition, ...]:
        """Manual feeds have no remote partitions."""

        return ()


class FeedChannelBackend(ManualChannelBackend):
    """Mark a feed's Channel parent as a content source without a second transport."""

    key = "feed"
    category = "feed"
    label = "Feed"
    icon = "rss"

    def __init__(self, integration: Any) -> None:
        """Resolve the feed child once; it owns both publish and sync identity."""

        feed = integration.concrete_capability()
        if not isinstance(feed, apps.get_model("posts", "Feed")):
            raise IntegrationError("This channel's feed source is unavailable.")
        super().__init__(feed)
        self._backend = feed.backend

    def delivery_lock(self) -> AbstractContextManager[bool]:
        """Serialize publish and settlement with the feed's stream sync."""

        return bridge_advisory_lock(self.bridge)

    def deliver(self, message: Any) -> DeliveryOutcome:
        """Dispatch the Channel transport to its Feed's one publishing backend."""

        return self._backend.deliver(message)

    def close(self) -> None:
        """Close the same backend instance used for delivery."""

        self._backend.close()
