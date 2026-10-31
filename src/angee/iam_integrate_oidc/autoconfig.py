"""Settings fragments required by the OIDC login addon."""

from __future__ import annotations

SETTINGS = {
    "ANGEE_OAUTH_PROVIDER_TYPE_CLASSES.generic_oidc": "angee.iam_integrate_oidc.providers.GenericOidc",
    "ANGEE_OAUTH_PROVIDER_TYPE_CLASSES.google": "angee.iam_integrate_oidc.providers.GoogleType",
}
"""Django settings contributed when the OIDC login addon is installed."""
