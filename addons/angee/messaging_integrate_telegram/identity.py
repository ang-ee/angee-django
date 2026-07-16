"""Pure Telegram identity-only message mapping rules.

The worker translates Telethon objects at this boundary, but this module keeps
only duck-typed value access so console imports never load the vendor SDK.
Telegram message ids are chat-local; every ingest id embeds the chat id. Unlike
WhatsApp's DTO-carrying parser, this boundary maps directly to ``ParsedMessage``.
"""

from __future__ import annotations

from typing import Any

from angee.messaging.backends import MediaItem, ParsedHandle, ParsedMessage, ParsedThread, body_part

PLATFORM = "telegram"


def external_id(chat_id: object, message_id: object) -> str:
    """Return Telegram's chat-scoped message identity."""

    return f"{chat_id}/{message_id}"


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
    """Return direct/group/channel while keeping Telegram megagroups as groups."""

    if is_private:
        return "direct"
    if is_group:
        return "group"
    if is_channel:
        return "channel"
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
            MediaItem(
                mime=str(getattr(file, "mime_type", "") or "application/octet-stream"),
                name=str(getattr(file, "name", "") or ""),
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


def _display_name(peer: Any | None) -> str:
    """Return a user full name or chat title from a Telegram-shaped peer."""

    if peer is None:
        return ""
    if title := str(getattr(peer, "title", "") or "").strip():
        return title
    return " ".join(
        value for field in ("first_name", "last_name") if (value := str(getattr(peer, field, "") or "").strip())
    )
