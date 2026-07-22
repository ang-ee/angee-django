"""iMessage channel backend — a backup/mount import target, not a live source.

Unlike WhatsApp, an Apple Messages account is not linkable as a live device: this
addon imports history from an iPhone backup only. The backend therefore carries no
session, pairing, or OAuth — it exists so a ``messaging.Channel`` can name the
``imessage`` platform, and so the archive/mount extractors can resolve a confirmed
channel of this platform. It ingests nothing on its own (``fetch_messages`` is
empty); the backup importer populates it.
"""

from __future__ import annotations

from typing import Any, ClassVar

from django.apps import apps
from django.core.exceptions import ValidationError
from rebac import system_context

from angee.messaging.backends import ChannelBackend, ParsedMessage


class ImessageChannelBackend(ChannelBackend):
    """Channel backend for imported SMS + iMessage history — import only."""

    key = "imessage"
    label = "iMessage"
    icon = "message-square"

    message_kind: ClassVar[str] = "chat"
    quote_edges: ClassVar[bool] = False

    def fetch_messages(self) -> list[ParsedMessage]:
        """Return nothing — an iMessage channel is populated by backup import."""

        return []


def confirmed_imessage_channel(sqid: str) -> Any:
    """Return the confirmed iMessage channel named by its public sqid.

    The single owner of "resolve an import target to an iMessage channel", shared
    by the archive (ZIP) and mount (drive) backup extractors. The Channel model is
    resolved lazily so importing this module never touches the app registry.
    """

    channel_model = apps.get_model("messaging", "Channel")
    with system_context(reason="messaging_integrate_imessage.channel.confirm"):
        channel = channel_model._base_manager.filter(
            sqid=sqid, backend_class=ImessageChannelBackend.key
        ).first()
    if channel is None:
        raise ValidationError({"target": f"No iMessage channel {sqid!r}."})
    return channel
