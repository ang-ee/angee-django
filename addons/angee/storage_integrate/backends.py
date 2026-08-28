"""Read-only byte serving for externally-owned local folders."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import FileSystemStorage

from angee.storage.backends import StorageBackend

logger = logging.getLogger(__name__)


class LocalFolderBackend(FileSystemStorage, StorageBackend):
    """Read-only filesystem backend rooted at an externally-owned folder."""

    writable: ClassVar[bool] = False

    def __init__(self, *, backend_config: Mapping[str, Any] | None = None) -> None:
        """Bind the required external root without creating or changing it."""

        StorageBackend.__init__(self, backend_config=backend_config)
        root = str(self.backend_config.get("root") or "").strip()
        if not root:
            raise ImproperlyConfigured("LocalFolderBackend requires backend_config['root'].")
        FileSystemStorage.__init__(self, location=str(Path(root)))

    def _save(self, name: str, content: Any) -> str:
        """Refuse writes to externally-owned bytes."""

        del name, content
        raise OSError("external folder storage is read-only")

    def delete(self, name: str) -> None:
        """Refuse direct deletion of externally-owned bytes."""

        del name
        raise OSError("external folder storage is read-only")

    def url(self, name: str) -> str:
        """Refuse public URLs; external bytes are served through Angee's proxy."""

        del name
        raise ValueError("external folder storage is not URL-accessible")

    def discard(self, key: str, *, context: str) -> None:
        """Leave external bytes untouched when a storage row is purged."""

        logger.info("storage.%s: retained external object %s", context, key)
