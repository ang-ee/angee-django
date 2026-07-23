"""Pure signal-cli receive-envelope identity and message mapping rules.

The worker hands this module ordinary dictionaries from signal-cli's JSON-RPC
``receive`` notifications. Keeping the translation SDK- and subprocess-free lets
the console import the backend declaration without entering the worker closure.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from angee.messaging._wire import mapping, millis_to_utc, sequence, text
from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedThread, body_part

PLATFORM = "signal"

_SKIPPED_ENVELOPE_KEYS = (
    "receiptMessage",
    "typingMessage",
    "storyMessage",
    "paymentMessage",
)
_SKIPPED_DATA_KEYS = (
    "reaction",
    "remoteDelete",
    "storyContext",
    "payment",
    "paymentNotification",
    "poll",
    "pollCreate",
    "pollVote",
    "pollTerminate",
)


@dataclass(frozen=True, slots=True)
class SignalMediaFact:
    """One attachment file signal-cli has already downloaded into the store."""

    id: str
    mime: str = "application/octet-stream"
    name: str = ""


def external_id(thread_key: object, source_uuid: object, timestamp: object) -> str:
    """Return Signal's chat-scoped sender/timestamp message identity."""

    return f"{thread_key}/{source_uuid}:{timestamp}"


def handle_for_envelope(envelope: Mapping[str, Any]) -> ParsedHandle | None:
    """Map one envelope's stable sender UUID and mutable profile labels."""

    source_uuid = text(envelope.get("sourceUuid"))
    if not source_uuid:
        return None
    return ParsedHandle(
        platform=PLATFORM,
        external_id=source_uuid,
        value=text(envelope.get("sourceNumber")) or source_uuid,
        display_name=text(envelope.get("sourceName")),
    )


def media_fact(attachment: Mapping[str, Any]) -> SignalMediaFact | None:
    """Return the store-file identity carried by one attachment descriptor."""

    attachment_id = text(attachment.get("id"))
    if not attachment_id:
        return None
    return SignalMediaFact(
        id=attachment_id,
        mime=text(attachment.get("contentType")).lower() or "application/octet-stream",
        name=text(attachment.get("filename")),
    )


def receive_envelope(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Return the bare envelope from one signal-cli receive notification."""

    params = mapping(record.get("params"))
    if params is None:
        return None
    if envelope := mapping(params.get("envelope")):
        return envelope
    result = mapping(params.get("result"))
    return mapping(result.get("envelope")) if result is not None else None


def parsed_message(envelope: Mapping[str, Any]) -> ParsedMessage | None:
    """Map a guaranteed-bare signal-cli envelope onto messaging.

    ``dataMessage`` is an inbound peer message. A
    ``syncMessage.sentMessage`` is the linked primary device echoing an outbound
    message. All other receive shapes are deliberately ignored in v1.
    """

    if any(envelope.get(key) is not None for key in _SKIPPED_ENVELOPE_KEYS):
        return None

    direction = "inbound"
    message = mapping(envelope.get("dataMessage"))
    if message is None:
        sync = mapping(envelope.get("syncMessage"))
        message = mapping(sync.get("sentMessage")) if sync is not None else None
        direction = "outbound"
    if message is None or any(message.get(key) is not None for key in _SKIPPED_DATA_KEYS):
        return None

    group = mapping(message.get("groupInfo"))
    group_id = text(group.get("groupId")) if group is not None else ""
    if group_id:
        thread_key = group_id
        modality = "group"
        title = text(group.get("groupName") or group.get("name")) if group is not None else ""
    else:
        peer_key = "sourceUuid" if direction == "inbound" else "destinationUuid"
        thread_key = text(envelope.get(peer_key) if direction == "inbound" else message.get(peer_key))
        modality = "direct"
        title = ""

    source_uuid = text(envelope.get("sourceUuid"))
    timestamp = message.get("timestamp", envelope.get("timestamp"))
    timestamp_key = text(timestamp)
    if not thread_key or not source_uuid or not timestamp_key:
        return None

    facts = tuple(
        fact
        for raw in sequence(message.get("attachments"))
        if isinstance(raw, Mapping) and (fact := media_fact(raw)) is not None
    )
    message_text = text(message.get("message"))
    if not message_text and not facts:
        return None

    quote = mapping(message.get("quote"))
    reply_to = ""
    if quote is not None:
        quote_author = text(quote.get("authorUuid"))
        quote_timestamp = text(quote.get("id"))
        if quote_author and quote_timestamp:
            reply_to = external_id(thread_key, quote_author, quote_timestamp)

    metadata: dict[str, Any] = {"signal_timestamp": timestamp_key}
    if facts:
        metadata["_media_facts"] = facts
    return ParsedMessage(
        external_id=external_id(thread_key, source_uuid, timestamp_key),
        platform=PLATFORM,
        direction=direction,
        sender=handle_for_envelope(envelope),
        sent_at=millis_to_utc(timestamp),
        in_reply_to=reply_to,
        thread=ParsedThread(
            external_id=thread_key,
            modality=modality,
            title=title,
        ),
        body=body_part(message_text),
        metadata=metadata,
    )
