"""Settings fragments required by the OIDC login addon."""

from __future__ import annotations

SETTINGS = {
    # Lifetime of a cached ``.well-known/openid-configuration`` discovery document.
    # The single-use redirect-state TTL is OAuth-level and owned by ``integrate``.
    "ANGEE_OIDC_DISCOVERY_TTL": 3600,
}
"""Django settings contributed when the OIDC login addon is installed."""
