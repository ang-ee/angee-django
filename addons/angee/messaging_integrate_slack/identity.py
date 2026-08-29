"""Console-safe Slack JSON to neutral messaging identity translation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from angee.messaging.backends import MediaItem, ParsedHandle, ParsedMessage, ParsedThread, body_part

PLATFORM = "slack"

_ALLOWED_SUBTYPES = frozenset({"", "me_message", "thread_broadcast", "file_share"})
_NOISE_SUBTYPES = frozenset(
    {
        "channel_join",
        "channel_leave",
        "channel_topic",
        "channel_purpose",
        "channel_name",
    }
)


def response_data(response: Any) -> Mapping[str, Any]:
    """Return the JSON mapping from a SlackResponse or a test mapping."""

    data = getattr(response, "data", response)
    return data if isinstance(data, Mapping) else {}


def parsed_message(
    raw: Mapping[str, Any],
    *,
    conversation: Mapping[str, Any],
    team_id: str,
    own_id: str,
    users: Mapping[str, Mapping[str, Any]],
    media: tuple[MediaItem, ...] = (),
) -> ParsedMessage | None:
    """Map one Slack history/reply item onto a flat conversation thread."""

    current, event_subtype = _current_message(raw)
    subtype = str(current.get("subtype") or "")
    if event_subtype != "message_changed" and (subtype in _NOISE_SUBTYPES or subtype not in _ALLOWED_SUBTYPES):
        return None

    channel_id = str(conversation.get("id") or "")
    timestamp = str(current.get("ts") or raw.get("ts") or "")
    if not channel_id or not timestamp:
        return None
    text = str(current.get("text") or "")
    body = body_part(text, media)
    has_files = any(isinstance(item, Mapping) for item in current.get("files") or ())
    if body is None and not has_files:
        return None

    sender_id = str(current.get("user") or current.get("bot_id") or "")
    sender = handle_for_user(
        sender_id,
        team_id=team_id,
        users=users,
        fallback=current,
    )
    thread_ts = str(current.get("thread_ts") or "")
    metadata: dict[str, Any] = {
        "slack_channel_id": channel_id,
        "slack_ts": timestamp,
    }
    if thread_ts:
        metadata["thread_ts"] = thread_ts
    if event_subtype or subtype:
        metadata["subtype"] = event_subtype or subtype
    if isinstance(current.get("edited"), Mapping):
        edited = current["edited"]
        metadata["edited"] = {key: str(edited.get(key) or "") for key in ("user", "ts") if edited.get(key)}

    return ParsedMessage(
        external_id=f"{channel_id}/{timestamp}",
        platform=PLATFORM,
        direction="outbound" if sender_id and sender_id == own_id else "inbound",
        sender=sender,
        sent_at=_sent_at(timestamp),
        thread=ParsedThread(
            external_id=channel_id,
            modality=_modality(conversation),
            title=_conversation_title(conversation, users),
        ),
        body=body,
        metadata=metadata,
    )


def handle_for_user(
    user_id: str,
    *,
    team_id: str,
    users: Mapping[str, Mapping[str, Any]],
    fallback: Mapping[str, Any] | None = None,
) -> ParsedHandle | None:
    """Return a workspace-scoped Slack handle with users-list display copy."""

    if not user_id or not team_id:
        return None
    user = users.get(user_id) or {}
    raw_profile = user.get("profile")
    profile: Mapping[str, Any] = raw_profile if isinstance(raw_profile, Mapping) else {}
    fallback_values: Mapping[str, Any] = fallback or {}
    fallback_profile = fallback_values.get("bot_profile")
    if not isinstance(fallback_profile, Mapping):
        fallback_profile = {}
    display_name = str(
        profile.get("display_name")
        or profile.get("real_name")
        or user.get("real_name")
        or user.get("name")
        or fallback_values.get("username")
        or fallback_profile.get("name")
        or user_id
    )
    username = str(user.get("name") or "").strip()
    return ParsedHandle(
        platform=PLATFORM,
        external_id=f"{team_id}:{user_id}",
        value=f"@{username}" if username else user_id,
        display_name=display_name,
    )


def _current_message(raw: Mapping[str, Any]) -> tuple[Mapping[str, Any], str]:
    """Unwrap a future live-layer edit event; polling already returns current text."""

    subtype = str(raw.get("subtype") or "")
    current = raw.get("message")
    if subtype == "message_changed" and isinstance(current, Mapping):
        return current, subtype
    return raw, ""


def _modality(conversation: Mapping[str, Any]) -> str:
    return "direct" if conversation.get("is_im") or conversation.get("is_mpim") else "group"


def _conversation_title(
    conversation: Mapping[str, Any],
    users: Mapping[str, Mapping[str, Any]],
) -> str:
    name = str(conversation.get("name_normalized") or conversation.get("name") or "")
    if name:
        return name
    peer_id = str(conversation.get("user") or "")
    peer = users.get(peer_id) or {}
    raw_profile = peer.get("profile")
    profile: Mapping[str, Any] = raw_profile if isinstance(raw_profile, Mapping) else {}
    return str(
        profile.get("display_name") or profile.get("real_name") or peer.get("real_name") or peer.get("name") or ""
    )


def _sent_at(timestamp: str) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(timestamp), tz=UTC)
    except OSError, OverflowError, TypeError, ValueError:
        return None
