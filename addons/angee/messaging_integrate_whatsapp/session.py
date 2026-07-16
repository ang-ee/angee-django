"""Live WhatsApp session — the ``neonize`` binding, worker-only."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neonize.client import NewClient
from neonize.events import (
    ConnectedEv,
    HistorySyncEv,
    LoggedOutEv,
    MessageEv,
    PairStatusEv,
)

from angee.integrate.live import STOP_JOIN_SECONDS
from angee.messaging.session import LiveChannelSession
from angee.messaging_integrate_whatsapp.parser import ChatMessage

logger = logging.getLogger(__name__)


def _jid_str(jid: Any) -> str:
    """Return ``user@server`` from a JID proto."""

    user = getattr(jid, "User", "") or ""
    server = getattr(jid, "Server", "") or ""
    return f"{user}@{server}" if user or server else ""


def _timestamp(value: Any) -> datetime | None:
    """Convert a wire timestamp (unix seconds; tolerate milliseconds) to UTC."""

    try:
        seconds = int(value)
    except TypeError, ValueError:
        return None
    if seconds <= 0:
        return None
    if seconds > 10**12:
        seconds //= 1000
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


@dataclass(frozen=True)
class _MediaFact:
    """Which media payload a message carries; bytes are fetched on the task thread."""

    mime: str
    name: str = ""


_MEDIA_FIELDS = ("imageMessage", "videoMessage", "audioMessage", "stickerMessage", "documentMessage")


def _content_facts(content: Any) -> tuple[str, str, tuple[_MediaFact, ...]]:
    """Read text, quoted stanza id, and media facts off a wire message payload."""

    text = getattr(content, "conversation", "") or ""
    extended = getattr(content, "extendedTextMessage", None)
    if not text and extended is not None:
        text = getattr(extended, "text", "") or ""
    quoted = ""
    if extended is not None:
        quoted = getattr(getattr(extended, "contextInfo", None), "stanzaID", "") or ""
    media: list[_MediaFact] = []
    for field in _MEDIA_FIELDS:
        node = getattr(content, field, None)
        mime = getattr(node, "mimetype", "") or "" if node is not None else ""
        if not mime:
            continue
        media.append(_MediaFact(mime=mime, name=getattr(node, "fileName", "") or ""))
        text = text or (getattr(node, "caption", "") or "")
        quoted = quoted or (getattr(getattr(node, "contextInfo", None), "stanzaID", "") or "")
    return text, quoted, tuple(media)


class WhatsAppSession(LiveChannelSession):
    """One WhatsApp connection: vendor callbacks translate and enqueue only."""

    client_class: type[Any] = NewClient

    def _connect(self) -> None:
        """Run the blocking vendor connect; unwound by ``client.stop()``."""

        try:
            self.client.connect()
        except Exception:
            logger.exception("WhatsApp connection for channel %s crashed.", self.bridge.sqid)
        finally:
            self.events.put(("disconnected", None))

    def _shutdown(self, connection: threading.Thread) -> bool:
        """Cancel the vendor connection; report whether the Go call unwound."""

        try:
            self.client.stop()
        except Exception:
            logger.exception("Stopping the WhatsApp client for channel %s failed.", self.bridge.sqid)
        connection.join(timeout=STOP_JOIN_SECONDS)
        return not connection.is_alive()

    def _download(self, payload: Any) -> bytes | None:
        """Fetch one message's media bytes; ``None`` lands the marker part."""

        if payload is None:
            return None
        try:
            return self.client.download_any(payload)
        except Exception:
            logger.info("WhatsApp media download failed for channel %s.", self.bridge.sqid)
            return None

    def _build_client(self, store: Path) -> Any:
        """Instantiate the vendor client against the session store and wire events."""

        client = self.client_class(str(store))
        client.event(ConnectedEv)(self._on_connected)
        client.event(PairStatusEv)(self._on_pair_status)
        client.event(LoggedOutEv)(self._on_logged_out)
        client.event(MessageEv)(self._on_message)
        client.event(HistorySyncEv)(self._on_history)
        client.event.qr(self._on_qr)
        return client

    def _on_qr(self, _client: Any, payload: bytes) -> None:
        self.events.put(("qr", payload))

    def _on_connected(self, client: Any, _event: Any) -> None:
        jid = ""
        me = getattr(client, "me", None)
        if me is not None:
            jid = _jid_str(getattr(me, "JID", None))
        self.events.put(("paired", jid))

    def _on_pair_status(self, _client: Any, event: Any) -> None:
        self.events.put(("paired", _jid_str(getattr(event, "ID", None))))

    def _on_logged_out(self, _client: Any, _event: Any) -> None:
        self.events.put(("logged_out", None))

    def _on_message(self, _client: Any, event: Any) -> None:
        info = event.Info
        source = info.MessageSource
        content = event.Message
        text, quoted, facts = _content_facts(content)
        message = ChatMessage(
            chat_jid=_jid_str(source.Chat),
            stanza_id=str(info.ID or ""),
            sender_jid=_jid_str(source.Sender),
            sender_name=str(getattr(info, "Pushname", "") or ""),
            from_me=bool(source.IsFromMe),
            timestamp=_timestamp(getattr(info, "Timestamp", 0)),
            text=text,
            quoted_stanza_id=quoted,
            metadata={"_media_facts": facts} if facts else {},
        )
        if message.stanza_id and (message.text or facts):
            self.events.put(("messages", [(message, content if facts else None)]))

    def _on_history(self, _client: Any, event: Any) -> None:
        batch: list[tuple[ChatMessage, Any]] = []
        for conversation in getattr(getattr(event, "Data", None), "conversations", ()) or ():
            chat_jid = str(getattr(conversation, "ID", "") or "")
            for item in getattr(conversation, "messages", ()) or ():
                web_message = getattr(item, "message", None)
                if web_message is None:
                    continue
                key = getattr(web_message, "key", None)
                content = getattr(web_message, "message", None)
                if key is None or content is None:
                    continue
                text, quoted, facts = _content_facts(content)
                if not (text or facts):
                    continue
                stanza_id = str(getattr(key, "ID", "") or "")
                if not stanza_id:
                    continue
                from_me = bool(getattr(key, "fromMe", False))
                sender = str(getattr(key, "participant", "") or "")
                if from_me:
                    sender = self.own_id
                elif not sender:
                    sender = chat_jid
                message = ChatMessage(
                    chat_jid=chat_jid,
                    stanza_id=stanza_id,
                    sender_jid=sender,
                    sender_name=str(getattr(web_message, "pushName", "") or ""),
                    from_me=from_me,
                    timestamp=_timestamp(getattr(web_message, "messageTimestamp", 0)),
                    text=text,
                    quoted_stanza_id=quoted,
                    metadata={"_media_facts": facts, "history": True} if facts else {"history": True},
                )
                batch.append((message, content if facts else None))
        if batch:
            self.events.put(("messages", batch))
