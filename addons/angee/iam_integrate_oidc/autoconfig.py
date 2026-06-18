"""Settings fragments required by the OIDC login addon."""

from __future__ import annotations

SETTINGS = {
    # Lifetime of a cached ``.well-known/openid-configuration`` discovery document.
    # The single-use redirect-state TTL is OAuth-level and owned by ``integrate``.
    "ANGEE_OIDC_DISCOVERY_TTL": 3600,
    "ANGEE_OAUTH_PROVIDER_TYPES.generic_oidc": "angee.iam_integrate_oidc.providers.GenericOidc",
    "ANGEE_OAUTH_PROVIDER_TYPES.google": "angee.iam_integrate_oidc.providers.GoogleType",
}
"""Django settings contributed when the OIDC login addon is installed."""
