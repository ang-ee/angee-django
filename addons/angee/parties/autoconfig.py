"""Settings fragments contributed when the parties addon is installed."""

from __future__ import annotations

SETTINGS = {
    # Directory backends a ``parties.Directory`` row may select. ``manual`` is the
    # neutral null-object (no source; ``ImplClassField`` requires a non-empty
    # registry). Source addons add their own with a yamlconf dotted key, e.g.
    # ``"ANGEE_DIRECTORY_BACKEND_CLASSES.carddav"`` from ``parties_integrate_carddav``.
    "ANGEE_DIRECTORY_BACKEND_CLASSES": {
        "manual": "angee.parties.backends.ManualDirectoryBackend",
    },
}
"""Django settings contributed when the parties addon is installed."""
