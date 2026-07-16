"""Telegram channel backend: live identity and neutral message hooks."""

from __future__ import annotations

from angee.messaging.backends import LiveChannelBackend, ParsedMessage
from angee.messaging_integrate_telegram.constants import SESSION_QUEUE
from angee.messaging_integrate_telegram.identity import account_label


class TelegramChannelBackend(LiveChannelBackend):
    """Channel backend for one Telegram user session."""

    key = "telegram"
    label = "Telegram"
    icon = "send"
    session_queue = SESSION_QUEUE
    session_class = "angee.messaging_integrate_telegram.session.TelegramSession"
    def normalize_account_id(self, raw: str) -> str:
        """Return Telegram's stable numeric user id as a string."""

        return str(raw or "").strip()

    def account_label(self, own_id: str) -> str:
        """Return the persisted phone, username, or stable id account label."""

        state = self.bridge.subscription_state
        return account_label(
            own_id,
            phone=state.get("phone", ""),
            username=state.get("username", ""),
        )

    def remember_account_profile(self, own_id: str, *, phone: str, username: str) -> None:
        """Persist Telegram's mutable account labels beside the durable id."""

        if self.normalize_account_id(own_id) != self.normalize_account_id(
            self.bridge.subscription_state.get(self.state_identity_key, "")
        ):
            return
        self.bridge.merge_subscription_state(
            phone=str(phone or "").strip(),
            username=str(username or "").strip().lstrip("@"),
        )

    def parse_live_message(self, message: ParsedMessage) -> ParsedMessage:
        """Pass the Telegram boundary's already-neutral message through."""

        return message
