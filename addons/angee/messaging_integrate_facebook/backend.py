"""Import-only Facebook channel backend."""

from __future__ import annotations

from typing import ClassVar

from angee.messaging.backends import ChannelBackend, ParsedMessage


class FacebookChannelBackend(ChannelBackend):
    """Channel target populated by Facebook takeout imports, never live polling."""

    key = "facebook"
    label = "Facebook"
    icon = "message-square"

    quote_edges: ClassVar[bool] = False

    def fetch_messages(self) -> list[ParsedMessage]:
        """Return nothing — takeout importers populate this channel."""

        return []
