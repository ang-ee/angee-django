"""Channel backend contract — ingest messages from an external source into messaging.

A :class:`~angee.messaging.models.Channel` (an ``integrate.Integration`` child +
``Bridge``) selects one ``ChannelBackend`` by registry key. The backend does the
per-source *transport* + *parse* — ``fetch_messages`` returns neutral
:class:`ParsedMessage` rows (a recursive :class:`ParsedPart` body, sender/recipient
:class:`ParsedHandle`\\s, RFC-5322 threading hints). The *map* onto messaging —
thread resolution, the idempotent channel-scoped external-id upsert, the Part /
Fragment tree (including the sparse title/header parts), and the quotation graph —
is owned by ``Message.objects.ingest`` + the managers, so every source shares one
write path. ``messaging_integrate_imap`` contributes the ``imap`` backend; the
``manual`` null-object keeps the registry non-empty when no source is installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from angee.integrate.http import HttpClientMixin
from angee.integrate.impl import BridgeImpl


@dataclass(frozen=True)
class ParsedHandle:
    """A reachable address parsed from a message (sender or recipient)."""

    platform: str
    value: str
    display_name: str = ""


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
    body: ParsedPart | None = None
    metadata: dict = field(default_factory=dict)


class ChannelBackend(BridgeImpl, HttpClientMixin):
    """Abstract backend that fetches and parses a message source.

    ``self.bridge`` is the ``Channel`` row — its ``config`` carries the source
    settings and ``self.bridge.credential`` authenticates — and ``self.http`` is the
    shared SSRF-pinned client. Incremental state lives on ``self.bridge.cursor``.
    """

    category = "channel"
    label = "Channel"
    icon = "inbox"

    partition: str | None = None
    """When set, this instance drains only the named partition (see :meth:`sync_partitions`)."""

    def sync_partitions(self) -> tuple[str, ...]:
        """Return this source's independently drainable partition keys, or ``()``.

        A backend whose source splits into units with *independent cursor state*
        — IMAP mailboxes, each with its own UID watermark — returns their keys.
        ``Channel.sync`` then drains each partition on its own backend instance
        (its own transport connection) in parallel threads, persisting each
        partition's cursor slice separately so one partition's crash never skips
        another's mail. The default ``()`` keeps the serial single-drain contract.
        """

        return ()

    def partition_cursor_slice(self, partition: str) -> tuple[tuple[str, ...], Any]:
        """Return ``(path, value)`` — one partition's fragment of ``bridge.cursor``.

        ``path`` addresses the nested cursor location this partition owns and
        ``value`` is its current in-memory state; ``Channel`` merges exactly that
        slice into the persisted cursor under a row lock, so parallel partitions
        never clobber each other and never persist a sibling's pre-ingest advance.
        """

        raise NotImplementedError("Partitioned backends must implement partition_cursor_slice().")

    def fetch_messages(self) -> list[ParsedMessage]:
        """Return the next batch of new messages since the bridge cursor.

        ``Channel.sync`` drains the backend — it calls this repeatedly on one
        instance until an empty list says the source is exhausted. A single-shot
        backend may return everything in its first batch; a paging backend keeps
        its position on the instance and advances its in-memory ``bridge.cursor``
        past each returned batch, so a large backfill streams with bounded memory
        and an interrupted run resumes from the last *persisted* cursor.
        """

        raise NotImplementedError("ChannelBackend subclasses must implement fetch_messages().")

    def close(self) -> None:
        """Release any transport this backend holds; called when the drain ends.

        ``Channel.sync`` calls this in ``finally``, so a run that fails mid-drain
        does not leak an authenticated connection. The default is a no-op for
        connectionless backends.
        """


class ManualChannelBackend(ChannelBackend):
    """The null-object default: a channel with no source backend ingests nothing.

    Keeps ``ANGEE_CHANNEL_BACKEND_CLASSES`` non-empty when no source addon is
    installed (``ImplClassField`` requires a non-empty registry), so the GraphQL
    enum is never empty and a draft channel always has a selectable backend.
    """

    key = "manual"
    label = "Manual"

    def fetch_messages(self) -> list[ParsedMessage]:
        """Return no messages — a manual channel is populated by hand."""

        return []
