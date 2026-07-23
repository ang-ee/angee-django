"""Pure Discord Gateway identity and message mapping rules.

The worker converts discord.py objects into ordinary mappings before crossing
this boundary. Keeping this module SDK-free lets console imports register the
backend and GraphQL mutation without importing the Gateway client.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from angee.messaging._wire import mapping, millis_to_utc, sequence, text
from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedThread, body_part

PLATFORM = "discord"

_USER_MESSAGE_TYPES = frozenset({0, 19, "0", "19", "default", "reply"})


@dataclass(frozen=True, slots=True)
class DiscordMediaFact:
    """One time-limited signed Discord CDN attachment."""

    url: str
    mime: str = "application/octet-stream"
    name: str = ""
    size: int = 0


def external_id(channel_id: object, message_id: object) -> str:
    """Scope Discord message snowflakes to their owning channel."""

    return f"{channel_id}/{message_id}"


def handle_for_author(author: Mapping[str, Any]) -> ParsedHandle | None:
    """Map one stable Discord user snowflake and mutable profile labels."""

    user_id = text(author.get("id"))
    if not user_id:
        return None
    username = text(author.get("username"))
    global_name = text(author.get("global_name"))
    return ParsedHandle(
        platform=PLATFORM,
        external_id=user_id,
        value=username or global_name or user_id,
        display_name=global_name or username,
    )


def media_fact(attachment: Mapping[str, Any]) -> DiscordMediaFact | None:
    """Return the signed CDN reference carried by one attachment."""

    url = text(attachment.get("url"))
    if not url:
        return None
    try:
        size = max(0, int(attachment.get("size") or 0))
    except (TypeError, ValueError):  # fmt: skip
        size = 0
    return DiscordMediaFact(
        url=url,
        mime=text(attachment.get("content_type")).lower() or "application/octet-stream",
        name=text(attachment.get("filename")),
        size=size,
    )


def parsed_message(message: Mapping[str, Any], *, own_id: object = "") -> ParsedMessage | None:
    """Map one supported Discord user message onto neutral messaging.

    Default messages and replies are ingested. Discord's join, pin, boost, call,
    thread-starter, and other system message types are deliberately skipped, as
    are embed-only and sticker-only events. An edit passes its current content
    through this same mapping.
    """

    message_type = message.get("type", 0)
    normalized_type = getattr(message_type, "value", message_type)
    if normalized_type not in _USER_MESSAGE_TYPES:
        return None

    channel = mapping(message.get("channel"))
    author = mapping(message.get("author"))
    channel_id = text((channel or {}).get("id") or message.get("channel_id"))
    message_id = text(message.get("id"))
    if channel is None or author is None or not channel_id or not message_id:
        return None

    author_id = text(author.get("id"))
    if not author_id:
        return None
    facts = tuple(
        fact
        for raw in sequence(message.get("attachments"))
        if isinstance(raw, Mapping) and (fact := media_fact(raw)) is not None
    )
    content = text(message.get("content"))
    if not content and not facts:
        return None

    guild = mapping(channel.get("guild"))
    guild_id = text((guild or {}).get("id") or message.get("guild_id"))
    guild_name = text((guild or {}).get("name"))
    channel_name = text(channel.get("name"))
    reference = mapping(message.get("message_reference"))
    reply_to = ""
    if reference is not None and (reference_id := text(reference.get("message_id"))):
        reference_channel_id = text(reference.get("channel_id")) or channel_id
        reply_to = external_id(reference_channel_id, reference_id)

    metadata: dict[str, Any] = {
        "discord_channel_id": channel_id,
        "discord_message_id": message_id,
        "discord_message_type": normalized_type,
    }
    if guild_id:
        metadata["discord_guild_id"] = guild_id
    if message.get("deleted"):
        metadata["discord_deleted"] = True
    if facts:
        metadata["_media_facts"] = facts

    return ParsedMessage(
        external_id=external_id(channel_id, message_id),
        platform=PLATFORM,
        direction="outbound" if author_id == text(own_id) else "inbound",
        sender=handle_for_author(author),
        sent_at=millis_to_utc(message.get("timestamp_ms")),
        in_reply_to=reply_to,
        thread=ParsedThread(
            external_id=channel_id,
            modality="group" if guild is not None or guild_id else "direct",
            title=channel_name or guild_name or channel_id,
        ),
        body=body_part(content),
        metadata=metadata,
    )
