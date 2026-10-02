"""Settings fragments required by Angee resources."""

from __future__ import annotations

SETTINGS = {
    "ANGEE_IMPL_REGISTRIES:append": ["angee.resources.sources.ResourceSource"],
    # Resource entry source classes keyed by manifest field. ``resources`` owns
    # the local-file source; addons that own other transports append their keys.
    "ANGEE_RESOURCE_SOURCE_CLASSES": {
        "path": "angee.resources.sources.PathSource",
    },
    # Optional project-level omissions keyed as ``addon.name:resource/path``.
    "ANGEE_RESOURCE_EXCLUDED_ENTRIES": (),
}
"""Django settings contributed when resources is installed."""
