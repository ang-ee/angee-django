"""Model opt-ins for GraphQL metadata and publication policy."""

from typing import Any


class ChangeBroadcastMixin:
    """Declare per-row policy for generic GraphQL change subscriptions.

    Local change observers still receive every event. The publisher evaluates
    this policy while the row is live, so deletes retain the same boundary as
    saves. Models without this opt-in use the publisher's default broadcast.
    """

    def broadcasts_changes(self) -> bool:
        """Return whether this row may appear on generic change subscriptions."""

        return True


class ImplementationChoicesMixin:
    """Let a model authorize implementation metadata beyond the admin default."""

    @classmethod
    def can_read_impl_choices(cls, field_name: str, actor: Any) -> bool:
        """Admit registry metadata only; this never authorizes record access."""

        return False
