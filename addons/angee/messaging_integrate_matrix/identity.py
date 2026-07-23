"""Pure Matrix room-event identity and message mapping rules.

The worker converts mautrix objects into ordinary mappings before crossing this
boundary. Keeping this module SDK-free lets the console import the backend and
GraphQL declarations without loading Matrix crypto or its native bindings.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from angee.messaging._wire import mapping, millis_to_utc, text
from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedThread, body_part

PLATFORM = "matrix"

_TEXT_MSGTYPES = frozenset({"m.text", "m.notice", "m.emote"})
_MEDIA_MSGTYPES = frozenset({"m.image", "m.file", "m.audio", "m.video"})


@dataclass(frozen=True, slots=True)
class MatrixMediaFact:
    """One Matrix content-repository object and optional encryption facts."""

    url: str
    mime: str = "application/octet-stream"
    name: str = ""
    key: str = ""
    hash: str = ""
    iv: str = ""

    @property
    def encrypted(self) -> bool:
        """Return whether this fact carries the complete encrypted-file tuple."""

        return bool(self.key and self.hash and self.iv)


def external_id(room_id: object, event_id: object) -> str:
    """Scope Matrix event ids to their owning room for messaging identity."""

    return f"{room_id}/{event_id}"


def handle_for_user(user_id: object, *, display_name: object = "") -> ParsedHandle | None:
    """Map one stable Matrix user id and mutable profile label."""

    stable_id = text(user_id)
    if not stable_id:
        return None
    return ParsedHandle(
        platform=PLATFORM,
        external_id=stable_id,
        value=stable_id,
        display_name=text(display_name),
    )


def media_fact(content: Mapping[str, Any]) -> MatrixMediaFact | None:
    """Return one plain or encrypted Matrix media reference."""

    encrypted = mapping(content.get("file"))
    source = encrypted if encrypted is not None else content
    url = text(source.get("url"))
    if not url:
        return None
    info = mapping(content.get("info")) or {}
    key_data = mapping(source.get("key")) or {}
    hashes = mapping(source.get("hashes")) or {}
    return MatrixMediaFact(
        url=url,
        mime=text(info.get("mimetype")).lower() or "application/octet-stream",
        name=text(content.get("filename") or content.get("body")),
        key=text(key_data.get("k")),
        hash=text(hashes.get("sha256")),
        iv=text(source.get("iv")),
    )


def parsed_message(
    event: Mapping[str, Any],
    *,
    room_id: object = "",
    room_name: object = "",
    own_user_id: object = "",
) -> ParsedMessage | None:
    """Map one v1-supported Matrix room message onto neutral messaging.

    State events, edits, reactions, redactions, locations, and unsupported
    message types are deliberately ignored. Encrypted events reach this function
    only after mautrix decrypts them into their original room-message shape.
    """

    if text(event.get("type")) != "m.room.message" or "state_key" in event:
        return None
    content = mapping(event.get("content"))
    if content is None:
        return None
    relation = mapping(content.get("m.relates_to"))
    if relation is not None and text(relation.get("rel_type")) == "m.replace":
        return None
    msgtype = text(content.get("msgtype"))
    if msgtype not in _TEXT_MSGTYPES | _MEDIA_MSGTYPES:
        return None

    resolved_room_id = text(room_id or event.get("room_id"))
    event_id = text(event.get("event_id"))
    sender_id = text(event.get("sender"))
    if not resolved_room_id or not event_id or not sender_id:
        return None

    fact = media_fact(content) if msgtype in _MEDIA_MSGTYPES else None
    message_text = text(content.get("body")) if msgtype in _TEXT_MSGTYPES else ""
    if not message_text and fact is None:
        return None

    reply_to = ""
    in_reply_to = mapping(relation.get("m.in_reply_to")) if relation is not None else None
    if in_reply_to is not None and (reply_event_id := text(in_reply_to.get("event_id"))):
        reply_to = external_id(resolved_room_id, reply_event_id)

    metadata: dict[str, Any] = {
        "matrix_event_id": event_id,
        "matrix_msgtype": msgtype,
    }
    if fact is not None:
        metadata["_media_facts"] = (fact,)
    return ParsedMessage(
        external_id=external_id(resolved_room_id, event_id),
        platform=PLATFORM,
        direction="outbound" if sender_id == text(own_user_id) else "inbound",
        sender=handle_for_user(sender_id, display_name=event.get("sender_display_name")),
        sent_at=millis_to_utc(event.get("origin_server_ts")),
        in_reply_to=reply_to,
        thread=ParsedThread(
            external_id=resolved_room_id,
            modality="group",
            title=text(room_name) or resolved_room_id,
        ),
        body=body_part(message_text),
        metadata=metadata,
    )
