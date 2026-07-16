"""Worker-only live channel ingestion.

This module imports :mod:`angee.integrate.session`, whose worker loop imports
``qrcode``. The console must never load either worker module; console-safe
backend declarations stay in :mod:`angee.messaging.backends`, mirroring the
``integrate.live`` / ``integrate.session`` split.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from django.apps import apps

from angee.integrate.session import LiveSession

INGEST_CHUNK = 100
"""Messages per drain slice so cooperative-stop and lock checks stay responsive."""


class LiveChannelSession(LiveSession):
    """Live worker session that owns queued message media resolution and landing."""

    def _handle(self, kind: str, payload: Any) -> bool:
        """Land messaging batches and ignore other implementation-specific events."""

        if kind == "messages":
            return self._ingest(payload)
        return self._still_wanted()

    def _ingest(self, batch: list[tuple[Any, Any]]) -> bool:
        """Land one queued batch in bounded chunks, checking liveness between them."""

        message_model = apps.get_model("messaging", "Message")
        for start in range(0, len(batch), INGEST_CHUNK):
            chunk = batch[start : start + INGEST_CHUNK]
            parsed = [
                self.live_impl.parse_live_message(self._with_media(message, payload)) for message, payload in chunk
            ]
            landed = message_model.objects.ingest(
                parsed,
                channel=self.bridge,
                message_kind=message_model.MessageKind.CHAT,
                quote_edges=False,
            )
            self.landed += len(landed)
            self._report(self.pairing)
            if start + INGEST_CHUNK < len(batch) and not self._still_wanted():
                return False
        return self._still_wanted()

    def _with_media(self, message: Any, payload: Any) -> Any:
        """Resolve queued media facts through the implementation's declared DTO class."""

        metadata = dict(message.metadata)
        facts = metadata.pop("_media_facts", ())
        if not facts:
            return message
        media_item_class = self.live_impl.media_item_class
        media = tuple(
            media_item_class(
                mime=getattr(fact, "mime", "application/octet-stream"),
                name=getattr(fact, "name", ""),
                content=self._download(payload),
            )
            for fact in facts
        )
        return self._attach_media(replace(message, metadata=metadata), media)

    def _attach_media(self, message: Any, media: tuple[Any, ...]) -> Any:
        """Attach resolved media to the default vendor DTO shape."""

        return replace(message, media=media)
