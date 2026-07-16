"""Neutral live-channel backend used by pairing contract tests."""

from angee.messaging.backends import LiveChannelBackend, ParsedMessage


class FakePairingBackend(LiveChannelBackend):
    """Vendor-free live backend for the messaging pairing surface."""

    key = "fake_live"
    label = "Fake Live"
    session_queue = "fake-live"

    def account_label(self, own_id: str) -> str:
        """Render a visible label independently from the durable identity."""

        return f"Account {own_id}"

    def parse_live_message(self, message: ParsedMessage) -> ParsedMessage:
        """Pass already-neutral messages through unchanged."""

        return message
