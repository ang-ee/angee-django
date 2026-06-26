"""Django config for Angee's IAM addon."""

from __future__ import annotations

from django.apps import AppConfig


class IAMConfig(AppConfig):
    """Source app manifest for Angee identity models."""

    default = True
    angee_addon = True
    angee_web_package = "@angee/iam"
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
        "demo": ({"path": "resources/demo/010_iam.user.yaml", "adopt": "username"},),
    }

    def ready(self) -> None:
        """Wire IAM-owned REBAC relationships after app population."""

        super().ready()
        # App population phase 1 imports AppConfig before IAM cleanup wiring is ready.
        from angee.iam import signals

        signals.connect()
