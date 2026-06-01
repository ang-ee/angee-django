"""Django config for Angee's IAM addon."""

from __future__ import annotations

from angee.base.apps import BaseAddonConfig


class IAMConfig(BaseAddonConfig):
    """Source app manifest for Angee identity models."""

    default = True
    name = "angee.iam"
    label = "iam"
    depends_on = ("base",)
    settings_defaults = {"AUTH_USER_MODEL": "iam.User"}
    """IAM owns the swappable user model it emits as the composed default."""

    def ready(self) -> None:
        """Wire IAM-owned REBAC relationships after app population."""

        super().ready()
        from angee.iam import signals

        signals.connect()
