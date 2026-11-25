"""Django config for the Angee message templates addon.

Message templates use ``ready()`` to refuse, on every delete path, deleting a row
other rows name while its key would stop resolving.
"""

from __future__ import annotations

from django.apps import AppConfig


class MessagingTemplatesConfig(AppConfig):
    """Source app manifest for the Angee message templates addon."""

    default = True
    name = "angee.messaging_templates"

    def ready(self) -> None:
        """Connect the delete refusal after app population."""

        super().ready()
        # The receivers name the model, which the app registry populates only now.
        from angee.messaging_templates import signals

        signals.connect()
