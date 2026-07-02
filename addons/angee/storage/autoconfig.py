"""Settings fragments required by Angee storage."""

from __future__ import annotations

SETTINGS = {
    "ANGEE_STORAGE_DEFAULT_DRIVE": "assets",
    "ANGEE_STORAGE_PROXY_UPLOAD_MAX_BYTES": 64 * 1024 * 1024,
    "ANGEE_STORAGE_DRAFT_TTL_HOURS": 24,
    "ANGEE_STORAGE_TRASH_TTL_DAYS": 30,
    # The ``Backend.backend_class`` registry: each key a ``Backend`` row may name
    # → the dotted path of the ``StorageBackend`` it resolves to. A backend addon
    # adds its own impl with a yamlconf dotted key
    # (``"ANGEE_STORAGE_BACKEND_CLASSES.s3": "…"``); see ``ImplClassField``.
    "ANGEE_STORAGE_BACKEND_CLASSES": {
        "local": "angee.storage.backends.LocalBackend",
    },
}
"""Django settings contributed when the storage addon is installed."""
