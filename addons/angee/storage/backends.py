"""Storage backend protocol and the bundled filesystem backend.

A backend is Django's ``Storage`` plus a presigned-download hook. Uploads
follow one client protocol regardless of backend kind: ``begin`` reserves a
``File`` row and answers with a single ``upload_url``, the client sends one
raw ``PUT``, and ``finalize`` verifies the bytes. Today every upload proxies
through the server; a backend that can presign uploads natively (S3-style)
is the follow-up that adds a ``presigned_put`` hook and the ``"presigned"``
method arm beside it.

Backend rows (``storage.Backend``) name a subclass of :class:`StorageBackend`
by a key in ``ANGEE_STORAGE_BACKEND_CLASSES`` and carry its constructor config;
resolution and caching live on the model that owns the row. This module stays
ORM-free.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.files.storage import FileSystemStorage, Storage

logger = logging.getLogger(__name__)

DOWNLOAD_URL_TTL_SECONDS = 3600
"""Lifetime requested for presigned download URLs."""


class StorageBackend(Storage):
    """Django ``Storage`` augmented with the storage addon's hooks.

    Subclasses implement Django's existing surface (``_save``, ``_open``,
    ``delete``, ``exists``, ``url``, ``size``) plus the hooks below. Backends
    do not implement authorization, byte hashing, or upload state; those live
    on ``File.objects`` and the REBAC schema.
    """

    def __init__(self, *, backend_config: Mapping[str, Any] | None = None) -> None:
        """Store the resolved per-row backend config."""

        self.backend_config = dict(backend_config or {})

    def presigned_get(self, key: str, *, expires_in: int) -> str | None:
        """Return a time-limited download URL, or ``None`` to use ``url(key)``."""

        del key, expires_in
        return None

    def discard(self, key: str, *, context: str) -> None:
        """Best-effort delete that logs transport failures instead of raising.

        Cleanup paths (rejected uploads, dedup losers, purge) must not fail
        the surrounding state transition; a failed delete leaves an accepted
        orphan object behind.
        """

        try:
            self.delete(key)
        except OSError as error:
            logger.warning("storage.%s: backend delete failed for %s: %s", context, key, error)
        except Exception:
            logger.exception("storage.%s: backend delete unexpectedly failed for %s", context, key)


class LocalBackend(FileSystemStorage, StorageBackend):
    """Filesystem backend for development and small deployments.

    Reads ``root`` and ``base_url`` from the owning Backend row's config,
    defaulting to Django's ``MEDIA_ROOT`` / ``MEDIA_URL``.
    """

    def __init__(self, *, backend_config: Mapping[str, Any] | None = None) -> None:
        """Bind the filesystem location and public base URL.

        Directories are created lazily by ``FileSystemStorage`` on save.
        """

        StorageBackend.__init__(self, backend_config=backend_config)
        root = Path(self.backend_config.get("root") or settings.MEDIA_ROOT)
        base_url = str(self.backend_config.get("base_url") or settings.MEDIA_URL)
        FileSystemStorage.__init__(self, location=str(root), base_url=base_url)
