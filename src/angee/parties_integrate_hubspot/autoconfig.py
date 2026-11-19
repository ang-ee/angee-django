"""Settings fragments contributed by the HubSpot directory backend addon."""

from __future__ import annotations

SETTINGS = {
    # A dotted key merges into the parties directory backend registry rather than replacing it.
    "ANGEE_DIRECTORY_BACKEND_CLASSES.hubspot": "angee.parties_integrate_hubspot.backends.HubSpotDirectoryBackend",
}
