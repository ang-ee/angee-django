"""WhatsApp identity + mapping rules — the one owner both ingest paths share.

Everything that decides *which row a WhatsApp message converges on* lives here,
consumed by the live session (:mod:`.client`) and the backup importer
(:mod:`.backup`) alike:

- **Bare JID** — ``user@server`` lowercased with device/agent suffixes stripped
  (``4917….3:12@s.whatsapp.net`` → ``4917…@s.whatsapp.net``). Live events carry
  device-qualified JIDs, a device backup carries bare ones; normalizing both
  sides is what lets them meet.
- **External id** — ``<bare chat JID>/<stanza id>``. Stanza ids are only unique
  per chat, so the chat scope is embedded (the ``ParsedMessage.external_id``
  contract). A stanza-less backup row falls back to its producer's
  ``fallback_id`` under the same chat scope (``<chat>/ios:<pk>``) — idempotent
  across re-imports, deliberately forfeiting live convergence (there is no wire
  id to converge on). Quoted-reply references compose the same form, so a reply
  threads to the row either path landed.
- **Handles** — ``platform="whatsapp"``, ``external_id`` = the bare JID (the
  stable identity), ``value`` = the E.164 phone when the JID user part is a
  phone number, else the bare JID.
- **Threads** — one per chat: ``ParsedThread(external_id=<bare chat JID>)``
  (the base namespaces the key), ``group``/``direct`` from the JID server,
  title from the chat's display name.
- **Media discipline** (IMAP's "mail is never dropped" mirror): a media item
  whose bytes could not be fetched lands as a text marker part instead of
  dropping the message or failing the batch.

Producers translate their wire shapes (neonize events, ChatStorage rows) into
the neutral :class:`ChatMessage`; :func:`parsed_message` is the single mapping
onto the messaging seam.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedPart, ParsedThread

PLATFORM = "whatsapp"
GROUP_SERVERS = ("g.us", "broadcast")
INDIVIDUAL_SERVER = "s.whatsapp.net"

DIRECTION_INBOUND = "inbound"
DIRECTION_OUTBOUND = "outbound"

INLINE_MEDIA_PREFIXES = ("image/", "video/", "audio/")


def bare_jid(jid: str) -> str:
    """Normalize a JID to its bare ``user@server`` identity, lowercased.

    Strips the multi-device qualifiers from the user part — ``:device`` and a
    numeric ``.agent`` — so a live event's ``4917….3:12@s.whatsapp.net`` and a
    backup's ``4917…@s.whatsapp.net`` name the same identity. Non-JID strings
    pass through lowercased, so a caller never crashes on vendor surprises.
    """

    jid = (jid or "").strip().lower()
    if "@" not in jid:
        return jid
    user, _, server = jid.partition("@")
    user = user.partition(":")[0]
    head, dot, tail = user.partition(".")
    if dot and head.isdigit() and tail.isdigit():
        user = head
    return f"{user}@{server}"


def phone_for_jid(jid: str) -> str:
    """Return the E.164 phone (``+digits``) behind an individual JID, or ``""``.

    Only ``s.whatsapp.net`` user parts are phone numbers; group ids, broadcast
    channels, and hidden-identity (``@lid``) JIDs have no derivable phone.
    """

    user, _, server = bare_jid(jid).partition("@")
    if server == INDIVIDUAL_SERVER and user.isdigit():
        return f"+{user}"
    return ""


def is_group_jid(jid: str) -> bool:
    """Return whether a chat JID names a group or broadcast conversation."""

    return bare_jid(jid).partition("@")[2] in GROUP_SERVERS


def handle_for_jid(jid: str, display_name: str = "") -> ParsedHandle:
    """Return the whatsapp handle for a JID — phone as the value when derivable."""

    bare = bare_jid(jid)
    return ParsedHandle(
        platform=PLATFORM,
        value=phone_for_jid(bare) or bare,
        display_name=display_name,
        external_id=bare,
    )


def external_id(chat_jid: str, stanza_id: str) -> str:
    """Compose the chat-scoped idempotency key both ingest paths converge on."""

    return f"{bare_jid(chat_jid)}/{stanza_id}"


@dataclass(frozen=True)
class MediaItem:
    """One media payload on a message; ``content=None`` means the fetch failed."""

    mime: str = "application/octet-stream"
    name: str = ""
    content: bytes | None = None


@dataclass(frozen=True)
class ChatMessage:
    """The neutral WhatsApp message shape both producers translate into.

    ``stanza_id`` is the wire message id; a producer with none (a backup row
    predating stanza ids) sets ``fallback_id`` to a source-stable local id
    (e.g. ``ios:<row pk>``) instead. ``group`` may be left ``None`` to derive
    from the chat JID's server. ``metadata`` carries the lossless envelope.
    """

    chat_jid: str
    stanza_id: str = ""
    fallback_id: str = ""
    chat_name: str = ""
    group: bool | None = None
    sender_jid: str = ""
    sender_name: str = ""
    from_me: bool = False
    timestamp: datetime | None = None
    text: str = ""
    quoted_stanza_id: str = ""
    media: tuple[MediaItem, ...] = ()
    metadata: dict = field(default_factory=dict)


def _media_part(item: MediaItem) -> ParsedPart:
    """Return the part for one media item — a marker part when bytes are missing."""

    if item.content is None:
        label = item.name or item.mime
        return ParsedPart(type="text/plain", role="body", text=f"[media unavailable: {label}]")
    inline = item.mime.startswith(INLINE_MEDIA_PREFIXES)
    return ParsedPart(
        type=item.mime,
        disposition="inline" if inline else "attachment",
        name=item.name,
        content=item.content,
    )


def _body(message: ChatMessage) -> ParsedPart | None:
    """Build the recursive body tree: bare text, one media part, or a mixed root."""

    parts: list[ParsedPart] = []
    if message.text:
        parts.append(ParsedPart(type="text/plain", role="body", text=message.text))
    parts.extend(_media_part(item) for item in message.media)
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return ParsedPart(type="multipart/mixed", children=tuple(parts))


def parsed_message(message: ChatMessage) -> ParsedMessage:
    """Map one :class:`ChatMessage` onto the neutral messaging seam."""

    chat = bare_jid(message.chat_jid)
    stanza = message.stanza_id or message.fallback_id
    group = message.group if message.group is not None else is_group_jid(chat)
    return ParsedMessage(
        external_id=external_id(chat, stanza),
        platform=PLATFORM,
        direction=DIRECTION_OUTBOUND if message.from_me else DIRECTION_INBOUND,
        sender=handle_for_jid(message.sender_jid, message.sender_name) if message.sender_jid else None,
        sent_at=message.timestamp,
        in_reply_to=external_id(chat, message.quoted_stanza_id) if message.quoted_stanza_id else "",
        thread=ParsedThread(
            external_id=chat,
            modality="group" if group else "direct",
            title=message.chat_name,
        ),
        body=_body(message),
        metadata={"chat_jid": chat, **message.metadata},
    )
