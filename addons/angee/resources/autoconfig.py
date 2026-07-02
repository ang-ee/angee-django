"""Settings fragments required by Angee resources."""

from __future__ import annotations

SETTINGS = {
    # Resource entry source factories keyed by manifest field. ``resources`` owns
    # the local-file source; addons that own other transports append their keys.
    "ANGEE_RESOURCE_SOURCE_CLASSES": {
        "path": "angee.resources.sources.path_source",
    },
}
"""Django settings contributed when resources is installed."""
