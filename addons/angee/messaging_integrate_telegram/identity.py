"""Pure Telegram identity-only message mapping rules.

Live Telethon objects and Telegram Desktop export dictionaries meet at this
boundary, while console imports remain vendor-SDK-free. Telegram message ids
are chat-local; every ingest id embeds the same Telethon-marked chat id whether
the message arrived live or from a takeout. Unlike WhatsApp's DTO-carrying
parser, this boundary maps both inputs directly to ``ParsedMessage``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Literal

from angee.messaging.backends import MediaItem, ParsedHandle, ParsedMessage, ParsedThread, body_part

PLATFORM = "telegram"

ExportPeerKind = Literal["user", "chat", "channel"]


@dataclass(frozen=True)
class _ExportChatKind:
    """SDK-free facts owned by one Telegram Desktop chat vocabulary token."""

    peer_kind: ExportPeerKind
    is_private: bool = False
    is_group: bool = False
    is_channel: bool = False


_EXPORT_CHAT_KINDS = {
    "bot_chat": _ExportChatKind("user", is_private=True),
    "personal_chat": _ExportChatKind("user", is_private=True),
    "saved_messages": _ExportChatKind("user", is_private=True),
    "verification_codes": _ExportChatKind("user", is_private=True),
    "private_group": _ExportChatKind("chat", is_group=True),
    "private_supergroup": _ExportChatKind("channel", is_group=True, is_channel=True),
    "public_supergroup": _ExportChatKind("channel", is_group=True, is_channel=True),
    "private_channel": _ExportChatKind("channel", is_channel=True),
    "public_channel": _ExportChatKind("channel", is_channel=True),
}


def external_id(chat_id: object, message_id: object) -> str:
    """Return Telegram's chat-scoped message identity."""

    return f"{chat_id}/{message_id}"


def export_peer_kind(chat_type: object) -> ExportPeerKind:
    """Return the Telethon peer kind owned by one Desktop chat type token.

    This console-safe module owns only Telegram export vocabulary. The worker
    constructs the corresponding vendor ``Peer`` object and asks Telethon to
    mark its id; numeric marking rules never live here.
    """

    return _export_chat_kind(chat_type).peer_kind


def media_fact(mime: object = "", name: object = "") -> MediaItem:
    """Return the canonical media identity used by live and export adapters."""

    return MediaItem(
        mime=str(mime or "").strip().lower() or "application/octet-stream",
        name=str(name or "").strip(),
    )


def account_label(user_id: object, *, phone: object = "", username: object = "") -> str:
    """Return the preferred phone, username, or stable Telegram user id label."""

    clean_phone = str(phone or "").strip()
    if clean_phone:
        return clean_phone if clean_phone.startswith("+") else f"+{clean_phone}"
    clean_username = str(username or "").strip().lstrip("@")
    if clean_username:
        return f"@{clean_username}"
    return str(user_id or "").strip()


def handle_for_peer(peer: Any | None, *, fallback_id: object = "") -> ParsedHandle:
    """Map a Telegram peer or user onto one stable messaging handle."""

    peer_id = str(getattr(peer, "id", None) or fallback_id or "").strip()
    phone = getattr(peer, "phone", "") if peer is not None else ""
    username = getattr(peer, "username", "") if peer is not None else ""
    display_name = _display_name(peer)
    return ParsedHandle(
        platform=PLATFORM,
        value=account_label(peer_id, phone=phone, username=username),
        display_name=display_name,
        external_id=peer_id,
    )


def thread_modality(*, is_private: bool, is_group: bool, is_channel: bool) -> str:
    """Return a ``Thread.Modality`` value, keeping Telegram megagroups as groups.

    Telegram's chat types are not the thread vocabulary: a broadcast channel is a
    one-to-many feed, whose structural shape messaging names ``public_thread``.
    Returning Telegram's own ``channel`` noun rejects the whole ingest — the
    modality column is a real enum — so the mapping onto messaging's vocabulary
    lands here, at the adapter that knows both.
    """

    if is_private:
        return "direct"
    if is_group:
        return "group"
    if is_channel:
        return "public_thread"
    return "group"


def parsed_message(
    message: Any,
    *,
    chat_id: object,
    sender_id: object = "",
    sender: Any | None = None,
    chat: Any | None = None,
    is_private: bool,
    is_group: bool,
    is_channel: bool,
) -> ParsedMessage:
    """Map one Telethon-shaped message directly onto the neutral ingest seam."""

    chat_key = str(chat_id)
    message_id = getattr(message, "id", "")
    text = str(getattr(message, "raw_text", None) or getattr(message, "message", None) or "")
    reply_id = getattr(message, "reply_to_msg_id", None)
    metadata: dict[str, Any] = {
        "chat_id": chat_key,
        "message_id": message_id,
    }
    if getattr(message, "media", None) is not None:
        file = getattr(message, "file", None)
        metadata["_media_facts"] = (
            media_fact(
                getattr(file, "mime_type", ""),
                getattr(file, "name", ""),
            ),
        )
    sender_key = str(sender_id or getattr(sender, "id", "") or "").strip()
    return ParsedMessage(
        external_id=external_id(chat_key, message_id),
        platform=PLATFORM,
        direction="outbound" if bool(getattr(message, "out", False)) else "inbound",
        sender=handle_for_peer(sender, fallback_id=sender_key) if sender_key else None,
        sent_at=getattr(message, "date", None),
        in_reply_to=external_id(chat_key, reply_id) if reply_id else "",
        thread=ParsedThread(
            external_id=chat_key,
            modality=thread_modality(
                is_private=is_private,
                is_group=is_group,
                is_channel=is_channel,
            ),
            title=_display_name(chat),
        ),
        body=body_part(text),
        metadata=metadata,
    )


def parsed_export_message(
    chat: Mapping[str, Any],
    message: Mapping[str, Any],
    *,
    marked_chat_id: object,
    own_id: object = "",
    media: tuple[MediaItem, ...] = (),
) -> ParsedMessage:
    """Adapt one Telegram Desktop JSON message through the live identity mapper.

    Desktop currently emits chat ``id/name/type`` and message
    ``id/type/date/from/from_id/text`` fields; ``date_unixtime`` and
    ``reply_to_message_id`` are preferred when present. Rich ``text`` arrays
    are flattened without interpreting entity style. ``marked_chat_id`` comes
    from the worker's Telethon owner call, exactly like ``event.chat_id`` on the
    live path. Unknown chat types fail closed before neutral adaptation.
    """

    chat_type = str(chat.get("type") or "").strip()
    chat_kind = _export_chat_kind(chat_type)
    if marked_chat_id is None or str(marked_chat_id).strip() == "":
        raise ValueError("Telegram export marked chat id is required.")
    sender_key = _export_peer_id(message.get("from_id"))
    sender_name = str(message.get("from") or "").strip()
    sender = SimpleNamespace(id=sender_key, title=sender_name) if sender_key else None
    own_key = _export_peer_id(own_id)
    wire = SimpleNamespace(
        id=message.get("id", ""),
        raw_text=_export_text(message.get("text")),
        message="",
        out=bool(own_key and sender_key == own_key),
        date=_export_date(message),
        reply_to_msg_id=message.get("reply_to_message_id"),
        media=None,
        file=None,
    )
    parsed = parsed_message(
        wire,
        chat_id=marked_chat_id,
        sender_id=sender_key,
        sender=sender,
        chat=SimpleNamespace(id=chat.get("id"), title=str(chat.get("name") or "")),
        is_private=chat_kind.is_private,
        is_group=chat_kind.is_group,
        is_channel=chat_kind.is_channel,
    )
    if not media:
        return parsed
    metadata = dict(parsed.metadata)
    metadata["_media_facts"] = tuple(media_fact(item.mime, item.name) for item in media)
    return replace(parsed, metadata=metadata)


def _export_chat_kind(chat_type: object) -> _ExportChatKind:
    """Return SDK-free facts for a supported Desktop chat vocabulary token."""

    kind = str(chat_type or "").strip()
    try:
        return _EXPORT_CHAT_KINDS[kind]
    except KeyError as error:
        raise ValueError(f"Telegram export chat type {kind!r} is not supported.") from error


def _export_peer_id(value: object) -> str:
    """Return the bare numeric id from Desktop's ``user123``-style peer id."""

    peer_id = str(value or "").strip()
    for prefix in ("user", "channel", "chat"):
        if peer_id.startswith(prefix):
            peer_id = peer_id[len(prefix) :]
            break
    return peer_id if peer_id.isdigit() else ""


def _export_text(value: object) -> str:
    """Flatten Desktop's string-or-entity-list text representation."""

    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for part in value:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, Mapping):
            parts.append(str(part.get("text") or ""))
    return "".join(parts)


def _export_date(message: Mapping[str, Any]) -> datetime | None:
    """Return an aware instant, preferring Desktop's lossless Unix timestamp."""

    raw_timestamp = message.get("date_unixtime")
    if raw_timestamp is not None and raw_timestamp != "":
        try:
            return datetime.fromtimestamp(int(str(raw_timestamp)), tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            pass
    raw_date = str(message.get("date") or "").strip()
    if not raw_date:
        return None
    try:
        parsed = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _display_name(peer: Any | None) -> str:
    """Return a user full name or chat title from a Telegram-shaped peer."""

    if peer is None:
        return ""
    if title := str(getattr(peer, "title", "") or "").strip():
        return title
    return " ".join(
        value for field in ("first_name", "last_name") if (value := str(getattr(peer, field, "") or "").strip())
    )
