"""Read-only byte serving for externally-owned sources."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import FileSystemStorage

from angee.storage.backends import StorageBackend

if TYPE_CHECKING:
    from django.db.models import Model

logger = logging.getLogger(__name__)


class ExternalStorageBackend(StorageBackend):
    """Read-only storage of bytes an external source owns: a reference mount's drive is stored on one.

    Reads go through :meth:`open_external`. Writes, deletes and public URLs are
    refused, since the source owns the bytes and Angee's proxy serves them; a
    purge leaves them in place.
    """

    writable: ClassVar[bool] = False

    def _open(self, name: str, mode: str = "rb") -> Any:
        """Refuse any mode but reading, then open through :meth:`open_external`."""

        if mode not in {"r", "rb"}:
            raise OSError("external storage is read-only")
        return self.open_external(name)

    def open_external(self, name: str) -> Any:
        """Open the externally-owned object stored under the backend key ``name`` for reading."""

        raise NotImplementedError

    def _save(self, name: str, content: Any) -> str:
        """Refuse writes to externally-owned bytes."""

        del name, content
        raise OSError("external storage is read-only")

    def delete(self, name: str) -> None:
        """Refuse direct deletion of externally-owned bytes."""

        del name
        raise OSError("external storage is read-only")

    def url(self, name: str | None) -> str:
        """Refuse public URLs; external bytes are served through Angee's proxy."""

        del name
        raise ValueError("external storage is not URL-accessible")

    def presigned_get(self, key: str, *, expires_in: int) -> str | None:
        """Use the authenticated Angee proxy for every download."""

        del key, expires_in
        return None

    def discard(self, key: str, *, context: str) -> None:
        """Leave externally-owned bytes untouched when a storage row is purged."""

        logger.info("storage.%s: retained external object %s", context, key)


class LocalFolderBackend(ExternalStorageBackend, FileSystemStorage):
    """Read-only filesystem backend rooted at an externally-owned folder."""

    def __init__(self, *, backend_config: Mapping[str, Any] | None = None, backend_row: Model | None = None) -> None:
        """Bind the required external root without creating or changing it."""

        StorageBackend.__init__(self, backend_config=backend_config, backend_row=backend_row)
        root = str(self.backend_config.get("root") or "").strip()
        if not root:
            raise ImproperlyConfigured("LocalFolderBackend requires backend_config['root'].")
        FileSystemStorage.__init__(self, location=str(Path(root)))

    def open_external(self, name: str) -> Any:
        """Open the file beneath the folder through Django's filesystem storage."""

        # django-stubs omits Storage's protected ``_open`` hook, which Django's filesystem storage implements.
        return FileSystemStorage._open(self, name, "rb")  # type: ignore[attr-defined]
