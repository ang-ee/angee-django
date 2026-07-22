"""Console-safe Matrix channel backend declaration."""

from __future__ import annotations

from angee.messaging.backends import LiveChannelBackend, ParsedMessage
from angee.messaging_integrate_matrix.constants import SESSION_QUEUE


class MatrixChannelBackend(LiveChannelBackend):
    """Live bridge for one user's own Matrix account."""

    key = "matrix"
    label = "Matrix"
    icon = "message-circle"
    session_queue = SESSION_QUEUE
    session_class = "angee.messaging_integrate_matrix.session.MatrixSession"
    transient_material_keys = ("recovery_key",)

    def normalize_account_id(self, raw: str) -> str:
        """Return the stable Matrix user id without surrounding whitespace."""

        return str(raw or "").strip()

    def account_label(self, own_id: str) -> str:
        """Use the stable Matrix user id as the account label."""

        return self.normalize_account_id(own_id)

    def parse_live_message(self, message: ParsedMessage) -> ParsedMessage:
        """Pass the Matrix boundary's already-neutral message through."""

        return message
