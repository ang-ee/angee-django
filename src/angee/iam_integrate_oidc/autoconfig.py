"""Settings fragments required by the OIDC login addon."""

from __future__ import annotations

from typing import TypedDict

_Settings = TypedDict(
    "_Settings",
    {
        "ANGEE_OIDC_JWKS_TTL": int,
        "ANGEE_OAUTH_PROVIDER_TYPE_CLASSES.generic_oidc": str,
        "ANGEE_OAUTH_PROVIDER_TYPE_CLASSES.google": str,
    },
)

SETTINGS: _Settings = {
    "ANGEE_OIDC_JWKS_TTL": 3600,
    "ANGEE_OAUTH_PROVIDER_TYPE_CLASSES.generic_oidc": "angee.iam_integrate_oidc.providers.GenericOidc",
    "ANGEE_OAUTH_PROVIDER_TYPE_CLASSES.google": "angee.iam_integrate_oidc.providers.GoogleType",
}
"""Django settings contributed when the OIDC login addon is installed."""
