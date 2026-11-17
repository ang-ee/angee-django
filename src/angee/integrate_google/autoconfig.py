"""Settings fragments required by the Google OAuth addon."""

from __future__ import annotations

SETTINGS = {
    # Dotted key so it merges into integrate's provider registry, not replaces it.
    "ANGEE_OAUTH_PROVIDER_TYPE_CLASSES.google": "angee.integrate_google.providers.GoogleType",
}
"""Django settings contributed when the Google OAuth addon is installed."""
