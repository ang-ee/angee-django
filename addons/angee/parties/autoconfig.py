"""Settings fragments contributed when the parties addon is installed."""

from __future__ import annotations

SETTINGS = {
    "ANGEE_HOOKS:append": ["ANGEE_PARTIES_SHARED_SENDER_PROVIDERS", "ANGEE_PARTIES_SIGNING_PROVIDERS"],
    # Evidence a downstream addon contributes to the hourly suggestion task, as
    # dotted callables: handles whose display names are no evidence, and
    # ``angee.parties.managers.Signing`` rows for signature mining.
    "ANGEE_PARTIES_SHARED_SENDER_PROVIDERS": [],
    "ANGEE_PARTIES_SIGNING_PROVIDERS": [],
    "ANGEE_IMPL_REGISTRIES:append": ["angee.parties.backends.DirectoryBackend"],
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
