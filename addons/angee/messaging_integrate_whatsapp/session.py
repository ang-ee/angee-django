"""Live WhatsApp session — the ``neonize`` (whatsmeow) binding, worker-only.

One :class:`WhatsAppSession` owns one whatsmeow connection for the life of a
``whatsapp.run_session`` task. The Go client's callbacks fire on its own
threads, so they only translate wire protos into the neutral
:class:`~.parser.ChatMessage` shape and enqueue; the task thread — the one
holding the bridge advisory lock and its DB connection — drains the queue,
downloads media, and writes through ``Message.objects.ingest``. Pairing state
(the :class:`~.client.PairingState` vocabulary) streams out as
``sync_progress.details.pairing`` via the bridge progress reporter, which is
what the ``channelChanged`` subscription broadcasts.

Loop discipline for a long-lived task: the drain wakes at least every
``WAKE_SECONDS`` and (a) re-reads the persisted desired-state so a cooperative
stop is bounded, (b) checks the worker's shutdown event so a warm SIGTERM
completes within one wake, and (c) verifies the advisory lock is still held —
a DB reconnect drops the session-scoped lock, and exiting immediately lets the
reconciler restart cleanly instead of racing a duplicate session against the
same store. The wake stays shorter than the reconciler tick so that overlap
window is bounded. A large history batch is drained in bounded chunks so those
checks are never starved behind one payload. The session store under
``ANGEE_DATA_DIR`` is never deleted on transient errors — only an explicit
pairing reset wipes it.

Session-level Postgres advisory locks are per-connection: this module assumes a
direct connection or session pooling, not pgbouncer transaction pooling (which
would silently drop the lock the liveness check relies on).

This module imports ``neonize`` (and its bundled Go library) and ``qrcode`` at
top, so only the dedicated ``whatsapp`` queue worker — which imports it through
``tasks.py`` — ever loads them; the console/web process imports only
:mod:`.client`. The proto readers are attribute-only, which also lets the tests
drive the session with a plain fake client.
"""

from __future__ import annotations

import base64
import logging
import queue
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import qrcode
from django.apps import apps
from neonize.client import NewClient
from neonize.events import ConnectedEv, HistorySyncEv, LoggedOutEv, MessageEv, PairStatusEv

from angee.integrate.locks import bridge_is_locked
from angee.integrate.sync import BridgeProgressReporter
from angee.messaging_integrate_whatsapp.client import (
    STOP_JOIN_SECONDS,
    WAKE_SECONDS,
    PairingState,
    SessionLoggedOut,
    session_store_path,
)
from angee.messaging_integrate_whatsapp.parser import (
    ChatMessage,
    MediaItem,
    bare_jid,
    parsed_message,
    phone_for_jid,
)

logger = logging.getLogger(__name__)

INGEST_CHUNK = 100
"""Messages ingested per drain slice, so a large history payload never blocks the
cooperative-stop / shutdown / lock checks longer than one chunk's work."""


def _qr_data_uri(payload: bytes) -> str:
    """Render the pairing QR payload to a PNG data URI the dialog can ``<img>``."""

    image = qrcode.make(payload.decode("utf-8", errors="replace"))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _jid_str(jid: Any) -> str:
    """Return ``user@server`` from a JID proto (bare-JID normalization follows)."""

    user = getattr(jid, "User", "") or ""
    server = getattr(jid, "Server", "") or ""
    return f"{user}@{server}" if user or server else ""


def _timestamp(value: Any) -> datetime | None:
    """Convert a wire timestamp (unix seconds; tolerate milliseconds) to UTC."""

    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    if seconds > 10**12:  # a producer that sent milliseconds
        seconds //= 1000
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


@dataclass(frozen=True)
class _MediaFact:
    """Which media payload a message carries; bytes are fetched on the task thread."""

    mime: str
    name: str = ""


_MEDIA_FIELDS = ("imageMessage", "videoMessage", "audioMessage", "stickerMessage", "documentMessage")


def _content_facts(content: Any) -> tuple[str, str, tuple[_MediaFact, ...]]:
    """Read (text, quoted stanza id, media facts) off a wire ``Message`` payload.

    Attribute-only with empty-string defaults: a proto's absent submessage reads
    as empty fields, and a test fake mirrors the same shape with plain objects.
    """

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


class WhatsAppSession:
    """One live connection: pairing, event drain, ingest, cooperative stop.

    ``client_class`` is the test seam — it defaults to ``neonize``'s
    ``NewClient``. ``run`` returns the final :class:`~.client.PairingState` on a
    cooperative stop and raises :class:`~.client.SessionLoggedOut` when the phone
    unlinked the device.
    """

    client_class: type[Any] = NewClient

    def __init__(self, channel: Any, *, reporter: BridgeProgressReporter, stop_event: threading.Event) -> None:
        self.channel = channel
        self.reporter = reporter
        self.stop_event = stop_event
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.client: Any = None
        self.pairing = PairingState.STARTING
        self.own_jid = str(self.channel.subscription_state.get("own_jid") or "")
        self.landed = 0

    # -- lifecycle ---------------------------------------------------------

    def run(self) -> str:
        """Connect and drain events until stopped, shut down, logged out, or unlocked."""

        store = session_store_path(self.channel)
        store.mkdir(parents=True, exist_ok=True)
        self.client = self._build_client(store / "session.db")
        connection = threading.Thread(
            target=self._connect,
            name=f"whatsapp-{self.channel.sqid}",
            daemon=True,
        )
        self._report(PairingState.STARTING)
        connection.start()
        try:
            while True:
                if not self._drain_once():
                    break
                if not connection.is_alive():
                    raise ConnectionError("WhatsApp connection ended unexpectedly.")
        finally:
            self._shutdown(connection)
        if self.pairing == PairingState.LOGGED_OUT:
            raise SessionLoggedOut("The linked phone removed this device.")
        return self.pairing

    def _connect(self) -> None:
        """Run the blocking vendor connect; unwound by ``client.stop()``."""

        try:
            self.client.connect()
        except Exception:
            logger.exception("WhatsApp connection for channel %s crashed.", self.channel.sqid)
        finally:
            self.events.put(("disconnected", None))

    def _shutdown(self, connection: threading.Thread) -> None:
        """Cancel the vendor connection and wait for the Go call to unwind."""

        try:
            self.client.stop()
        except Exception:  # noqa: BLE001 - best-effort teardown of a foreign runtime
            logger.exception("Stopping the WhatsApp client for channel %s failed.", self.channel.sqid)
        connection.join(timeout=STOP_JOIN_SECONDS)

    def _drain_once(self) -> bool:
        """Handle queued events for up to one wake; return whether to keep running."""

        try:
            kind, payload = self.events.get(timeout=WAKE_SECONDS)
        except queue.Empty:
            return self._still_wanted()
        if kind == "qr":
            self.pairing = PairingState.AWAITING_SCAN
            self._report(PairingState.AWAITING_SCAN, qr=_qr_data_uri(payload))
        elif kind == "paired":
            self._mark_paired(payload)
        elif kind == "logged_out":
            self.pairing = PairingState.LOGGED_OUT
            return False
        elif kind == "disconnected":
            return False
        elif kind == "messages":
            return self._ingest(payload)
        return self._still_wanted()

    def _still_wanted(self) -> bool:
        """The bounded wake checks: shutdown event, persisted desire, lock held."""

        if self.stop_event.is_set():
            self.pairing = PairingState.STOPPED if self.pairing != PairingState.PAIRED else self.pairing
            return False
        self.channel.refresh_from_db(fields=["subscription_state"])
        if self.channel.subscription_state.get("desired") != self.channel.LiveState.LIVE:
            self.pairing = PairingState.STOPPED
            return False
        if not bridge_is_locked(self.channel):
            logger.warning(
                "WhatsApp session for channel %s lost its advisory lock; exiting for a clean restart.",
                self.channel.sqid,
            )
            return False
        return True

    # -- state reports -----------------------------------------------------

    def _report(self, state: str, **pairing: Any) -> None:
        """Persist pairing + progress; each save streams over ``channelChanged``."""

        stage = (
            self.channel.SyncStage.SYNCING
            if state == PairingState.PAIRED
            else self.channel.SyncStage.DISCOVERING
        )
        details: dict[str, Any] = {"pairing": {"state": state, **pairing}}
        if self.own_jid:
            details["pairing"].setdefault("jid", self.own_jid)
            phone = phone_for_jid(self.own_jid)
            if phone:
                details["pairing"].setdefault("phone", phone)
        if self.landed:
            details["items"] = self.landed
        self.reporter.report(stage, details=details)

    def _mark_paired(self, jid: str) -> None:
        """Record the linked identity and clear the QR from the persisted row.

        The own-JID is merged under a row lock (not a full-column write from this
        long-lived instance) so a reconnect cannot clobber a concurrent operator
        ``stop``/``disconnect`` writing the ``desired`` key.
        """

        if jid:
            self.own_jid = bare_jid(jid)
            self.channel.merge_subscription_state(own_jid=self.own_jid)
        self.pairing = PairingState.PAIRED
        self._report(PairingState.PAIRED)

    # -- ingest ------------------------------------------------------------

    def _ingest(self, batch: list[tuple[ChatMessage, Any]]) -> bool:
        """Land one queued batch in bounded chunks; re-check liveness between them.

        A history payload can be thousands of messages with media downloads, so
        chunking keeps the cooperative-stop / shutdown / lock checks responsive
        instead of blocking behind one giant ``ingest``. Returns whether to keep
        running, so a stop mid-history exits promptly.
        """

        message_model = apps.get_model("messaging", "Message")
        for start in range(0, len(batch), INGEST_CHUNK):
            chunk = batch[start : start + INGEST_CHUNK]
            parsed = [parsed_message(self._with_media(message, payload)) for message, payload in chunk]
            landed = message_model.objects.ingest(
                parsed,
                channel=self.channel,
                message_kind=message_model.MessageKind.CHAT,
                quote_edges=False,
            )
            self.landed += len(landed)
            self._report(self.pairing)
            if start + INGEST_CHUNK < len(batch) and not self._still_wanted():
                return False
        return self._still_wanted()

    def _with_media(self, message: ChatMessage, payload: Any) -> ChatMessage:
        """Resolve a queued message's media facts into bytes (or unavailability)."""

        facts = message.metadata.pop("_media_facts", ())
        if not facts:
            return message
        media = tuple(
            MediaItem(mime=fact.mime, name=fact.name, content=self._download(payload))
            for fact in facts
        )
        return ChatMessage(**{**message.__dict__, "media": media})

    def _download(self, payload: Any) -> bytes | None:
        """Fetch one message's media bytes; ``None`` lands the marker part."""

        if payload is None:
            return None
        try:
            return self.client.download_any(payload)
        except Exception:  # noqa: BLE001 - expired keys and vendor hiccups are routine
            logger.info("WhatsApp media download failed for channel %s.", self.channel.sqid)
            return None

    # -- vendor callbacks (Go threads: translate + enqueue only) ------------

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
                    sender = self.own_jid
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
