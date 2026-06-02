"""Django config for Angee's IAM addon."""

from __future__ import annotations

from angee.base.apps import BaseAddonConfig


class IAMConfig(BaseAddonConfig):
    """Source app manifest for Angee identity models."""

    default = True
    name = "angee.iam"
    label = "iam"
    depends_on = ("base",)
    settings_defaults = {
        "AUTH_USER_MODEL": "iam.User",
        "ANGEE_IAM_CLIENTS": (),
    }
    """IAM owns the user model and optional settings-sourced OAuth/OIDC clients."""

    resources = {
        "master": ({"path": "resources/master/010_iam.vendor.yaml", "adopt": "slug"},),
    }
    """Default vendor catalogue, adopted by slug so reloads stay idempotent."""

    def ready(self) -> None:
        """Wire IAM-owned REBAC relationships after app population."""

        super().ready()
        from angee.iam import signals

        signals.connect()
