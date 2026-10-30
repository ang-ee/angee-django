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
    name = "angee.iam_integrate_oidc"
