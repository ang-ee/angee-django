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

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from math import isfinite
from typing import Any, ClassVar, Self, cast

import httpx2
from django.apps import apps
from django.core.exceptions import ValidationError
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field, model_validator
from rebac import system_context

from angee.integrate.discovery import ConnectionDiscovery
from angee.integrate.errors import IntegrationError, RequestNotSentError
from angee.integrate.http import HttpClientMixin, ResponseTooLargeError
from angee.integrate.impl import AdapterContractError, BridgeImpl
from angee.integrate.locks import bridge_advisory_lock
from angee.integrate.net import AddressNotAllowedError
from angee.integrate.states import StreamDefinition
from angee.integrate.streams import ApplyResult, SemanticError, StreamPage
from angee.messaging.backends import (
    DeliveryOutcome,
    DeliveryUncertain,
    ManualChannelBackend,
    ParsedHandle,
    ParsedMessage,
)
from angee.models import SourceKind
from angee.posts.ingest import land_post_relations, land_posts

RECONCILIATION_SKEW_MARGIN = timedelta(minutes=3)
"""Provider clocks and whole-second timestamps can precede the local claim."""


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


@dataclass(frozen=True)
class RecordRefusal:
    """Safe record identity and semantic code that extract() may put in a page.

    Use a stable, non-secret external_id (or deterministic fallback) and a bounded
    code, never raw provider payloads. The base raises SemanticError during apply,
    inside integrate's page transaction, with details['external_key'] = external_id.
    Refusals consume page slots and are excluded from relation finishing.
    """

    external_id: str
    code: str


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

    class Config(BaseModel):
        """Daily/re-observation budgets, reply reserve and active window; extend per provider."""

        model_config = ConfigDict(extra="allow")
        quota_limit: int = Field(default=10000, ge=1)
        reply_reserve: int = Field(default=100, ge=0)
        active_thread_days: int = Field(default=30, ge=1)
        reobserve_units_per_cycle: int = Field(default=100, ge=0)

        @model_validator(mode="after")
        def check_reserve(self) -> Self:
            """Keep a positive polling allowance within the configured daily ceiling."""

            if self.reply_reserve >= self.quota_limit:
                raise ValueError("reply_reserve must be smaller than quota_limit")
            return self

    config_model = Config
    quota_reset_timezone: ClassVar[str] = "UTC"
    """IANA reset timezone; daily periods honor local midnight, including DST."""
    publish_reply_cost: ClassVar[int] = 1
    find_published_reply_cost: ClassVar[int] = 1
    """Provider-declared API units for each publication or reconciliation hook call."""
    delivery_visibility_delay: ClassVar[timedelta] = timedelta(minutes=3)
    """Minimum age of the first possibly accepted claim before reply lookup."""

    def __init__(self, integration: Any) -> None:
        """Bind one sync-cycle adapter; re-observation shares its bounded allowance."""

        super().__init__(integration)
        self._reobserve_units_used = 0

    @property
    def policy(self) -> Config:
        """Read current validated configuration through the implementation descriptor."""

        return cast(FeedBackend.Config, self.parse_config(self.bridge.config))

    def quota_scope(self) -> str:
        """Declare the namespaced provider budget identity, shared by every spender.

        Project-wide budgets must override this with the OAuth client's identity;
        account-wide budgets may retain this integration-scoped default. Never
        put secrets in the key. Stream reads call consume_quota(delivery=False).
        deliver() debits declared hook costs with delivery=True; hooks never debit.
        """

        return f"{self.key}:integration:{self.bridge.pk}"

    def consume_quota(self, units: int, *, delivery: bool = False, now: datetime | None = None) -> Any:
        """Debit the one quota owner; polling leaves the configured reply reserve.

        Read the limit at each check. Return the period for provider quota-error
        horizons; a local refusal is a native transient IntegrationError.
        """

        moment = now or timezone.now()
        policy = self.policy
        quota = apps.get_model("posts", "Quota").objects
        with system_context(reason="posts.quota"):
            period = self.current_quota_period(now=moment)
            if not quota.consume(
                period=period,
                units=units, limit=policy.quota_limit, reserve=0 if delivery else policy.reply_reserve, now=moment,
            ):
                raise IntegrationError("The feed request budget is exhausted.", retry_after=period.period_end - moment)
        return period

    def current_quota_period(self, *, now: datetime | None = None) -> Any:
        """Open and return the current scope period without debiting any units.

        Hooks may use period_end for provider quota-error horizons. Open through
        QuotaManager, rather than query for a prior debit: midnight may have passed
        since that debit. Missing periods are created, never DoesNotExist.
        """

        with system_context(reason="posts.quota"):
            return apps.get_model("posts", "Quota").objects.open_period(
                integration=self.bridge, scope=self.quota_scope(), reset_timezone=self.quota_reset_timezone, now=now,
            )

    def consume_reobserve_quota(self, units: int, *, now: datetime | None = None) -> bool:
        """Reserve re-observation units before I/O, including the daily polling debit.

        One adapter instance is one sync cycle (integrate constructs a fresh
        adapter for each parallel partition). Share this allowance across pages;
        do not reset it in extract(). False spends neither allowance nor quota.
        On False, finish this cycle on a separately declared event-feed stream;
        retain its cursor for the next cycle. With no pending records return
        StreamPage((), stream.cursor, exhausted=True); otherwise commit the fetched
        records and their cursor before stopping. Live/history requests use
        consume_quota() and remain independent. units must be positive.
        Zero disables re-observation. An exhausted daily polling quota also returns
        False without spending this allowance; the live stream reports that
        exhaustion. API failures after a debit still spend it.
        """

        if units < 1:
            raise ValueError("Re-observation units must be positive.")
        if self._reobserve_units_used + units > self.policy.reobserve_units_per_cycle:
            return False
        try:
            self.consume_quota(units, now=now)
        except IntegrationError:
            return False
        self._reobserve_units_used += units
        return True

    def request_json(
        self, method: str, url: str, *,
        is_transient: Callable[[httpx2.Response, dict[str, Any]], bool],
        refusal: Callable[[httpx2.Response, dict[str, Any]], Exception | None],
        retry_delay: Callable[[httpx2.Response], timedelta] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send a GET/POST through self.http and classify one JSON-object response.

        Pass native HttpClient.request keyword arguments. RequestNotSentError is
        re-raised unchanged. Transport/OSError, oversized responses, 5xx, a success
        whose body is not a JSON object, and is_transient(response, data) are
        uncertain for POST and native transient IntegrationError for GET. The
        transient predicate runs before refusal even on 5xx. A 4xx whose body is not
        a JSON object answered the request: both predicates run with empty data, and
        it is never uncertain.

        refusal(response, data) returns a provider-owned exception or None. Use
        native IntegrationError(transient=True, retry_after=...) for definite rate
        or quota refusals, permanent IntegrationError for rejection, CursorInvalid
        for cursor refusal, or SemanticError for record-local read refusal. A generic
        HTTP >= 400 refusal follows if neither predicate handled it. Predicate
        exceptions propagate. All public messages must be bounded and safe.

        retry_delay(response) may combine usage headers with retry_after(response);
        by default self.retry_after parses Retry-After (including overrides).
        This helper never debits quota; stream callers debit before it and delivery
        hooks use deliver()'s debit. Only GET and POST are accepted.
        """

        method = method.upper()
        if method not in {"GET", "POST"}:
            raise ValueError("Feed JSON requests must use GET or POST.")
        try:
            response = self.http.request(method, url, **kwargs)
        except RequestNotSentError:
            raise
        except AddressNotAllowedError as error:
            raise IntegrationError("The feed address is not allowed.") from error
        except (OSError, httpx2.TransportError, ResponseTooLargeError) as error:
            if method == "POST":
                raise DeliveryUncertain("The feed reply acknowledgement was lost.") from error
            raise IntegrationError("The feed could not be read.", transient=True) from error
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object.")
        except ValueError as error:
            if not 400 <= response.status_code < 500:
                if method == "POST":
                    raise DeliveryUncertain("The feed reply acknowledgement was unreadable.") from error
                raise IntegrationError(
                    "The feed response was unreadable.", retry_after=(retry_delay or self.retry_after)(response),
                ) from error
            # A 4xx answered the request: classify it with the predicates, without a body.
            data = {}
        if is_transient(response, data) or response.status_code >= 500:
            if method == "POST":
                raise DeliveryUncertain("The feed reply acknowledgement was lost.")
            raise IntegrationError(
                "The feed is temporarily unavailable.", retry_after=(retry_delay or self.retry_after)(response),
            )
        refusal_error = refusal(response, data)
        if refusal_error is not None:
            raise refusal_error
        if response.status_code >= 400:
            raise IntegrationError("The feed refused the request.")
        return data

    @staticmethod
    def retry_after(response: Any, *, now: datetime | None = None) -> timedelta:
        """Parse a provider Retry-After delta/date, falling back to one minute."""

        value = response.headers.get("Retry-After", "")
        try:
            seconds = float(value)
            if isfinite(seconds) and seconds > 0:
                return timedelta(seconds=seconds)
        except (ValueError, TypeError, OverflowError):
            pass
        try:
            at = parsedate_to_datetime(value)
            if at.tzinfo is not None:
                return max(at - (now or timezone.now()), timedelta(seconds=1))
        except (ValueError, TypeError, OverflowError):
            pass
        return timedelta(minutes=1)

    @classmethod
    def reconciles_delivery(cls) -> bool:
        """Overriding the lookup hook declares safe recovery of ambiguous sends."""

        return cls.find_published_reply is not FeedBackend.find_published_reply

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

    def record_key(self, record: ParsedPost | RecordRefusal) -> str:
        """Safe event identity for integrate's per-record quarantine."""

        return record.external_id if isinstance(record, RecordRefusal) else record.message.external_id

    def apply_record(self, stream: Any, record: ParsedPost | RecordRefusal) -> ApplyResult:
        """Land one record, with live classification independent of its stream."""

        if isinstance(record, RecordRefusal):
            raise SemanticError(record.code, details={"external_key": record.external_id})
        if not record.message.external_id:
            raise SemanticError("missing_external_id")
        if self.bridge.live_since is None and self.bridge.external_id and self.bridge.discovery_ready:
            # A robust on-commit receiver can fail after discovery committed.
            # Repair before classification; NULL never makes backfilled records live.
            with system_context(reason="posts.discovery.horizon"):
                self.bridge.discovery_finished()
        (message,) = land_posts(
            self.bridge, [record], owner_id=self.bridge.owner_id,
            historical=self.bridge.is_historical(record), relations=False,
        )
        return ApplyResult(target=message)

    def finish_page(self, stream: Any, page: StreamPage, outcomes: Sequence[ApplyResult]) -> None:
        """Resolve cross-post relations after all successful records are visible."""

        messages = [outcome.bound_target for outcome in outcomes if outcome.bound_target is not None]
        records = [record for record in page.records if isinstance(record, ParsedPost)]
        land_post_relations(self.bridge, records, messages, owner_id=self.bridge.owner_id)

    def deliver(self, message: Any) -> DeliveryOutcome:
        """Reconcile a previous attempt, then publish one reply through provider hooks.

        Only a possibly accepted settlement permits lookup. Recovery matches
        publisher, top_level_comment() anchor and body within the tolerant claim
        window. A complete lookup must prove absence before returning
        None; an incomplete/unavailable lookup raises and never permits a POST.
        Native transient errors promise non-acceptance (including integrate's
        typed not-sent connection errors); permanent refusals fail. Post-send
        ambiguity raises DeliveryUncertain and is settled before reconciliation.
        Resolve the anchor during context checks before any quota debit/hook;
        context loss is a definite refusal unless an earlier send was possibly
        accepted. Lookup waits for delivery_started_at + delivery_visibility_delay.
        """

        valid_context = (
            message.channel_id == self.bridge.pk and message.direction == "outbound"
            and message.parent_id is not None and message.parent.channel_id == self.bridge.pk
            and not message.parent.is_original_post and bool(message.body_text().strip())
        )
        if valid_context:
            try:
                message.top_level_comment()
            except ValidationError:
                valid_context = False
        if not valid_context:
            if message.delivery_needs_reconciliation:
                raise DeliveryUncertain("The prior reply no longer has enough context to reconcile.")
            return DeliveryOutcome(accepted=False)
        if message.delivery_needs_reconciliation and self.reconciles_delivery():
            identity = self._reconcile_delivery(message)
            if identity:
                return DeliveryOutcome(accepted=True, provider_id=identity)
            type(message).objects.confirm_delivery_absent(message)
            if message.is_trashed:
                return DeliveryOutcome(accepted=False)
        self.consume_quota(self.publish_reply_cost, delivery=True)
        identity = self.publish_reply(message)
        if not isinstance(identity, str) or not identity.strip():
            raise DeliveryUncertain("The provider did not acknowledge a reply identity.")
        return DeliveryOutcome(accepted=True, provider_id=identity)

    def _reconcile_delivery(self, message: Any) -> str | None:
        """A failed lookup after a possible effect is always uncertain, even if transient."""

        if message.delivery_started_at is None:
            raise DeliveryUncertain("The original delivery claim time is unavailable.")
        if timezone.now() < message.delivery_started_at + self.delivery_visibility_delay:
            raise DeliveryUncertain("The prior reply is still within the provider visibility margin.")
        since = message.delivery_started_at.replace(microsecond=0) - RECONCILIATION_SKEW_MARGIN
        try:
            if message.has_ambiguous_delivery_reply(since=since):
                raise DeliveryUncertain("Another active local reply shares this anchor and body.")
            self.consume_quota(self.find_published_reply_cost, delivery=True)
            identity = self.find_published_reply(message, since=since)
        except Exception:
            raise DeliveryUncertain("The prior reply could not be reconciled; inspect the thread.") from None
        return identity

    def publish_reply(self, message: Any) -> str:
        """POST a reply on message.top_level_comment(), returning its nonblank id.

        Use message.top_level_comment() as the provider anchor, serialize
        message.body_text(), and raise native IntegrationError refusals
        or DeliveryUncertain for missing/post-send acknowledgements. Compose
        request_json() for shared HTTP classification. No DB writes except opening
        current_quota_period() for quota-error horizons. Generic delivery preamble,
        quota policy and transient classification belong to the base helpers.
        deliver() debits publish_reply_cost with delivery=True before this hook.
        Recovery receives a lower bound rounded down to seconds minus the named
        RECONCILIATION_SKEW_MARGIN; this hook never searches for previous replies.
        """

        raise IntegrationError("This feed does not support reply publication.")

    def find_published_reply(self, message: Any, *, since: datetime) -> str | None:
        """Find this publisher's matching reply on its top_level_comment() anchor.

        Search newest-first, stop at records older than the inclusive lower bound,
        and match author, anchor and body. The base supplies since rounded down
        to seconds minus RECONCILIATION_SKEW_MARGIN. Before lookup it rejects
        ambiguous active local replies with the same anchor/body in that window.
        Identical text under another anchor or before since is never evidence.
        An operator-typed Page reply with the same publisher, anchor and body in
        this window counts as the delivered reply; the provider cannot distinguish
        its authorship from this attempt.
        The base debits find_published_reply_cost with delivery=True; this hook
        performs no quota debits or delivery-state writes.
        Return None only after a complete, authoritative absence check; raise if
        indexing, pagination or transport prevents that guarantee. Implementing
        this hook declares reconciliation capability to messaging's recovery owner.
        Lookup never runs before delivery_started_at + delivery_visibility_delay.
        Compose request_json(); current_quota_period() may open a reset period for
        quota-error horizons, but this hook never debits it.
        """

        raise DeliveryUncertain("This feed cannot reconcile reply publication.")


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
    """Mark a feed's Channel parent as a content source without a second transport.

    A feed is an import source: what is landed from a feed's own history, such as a
    contact its comments name, is that feed's assertion, ranked below a person's.
    """

    key = "feed"
    category = "feed"
    label = "Feed"
    icon = "rss"
    source_kind = SourceKind.IMPORT

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
