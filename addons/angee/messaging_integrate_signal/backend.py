"""Signal channel backend: live identity and neutral message hooks."""

from __future__ import annotations

from angee.messaging.backends import LiveChannelBackend, ParsedMessage
from angee.messaging_integrate_signal.constants import SESSION_QUEUE


class SignalChannelBackend(LiveChannelBackend):
    """Channel backend for one signal-cli linked-device session."""

    key = "signal"
    label = "Signal"
    icon = "message-circle"
    session_queue = SESSION_QUEUE
    session_class = "angee.messaging_integrate_signal.session.SignalSession"

    def normalize_account_id(self, raw: str) -> str:
        """Return Signal's E.164 account id without surrounding whitespace."""

        return str(raw or "").strip()

    def account_label(self, own_id: str) -> str:
        """Return the linked Signal account's E.164 identifier."""

        return self.normalize_account_id(own_id)

    def parse_live_message(self, message: ParsedMessage) -> ParsedMessage:
        """Pass the Signal boundary's already-neutral message through."""

        return message
