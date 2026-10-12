"""Settings fragments required by Angee storage."""

from __future__ import annotations

SETTINGS = {
    "ANGEE_IMPL_REGISTRIES:append": ["angee.storage.backends.StorageBackend"],
    "ANGEE_STORAGE_DEFAULT_DRIVE": "assets",
    "ANGEE_STORAGE_PROXY_UPLOAD_MAX_BYTES": 64 * 1024 * 1024,
    # Images a server ingests to render inline (avatars): raster types only,
    # since an SVG can carry script, sniffed from the bytes. They live on their
    # own drive, so content deduplication never resolves one to a user's file.
    "ANGEE_STORAGE_IMAGE_DRIVE": "images",
    "ANGEE_STORAGE_IMAGE_MAX_BYTES": 5 * 1024 * 1024,
    "ANGEE_STORAGE_IMAGE_TYPES": [
        "image/avif",
        "image/bmp",
        "image/gif",
        "image/heic",
        "image/heif",
        "image/jpeg",
        "image/png",
        "image/tiff",
        "image/webp",
    ],
    # Types a browser opens in its own viewer, served inline when a file is
    # opened directly (``major/*`` names a whole major type). Raster images
    # above render inline too; every other type downloads as an attachment.
    "ANGEE_STORAGE_VIEWER_TYPES": [
        "application/pdf",
        "audio/*",
        "text/plain",
        "video/*",
    ],
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
