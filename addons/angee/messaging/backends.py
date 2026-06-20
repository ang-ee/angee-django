"""Channel backend contract — ingest messages from an external source into messaging.

A :class:`~angee.messaging.models.Channel` (an ``integrate.Integration`` child +
``Bridge``) selects one ``ChannelBackend`` by registry key. The backend does the
per-source *transport* + *parse* — ``fetch_messages`` returns neutral
:class:`ParsedMessage` rows (a recursive :class:`ParsedPart` body, sender/recipient
:class:`ParsedHandle`\\s, RFC-5322 threading hints). The *map* onto messaging —
thread resolution, the idempotent ``(platform, external_id)`` upsert, the Part /
Fragment tree, and the quotation graph — is owned by ``Message.objects.ingest`` +
the managers, so every source shares one write path. ``messaging_integrate_imap``
contributes the ``imap`` backend; the ``manual`` null-object keeps the registry
non-empty when no source is installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

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

    ``external_id`` is the per-platform idempotency key (e.g. the RFC-5322
    Message-ID). ``in_reply_to`` / ``references`` carry the threading hints the map
    resolves; ``body`` is the recursive part tree.
    """

    external_id: str
    platform: str
    direction: str = "inbound"
    subject: str = ""
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

    def fetch_messages(self) -> list[ParsedMessage]:
        """Return the new messages since the bridge cursor as neutral dataclasses."""

        raise NotImplementedError("ChannelBackend subclasses must implement fetch_messages().")


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
