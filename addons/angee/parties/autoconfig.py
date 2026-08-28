"""Settings fragments contributed when the parties addon is installed."""

from __future__ import annotations

SETTINGS = {
    "CELERY_BEAT_SCHEDULE:append": {
        "parties.refresh_handle_suggestions": {
            "task": "parties.refresh_handle_suggestions",
            "schedule": 3600.0,
        },
    },
    # Directory backends a ``parties.Directory`` row may select. ``manual`` is the
    # neutral null-object (no source; ``ImplClassField`` requires a non-empty
    # registry). Source addons add their own with a yamlconf dotted key, e.g.
    # ``"ANGEE_DIRECTORY_BACKEND_CLASSES.carddav"`` from ``parties_integrate_carddav``.
    "ANGEE_DIRECTORY_BACKEND_CLASSES": {
        "manual": "angee.parties.backends.ManualDirectoryBackend",
    },
}
"""Django settings contributed when the parties addon is installed."""
