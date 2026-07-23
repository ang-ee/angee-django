"""Console-safe Discord channel backend declaration."""

from __future__ import annotations

from angee.messaging.backends import LiveChannelBackend, ParsedMessage
from angee.messaging_integrate_discord.constants import SESSION_QUEUE


class DiscordChannelBackend(LiveChannelBackend):
    """Live bridge for one Discord bot token across all invited guilds."""

    key = "discord"
    label = "Discord"
    icon = "message-circle"
    session_queue = SESSION_QUEUE
    session_class = "angee.messaging_integrate_discord.session.DiscordSession"
    state_identity_key = "own_id"
    transient_material_keys = ()

    def normalize_account_id(self, raw: str) -> str:
        """Return the stable Discord bot user id as a string."""

        return str(raw or "").strip()

    def account_label(self, own_id: str) -> str:
        """Return the persisted bot username, falling back to its stable id."""

        return str(self.bridge.subscription_state.get("username") or "").strip() or self.normalize_account_id(own_id)

    def remember_account_profile(self, own_id: str, *, username: str) -> None:
        """Persist the mutable bot username beside the durable bot id."""

        if self.normalize_account_id(own_id) != self.normalize_account_id(
            self.bridge.subscription_state.get(self.state_identity_key, "")
        ):
            return
        self.bridge.merge_subscription_state(username=str(username or "").strip())

    def parse_live_message(self, message: ParsedMessage) -> ParsedMessage:
        """Pass the worker boundary's already-neutral message through."""

        return message
