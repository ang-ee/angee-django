"""iMessage/SMS DTO-carrying parser mapping the Apple store onto the neutral seam.

Identity is a **phone (E.164) or email**, not a JID — Apple's ``handle.id`` is the
reachable address itself, so there is no bare-JID gymnastics and no ``own_jid``:
``message.is_from_me`` alone gives direction. Apple message GUIDs are globally
unique, so the idempotency key is just the guid (no chat scoping). The parser owns
the DTO also produced by :mod:`.store`, so the shape stays in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from angee.messaging.backends import MediaItem, ParsedHandle, ParsedMessage, ParsedThread, body_part
from angee.messaging_integrate_imessage.lines import normalize_line

PLATFORM = "imessage"

DIRECTION_INBOUND = "inbound"
DIRECTION_OUTBOUND = "outbound"


def handle_for_value(value: str, name: str = "") -> ParsedHandle:
    """Return the iMessage handle for a phone/email — the address is the identity.

    ``value`` is the raw reachable address (an E.164 phone or an email); the
    stable ``external_id`` lowercases it so a mixed-case email address still
    converges on one handle (an E.164 phone has no case to fold).
    """

    address = str(value or "").strip()
    return ParsedHandle(
        platform=PLATFORM,
        value=address,
        display_name=name,
        external_id=address.lower(),
    )


def external_id(message_guid: str, fallback_id: str = "") -> str:
    """Return the globally-unique idempotency key for one message.

    Apple message GUIDs are globally unique, so — unlike WhatsApp's chat-scoped
    stanza ids — the guid alone is the ingest key; ``fallback_id`` (``ios:<rowid>``)
    covers a row whose guid is somehow absent.
    """

    return message_guid or fallback_id


@dataclass(frozen=True)
class ChatMessage:
    """The iMessage/SMS message DTO the backup store produces for ingest."""

    chat_guid: str
    message_guid: str = ""
    fallback_id: str = ""
    chat_name: str = ""
    group: bool | None = None
    sender_value: str = ""
    sender_name: str = ""
    from_me: bool = False
    timestamp: datetime | None = None
    text: str = ""
    service: str = ""
    media: tuple[MediaItem, ...] = ()
    line_raw: str = ""
    account: str = ""


def parsed_message(message: ChatMessage) -> ParsedMessage:
    """Map one :class:`ChatMessage` onto the neutral messaging seam.

    Direction is outbound iff ``from_me``; an inbound row's ``handle.id`` names
    the sender (an outbound row has none). The thread is the Apple chat guid; the
    reader classifies group vs direct from the chat's ``style``/``room_name`` and
    passes it through, defaulting to direct when neither column is present. The
    handling local line is carried in the metadata both normalized (``line``) and
    raw (``line_raw``) so even a catch-all message keeps its original
    ``destination_caller_id`` for later resolution; ``account`` is stashed
    alongside. This is metadata only — it does not touch identity or threading.
    """

    group = bool(message.group)
    sender = (
        handle_for_value(message.sender_value, message.sender_name)
        if not message.from_me and message.sender_value
        else None
    )
    return ParsedMessage(
        external_id=external_id(message.message_guid, message.fallback_id),
        platform=PLATFORM,
        direction=DIRECTION_OUTBOUND if message.from_me else DIRECTION_INBOUND,
        sender=sender,
        sent_at=message.timestamp,
        thread=ParsedThread(
            external_id=message.chat_guid,
            modality="group" if group else "direct",
            title=message.chat_name,
        ),
        body=body_part(message.text, message.media),
        metadata={
            "service": message.service,
            "chat_guid": message.chat_guid,
            "line": normalize_line(message.line_raw) or "",
            "line_raw": message.line_raw,
            "account": message.account,
        },
    )
