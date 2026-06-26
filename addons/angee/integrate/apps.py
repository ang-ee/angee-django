"""Django config for Angee's integration runtime addon."""

from __future__ import annotations

from django.apps import AppConfig


class IntegrateConfig(AppConfig):
    """Source app manifest for Angee integration runtime primitives."""

    default = True
    angee_addon = True
    angee_web_package = "@angee/integrate"
    name = "angee.integrate"
    label = "integrate"
    depends_on = ("angee.iam",)
    schemas = "schema.schemas"
    permissions = "permissions.zed"

    resources = {
        "master": ({"path": "resources/master/010_integrate.vendor.yaml", "adopt": "slug"},),
        "install": ({"path": "resources/install/010_integrate.oauthclient.yaml", "adopt": ["slug", "environment"]},),
    }
    """Default vendor catalogue (master) and public OAuth client seeds (install),
    adopted by natural key so reloads stay idempotent. The OIDC refinements for the
    login-capable clients are seeded by the ``iam_integrate_oidc`` addon."""

    def ready(self) -> None:
        """Wire integration-owned denormalization maintenance after app population."""

        super().ready()
        from angee.integrate import signals

        signals.connect()
