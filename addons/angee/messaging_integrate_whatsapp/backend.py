"""WhatsApp channel backend: live-session identity and message parsing hooks."""

from __future__ import annotations

from angee.integrate.live import SessionLoggedOut
from angee.messaging.backends import LiveChannelBackend, ParsedMessage
from angee.messaging_integrate_whatsapp.client import DuplicateAccountRejected
from angee.messaging_integrate_whatsapp.constants import SESSION_QUEUE
from angee.messaging_integrate_whatsapp.parser import (
    ChatMessage,
    MediaItem,
    bare_jid,
    parsed_message,
    phone_for_jid,
)


class WhatsAppChannelBackend(LiveChannelBackend):
    """Channel backend for a linked WhatsApp account — one live session per channel."""

    key = "whatsapp"
    label = "WhatsApp"
    icon = "message-circle"
    session_queue = SESSION_QUEUE
    session_class = "angee.messaging_integrate_whatsapp.session.WhatsAppSession"
    state_identity_key = "own_jid"
    media_item_class = MediaItem

    def normalize_account_id(self, raw: str) -> str:
        """Return WhatsApp's durable bare-JID account identity."""

        return bare_jid(raw)

    def account_label(self, own_id: str) -> str:
        """Return a human label for a WhatsApp account id."""

        return phone_for_jid(own_id) or own_id

    def parse_live_message(self, message: ChatMessage) -> ParsedMessage:
        """Map one queued WhatsApp live message onto the neutral messaging seam."""

        return parsed_message(message)

    def duplicate_account_error(self) -> Exception:
        """Return the runtime error recorded for a duplicate WhatsApp account."""

        return DuplicateAccountRejected("Another channel already owns this WhatsApp account.")

    def logged_out_error(self) -> SessionLoggedOut:
        """Restore WhatsApp's operator-visible phone/device logout wording."""

        return SessionLoggedOut("The linked phone removed this device.")
