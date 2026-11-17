"""Settings fragments required by the OIDC login addon."""

from __future__ import annotations

from typing import TypedDict

_Settings = TypedDict(
    "_Settings",
    {
        "ANGEE_OAUTH_PROVIDER_TYPE_CLASSES.generic_oidc": str,
    },
)

SETTINGS: _Settings = {
    # Discovery and JWKS caching and the single-use redirect-state TTL are owned by
    # ``integrate``; the Google preset by ``integrate_google``.
    "ANGEE_OAUTH_PROVIDER_TYPE_CLASSES.generic_oidc": "angee.iam_integrate_oidc.providers.GenericOidc",
}
"""Django settings contributed when the OIDC login addon is installed."""
