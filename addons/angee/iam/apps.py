"""Django config for Angee's IAM addon."""

from __future__ import annotations

from django.apps import AppConfig


class IAMConfig(AppConfig):
    """Source app manifest for Angee identity models."""

    default = True
    angee_addon = True
    default_auto_field = "django.db.models.BigAutoField"
    name = "angee.iam"
    label = "iam"
    depends_on = (
        "angee.resources",
        "angee.graphql",
        "django.contrib.auth",
        "django.contrib.sessions",
    )
    schemas = "schema.schemas"
    permissions = "permissions.zed"

    resources = {
        "install": ({"path": "resources/install/010_iam.oauthclient.yaml", "adopt": ["slug", "environment"]},),
    }
    """Public OAuth client catalogue seeded at install, adopted by slug/environment."""

    def ready(self) -> None:
        """Wire IAM-owned REBAC relationships after app population."""

        super().ready()
        # App population phase 1 imports AppConfig before IAM cleanup wiring is ready.
        from angee.iam import signals

        signals.connect()
