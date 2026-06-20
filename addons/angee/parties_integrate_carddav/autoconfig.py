"""Settings fragments contributed by the CardDAV directory backend addon."""

from __future__ import annotations

SETTINGS = {
    # Contribute the CardDAV backend into the directory backend registry. Dotted
    # key so it merges into parties' default rather than replacing it.
    "ANGEE_DIRECTORY_BACKEND_CLASSES.carddav": "angee.parties_integrate_carddav.backend.CardDavDirectoryBackend",
}
