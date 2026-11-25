"""Django config for the Angee brand kits addon.

Brand kits use ``ready()`` to refuse deleting the default kit on every delete path.
"""

from __future__ import annotations

from django.apps import AppConfig


class BrandkitConfig(AppConfig):
    """Source app manifest for the Angee brand kits addon."""

    default = True
    name = "angee.brandkit"

    def ready(self) -> None:
        """Connect the default kit's delete refusal after app population."""

        super().ready()
        from angee.brandkit import signals

        signals.connect()
