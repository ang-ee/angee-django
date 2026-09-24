"""Channel backend contract — ingest and deliver messages through external sources.

A :class:`~angee.messaging.models.Channel` (an ``integrate.Integration`` child +
``Bridge``) selects one ``ChannelBackend`` by registry key. The backend does the
per-source *transport* + *parse* — ``extract`` returns neutral
:class:`ParsedMessage` rows (a recursive :class:`ParsedPart` body, sender/recipient
:class:`ParsedHandle`\\s, RFC-5322 threading hints). The *map* onto messaging —
thread resolution, the idempotent channel-scoped external-id upsert, the Part /
Fragment tree (including the sparse title/header parts), and the quotation graph —
is owned by ``Message.objects.ingest`` + the managers, so every source shares one
write path. Outbound messages take the symmetric ``ChannelBackend.deliver`` path
through ``angee.jobs``. ``messaging_integrate_imap`` contributes the ``imap``
backend; the ``manual`` null-object keeps the registry non-empty when no source is
installed.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, ClassVar

from django.apps import apps

from angee.base.db import get_write_alias
from angee.integrate.http import HttpClientMixin
from angee.integrate.impl import BridgeImpl, LiveBridgeImpl
from angee.integrate.streams import ApplyResult, SemanticError, StreamDefinition, StreamPage

INLINE_MEDIA_PREFIXES = ("image/", "video/", "audio/")

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParsedHandle:
    """A reachable address parsed from a message (sender or recipient).

    ``external_id`` carries the source's stable identifier for the address when
    it differs from ``value`` (a chat platform's user id behind a display
    number). The map resolves on ``(platform, external_id)`` first, so an
    address whose human-readable ``value`` drifts still converges on one handle.
    """

    platform: str
    value: str
    display_name: str = ""
    external_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    """Source-owned identity evidence used to refresh the resolved Handle."""


@dataclass(frozen=True)
class ParsedRecipient:
    """One addressed party on a message and its envelope role."""

    handle: ParsedHandle
    role: str = "to"  # from / to / cc / bcc


@dataclass(frozen=True)
class ParsedPart:
    """One recursive body node — the neutral MIME/JMAP part shape.

    A text part carries ``text`` (content-addressed into a ``Fragment`` by the map);
    a byte part carries ``content`` (ingested into a ``storage.File``). ``role`` is
    the query axis (body / quoted / signature / header); ``disposition`` separates
    inline parts from attachments.
    """

    type: str = "text/plain"
    disposition: str = "inline"  # inline / attachment
    role: str = "body"  # body / quoted / signature / header
    text: str = ""
    name: str = ""
    cid: str = ""
    content: bytes | None = None
    children: tuple[ParsedPart, ...] = ()


@dataclass(frozen=True)
class MediaItem:
    """One resolved media payload; ``content=None`` preserves a failed fetch."""

    mime: str = "application/octet-stream"
    name: str = ""
    content: bytes | None = None


@dataclass(frozen=True)
class ParsedThread:
    """The conversation a chat message belongs to, named by the source.

    Email threads are *resolved* (reply headers, subject); a chat source *names*
    its conversation outright. ``external_id`` is the source's raw conversation
    id, scoped by the adapter exactly like ``ParsedMessage.external_id`` — the
    map namespaces it (the ``chat:`` thread key), so adapters never compose
    prefixes. ``modality``/``visibility``/``title`` land on a newly created
    thread only; an established thread keeps its own. ``visibility`` is the
    adapter's hint for a source that knows its conversation is not private (a
    broadcast feed passes ``public``); empty means the private default.
    """

    external_id: str
    modality: str = ""
    visibility: str = ""
    title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    """Source-owned conversation facts written when the thread is first created."""


@dataclass(frozen=True)
class ParsedMessage:
    """One message parsed from a source, neutral of the wire format.

    ``external_id`` is the idempotency key, unique *within the producing channel*
    (e.g. the RFC-5322 Message-ID; a chat adapter embeds its chat scope). The map
    stores ``subject`` as a ``TITLE`` part and each ``headers`` pair as a ``HEADER``
    part — sparse, fragment-backed rows only messages that have them pay for.
    Adapters emit only headers worth keeping standalone (their retained-header
    allow-list); the lossless envelope stays in ``metadata``. ``in_reply_to`` /
    ``references`` carry the threading hints the map resolves; ``body`` is the
    recursive part tree.
    """

    external_id: str
    platform: str
    direction: str = "inbound"
    subject: str = ""
    headers: tuple[tuple[str, str], ...] = ()
    sender: ParsedHandle | None = None
    recipients: tuple[ParsedRecipient, ...] = ()
    sent_at: datetime | None = None
    received_at: datetime | None = None
    in_reply_to: str = ""
    references: tuple[str, ...] = ()
    thread: ParsedThread | None = None
    body: ParsedPart | None = None
    metadata: dict = field(default_factory=dict)

    def with_media(self, media: tuple[MediaItem, ...]) -> ParsedMessage:
        """Return this neutral message with resolved media merged into its body."""

        return replace(self, body=body_part(media=media, body=self.body))


def media_part(item: Any) -> ParsedPart:
    """Return one media part, preserving failed downloads as visible marker text.

    A media item with ``content=None`` is never dropped: it becomes a
    ``[media unavailable: ...]`` body marker so the message remains loss-aware.
    """

    mime = str(getattr(item, "mime", "") or "application/octet-stream")
    name = str(getattr(item, "name", "") or "")
    content = getattr(item, "content", None)
    if content is None:
        label = name or mime
        return ParsedPart(type="text/plain", role="body", text=f"[media unavailable: {label}]")
    inline = mime.startswith(INLINE_MEDIA_PREFIXES)
    return ParsedPart(
        type=mime,
        disposition="inline" if inline else "attachment",
        name=name,
        content=content,
    )


def body_part(
    text: str = "",
    media: Any = (),
    *,
    body: ParsedPart | None = None,
) -> ParsedPart | None:
    """Build or extend a recursive body tree with text and resolved media."""

    parts: list[ParsedPart] = [body] if body is not None else []
    if text:
        parts.append(ParsedPart(type="text/plain", role="body", text=text))
    parts.extend(media_part(item) for item in media)
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return ParsedPart(type="multipart/mixed", children=tuple(parts))


class ChannelBackend(BridgeImpl, HttpClientMixin):
    """Abstract backend that fetches, parses, and optionally delivers messages.

    ``self.bridge`` is the ``Channel`` row — its ``config`` carries the source
    settings and ``self.bridge.credential`` authenticates — and ``self.http`` is the
    shared SSRF-pinned client. Incremental state lives on each ``SyncStream``.
    """

    category = "channel"
    label = "Channel"
    icon = "inbox"

    message_platform = "email"
    """Platform stamped on locally submitted document Messages for this channel."""

    quote_edges: ClassVar[bool] = True
    """Whether ingest should build the email shared-fragment quotation graph."""

    def streams(self, *, deadline: float | None = None, using: str | None = None) -> tuple[StreamDefinition, ...]:
        """Declare pull streams; manual, live and outbound-only channels have none."""

        return ()

    def apply_record(self, stream: Any, record: ParsedMessage, *, using: str | None = None) -> ApplyResult:
        """Compose messaging's idempotent ingest inside the driver's page transaction."""

        using = get_write_alias(type(self.bridge), using=using, instance=self.bridge)
        if not record.external_id:
            raise SemanticError("missing_external_id")
        (message,) = (
            apps.get_model("messaging", "Message")
            .objects.db_manager(using)
            .ingest([record], channel=self.bridge, quote_edges=False, using=using)
        )
        return ApplyResult(target=message)

    def finish_page(
        self,
        stream: Any,
        page: StreamPage,
        outcomes: Sequence[ApplyResult],
        *,
        using: str | None = None,
    ) -> None:
        """Resolve email quotation links after the whole applied page is visible."""

        if self.quote_edges:
            using = get_write_alias(type(self.bridge), using=using, instance=self.bridge)
            apps.get_model("messaging", "Message").objects.db_manager(using).resolve_ingest_edges(
                [outcome.bound_target for outcome in outcomes if outcome.bound_target is not None],
                using=using,
            )

    def test_connection(self) -> str:
        """Prove this backend can reach its source; return the operator message.

        The console's "Test connection" verb lands here through
        ``Channel.test_connection``. A transport backend overrides this with a
        real handshake (IMAP logs in and out); the default proves only the
        channel's credential through the row's own probe. Failures raise
        ``IntegrationError`` with the operator-safe reason.
        """

        return self.bridge.probe_credential()

    def deliver(self, message: Any) -> bool:
        """Deliver one outbound message; return whether a transport accepted it.

        Inbound-only backends inherit this safe, explicit decline so adding the
        outbound seam does not turn an existing source into a crashing worker.
        The message delivery owner records the ``False`` result as ``failed``.
        """

        logger.warning(
            "Channel backend %s does not support outbound delivery for message %s.",
            type(self).__name__,
            getattr(message, "pk", None),
        )
        return False

    def start_live(self, *, using: str | None = None) -> None:
        """Dispatch this source's live ingest (start a session, renew a subscription).

        ``Channel.start_live`` owns the persisted desired-state and calls this
        hook for the vendor action only — a live backend enqueues or renews its
        long-lived worker here. The default is a no-op: a poll-only backend has
        no live mode, and the channel is simply marked live-desired with nothing
        to dispatch.
        """

    def stop_live(self, *, using: str | None = None) -> None:
        """Dispatch this source's live-ingest stop.

        The counterpart of :meth:`start_live`; ``Channel.stop_live`` persists the
        desired-state and a live backend's session notices it cooperatively, so
        most backends need nothing here. The default is a no-op.
        """


class LiveChannelBackend(LiveBridgeImpl, ChannelBackend):
    """Channel backend whose messages arrive through a long-lived live session."""

    category = "channel"
    label = "Live Channel"
    icon = "inbox"

    quote_edges: ClassVar[bool] = False

    media_item_class: ClassVar[type[MediaItem]] = MediaItem
    """DTO class used to attach downloaded media to a queued live message."""

    def parse_live_message(self, message: Any) -> ParsedMessage:
        """Map one queued live message DTO onto the neutral messaging seam."""

        raise NotImplementedError("LiveChannelBackend subclasses must implement parse_live_message().")


class ManualChannelBackend(ChannelBackend):
    """The null-object default: a channel with no source backend ingests nothing.

    Keeps ``ANGEE_CHANNEL_BACKEND_CLASSES`` non-empty when no source addon is
    installed (``ImplClassField`` requires a non-empty registry), so the GraphQL
    enum is never empty and a new channel always has a selectable backend.
    """

    key = "manual"
    label = "Manual"


class WebformChannelBackend(ChannelBackend):
    """Vendor-free public-form channel populated only by its curated HTTP view."""

    key = "webform"
    label = "Public Webform"
    quote_edges = False
