"""Django config for Angee's OIDC login addon."""

from __future__ import annotations

from django.apps import AppConfig


class IAMIntegrateOidcConfig(AppConfig):
    """Source app manifest for the OIDC login addon.

    OIDC end to end: it extends ``integrate``'s OAuth client with login fields,
    the OIDC protocol, and ID-token verification, and composes ``iam`` (the user
    and session) into the login/link flow.
    """

    default = True
    angee_addon = True
    default_auto_field = "django.db.models.BigAutoField"
    name = "angee.iam_integrate_oidc"
    label = "iam_integrate_oidc"
    depends_on = ("angee.iam", "angee.integrate")
    schemas = "schema.schemas"

    resources = {
        "install": (
            {"path": "resources/install/010_integrate.oauthclient.yaml", "adopt": ["slug", "environment"]},
        ),
    }
    """OIDC defaults for the login-capable public OAuth clients integrate seeds."""

    def ready(self) -> None:
        """Wire the last-sign-in disconnect guard after app population."""

        super().ready()
        from angee.iam_integrate_oidc import signals

        signals.connect()
