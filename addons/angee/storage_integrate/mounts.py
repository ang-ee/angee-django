"""External mount backend contract and the local-folder implementation."""

from __future__ import annotations

import contextlib
import logging
import os
import stat as stat_module
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, ClassVar, cast

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from rebac import system_context

from angee.base.impl import resolve_impl_class
from angee.integrate.impl import BridgeImpl
from angee.storage.exceptions import UploadError
from angee.storage.uploads import MIME_SNIFF_BYTES, detect_mime, sha256_stream

logger = logging.getLogger(__name__)

_DIRECTORY_LIST_LIMIT = 1000


@dataclass(frozen=True)
class MountEntry:
    """One file exposed by an external mount backend."""

    path: str
    size_bytes: int
    mtime_ns: int


@dataclass(frozen=True)
class MountLocation:
    """One navigable node in a mount source tree."""

    token: str
    label: str
    is_navigable: bool
    is_mountable: bool
    blocked_reason: str


@dataclass(frozen=True)
class MountBrowseResult:
    """One bounded page of locations in a mount source tree."""

    location: MountLocation
    parent_token: str | None
    entries: tuple[MountLocation, ...]
    truncated: bool
    supports_manual_token: bool


class MountBackend(BridgeImpl):
    """Descriptor contract for walking and reading an external storage source."""

    category = "mount"
    label = "Mount"
    requires_credential: ClassVar[bool] = False

    @classmethod
    def browse(
        cls,
        *,
        credential: Any | None = None,
        token: str = "",
    ) -> MountBrowseResult:
        """List child locations of ``token`` before a Mount row exists."""

        raise NotImplementedError

    def __init__(self, integration: Any) -> None:
        """Bind the Mount row and initialize per-run MIME observations."""

        super().__init__(integration)
        self._entry_mime_types: dict[str, str] = {}
        self.observed_paths: set[str] = set()
        self.scan_errors = 0

    def check_source(self) -> None:
        """Raise :class:`ValidationError` when the configured source is unusable."""

        raise NotImplementedError

    def iter_entries(self) -> Iterator[MountEntry]:
        """Yield files in deterministic path order."""

        raise NotImplementedError

    def iter_directories(self) -> Iterator[str]:
        """Yield source directories in deterministic order, the tree root excluded.

        A file walk yields only files, so a directory with no files under it
        would never reach the indexer. Mount sync mirrors the full source tree
        by ensuring a folder for every path this yields, empty directories
        included. Backends apply the same symlink and traversal safety as
        :meth:`iter_entries`; each path is drive-relative in POSIX form.
        """

        raise NotImplementedError

    def entry_hash(self, entry: MountEntry) -> str:
        """Stream-hash ``entry`` and retain its MIME for the current sync run."""

        try:
            with contextlib.closing(self.open_entry(entry)) as stream:
                digest, size_bytes, head = sha256_stream(stream, capture_head=MIME_SNIFF_BYTES)
        except OSError as error:
            raise UploadError(f"could not read external file {entry.path!r}: {error}") from error
        if size_bytes != entry.size_bytes:
            raise UploadError(f"external file changed while reading: {entry.path}")
        self._entry_mime_types[entry.path] = detect_mime(head, PurePosixPath(entry.path).name)
        return digest

    def entry_mime(self, entry: MountEntry) -> str:
        """Return the MIME captured while hashing ``entry``."""

        return self._entry_mime_types.get(entry.path, "")

    def open_entry(self, entry: MountEntry) -> BinaryIO:
        """Open ``entry`` for streaming reads."""

        raise NotImplementedError

    def storage_backend_spec(self) -> tuple[str, dict[str, Any]]:
        """Return the storage backend key and config used to serve indexed bytes."""

        raise NotImplementedError


class LocalFolderMountBackend(MountBackend):
    """Walk and read a folder on the Django host without following symlinks."""

    key = "local_folder"
    label = "Local folder"
    requires_credential = False

    @classmethod
    def browse(
        cls,
        *,
        credential: Any | None = None,
        token: str = "",
    ) -> MountBrowseResult:
        """List one bounded level of local directories for mount selection."""

        del credential
        raw_token = str(token or "").strip()
        candidate = Path.home() if not raw_token else Path(raw_token)
        if raw_token and not candidate.is_absolute():
            raise ValidationError({"token": "Local folder path must be absolute."})
        try:
            current = candidate.resolve(strict=True)
        except OSError as error:
            raise ValidationError({"token": f"Local folder does not exist: {candidate}"}) from error
        if not current.is_dir():
            raise ValidationError({"token": f"Local folder path is not a directory: {current}"})
        if not os.access(current, os.R_OK | os.X_OK):
            raise ValidationError({"token": f"Local folder is not readable: {current}"})

        try:
            directory_children = list(current.iterdir())
        except OSError as error:
            raise ValidationError({"token": f"Could not list local folder: {current}"}) from error
        children = sorted(
            (
                resolved
                for child in directory_children
                if (resolved := _resolve_browsable_directory(child)) is not None
            ),
            key=lambda child: (child.name.casefold(), child.name),
        )
        truncated = len(children) > _DIRECTORY_LIST_LIMIT
        visible_children = children[:_DIRECTORY_LIST_LIMIT]
        return MountBrowseResult(
            location=_local_mount_location(current),
            parent_token=None if current.parent == current else str(current.parent),
            entries=tuple(_local_mount_location(child) for child in visible_children),
            truncated=truncated,
            supports_manual_token=True,
        )

    def __init__(self, integration: Any) -> None:
        """Bind the Mount and reserve its per-instance validated-root cache."""

        super().__init__(integration)
        self._root: Path | None = None

    @property
    def root(self) -> Path:
        """Return the source root validated for this backend instance."""

        if self._root is None:
            self.check_source()
        return cast(Path, self._root)

    def check_source(self) -> None:
        """Validate that the configured local folder remains usable."""

        self._root = validate_local_folder_root(str(self.bridge.config.get("root") or ""))

    def _walk_directories(self) -> Iterator[tuple[Path, list[str]]]:
        """Yield each real directory and its sorted file names, symlinks pruned.

        The one traversal shared by :meth:`iter_entries` and
        :meth:`iter_directories`, so the symlink and ordering safety cannot
        drift between the file and folder passes. Symlinked subdirectories are
        removed from the walk in place, so it never descends into or yields a
        link target.
        """

        root = self.root
        for directory, directory_names, file_names in os.walk(root, followlinks=False):
            directory_path = Path(directory)
            directory_names[:] = sorted(
                name for name in directory_names if not (directory_path / name).is_symlink()
            )
            yield directory_path, sorted(file_names)

    def iter_entries(self) -> Iterator[MountEntry]:
        """Yield regular files in deterministic POSIX-path order."""

        root = self.root
        for directory_path, file_names in self._walk_directories():
            for name in file_names:
                path = directory_path / name
                if path.is_symlink():
                    continue
                relative_path = path.relative_to(root).as_posix()
                self.observed_paths.add(relative_path)
                try:
                    stat = path.stat()
                except OSError as error:
                    self.scan_errors += 1
                    logger.warning("storage.mount.stat: skipped %s: %s", path, error)
                    continue
                if not stat_module.S_ISREG(stat.st_mode):
                    self.observed_paths.discard(relative_path)
                    continue
                yield MountEntry(
                    path=relative_path,
                    size_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                )

    def iter_directories(self) -> Iterator[str]:
        """Yield every subdirectory beneath the root, empty ones included."""

        root = self.root
        for directory_path, _file_names in self._walk_directories():
            if directory_path == root:
                continue
            yield directory_path.relative_to(root).as_posix()

    def open_entry(self, entry: MountEntry) -> BinaryIO:
        """Open a walked file without allowing the relative key to escape root."""

        path = self._entry_path(entry)
        try:
            return path.open("rb")
        except OSError as error:
            raise UploadError(f"could not open external file {entry.path!r}: {error}") from error

    def storage_backend_spec(self) -> tuple[str, dict[str, Any]]:
        """Serve indexed bytes through the read-only local-folder backend."""

        # This key belongs to ANGEE_STORAGE_BACKEND_CLASSES, not the Mount registry.
        return "local_folder", {"root": str(self.root)}

    def _entry_path(self, entry: MountEntry) -> Path:
        """Resolve one backend-issued relative POSIX path beneath the root."""

        relative = PurePosixPath(entry.path)
        if relative.is_absolute() or ".." in relative.parts:
            raise UploadError(f"external path escapes its root: {entry.path!r}")
        root = self.root
        path = root.joinpath(*relative.parts).resolve()
        if path != root and root not in path.parents:
            raise UploadError(f"external path escapes its root: {entry.path!r}")
        return path


def validate_local_folder_root(path: str) -> Path:
    """Return a safe, readable absolute local-folder root."""

    raw_path = str(path or "").strip()
    candidate = Path(raw_path)
    if not raw_path or not candidate.is_absolute():
        raise ValidationError("Local folder path must be absolute.")
    try:
        root = candidate.resolve(strict=True)
    except OSError as error:
        raise ValidationError(f"Local folder does not exist: {candidate}") from error
    if not root.is_dir():
        raise ValidationError(f"Local folder path is not a directory: {root}")
    if not os.access(root, os.R_OK | os.X_OK):
        raise ValidationError(f"Local folder is not readable: {root}")

    media_value = str(getattr(settings, "MEDIA_ROOT", "") or "").strip()
    if media_value:
        media_root = Path(media_value).resolve()
        if root == media_root or media_root in root.parents:
            raise ValidationError("Local folder must be outside MEDIA_ROOT.")

    data_value = str(getattr(settings, "ANGEE_DATA_DIR", "") or "").strip()
    if data_value:
        data_root = Path(data_value).resolve()
        if root == data_root or data_root in root.parents:
            raise ValidationError("Local folder must be outside ANGEE_DATA_DIR.")
    return root


def browse_mount_source(
    backend_class: str,
    *,
    credential: Any | None = None,
    token: str = "",
) -> MountBrowseResult:
    """Dispatch pre-connection browsing through the configured Mount backend."""

    try:
        impl = resolve_impl_class(
            "ANGEE_STORAGE_MOUNT_BACKEND_CLASSES",
            backend_class,
            MountBackend,
        )
    except ImproperlyConfigured as error:
        raise ValidationError({"backend_class": str(error)}) from error
    return cast(type[MountBackend], impl).browse(credential=credential, token=token)


def _resolve_browsable_directory(path: Path) -> Path | None:
    """Return a resolved real directory, skipping symlinks and stat races."""

    try:
        if path.is_symlink() or not path.is_dir():
            return None
        return path.resolve(strict=True)
    except OSError:
        return None


def _local_folder_validation_reason(path: Path) -> str:
    """Return the mount-root validation failure for ``path``, or an empty string."""

    try:
        validate_local_folder_root(str(path))
    except ValidationError as error:
        return "; ".join(error.messages)
    return ""


def _local_mount_location(path: Path) -> MountLocation:
    """Project one resolved local directory into the neutral browser contract."""

    reason = _local_folder_validation_reason(path)
    if not reason:
        mount_model = apps.get_model("storage_integrate", "Mount")
        with system_context(reason="storage_integrate.browse_mount_source"):
            if mount_model.objects.filter(config__root=str(path)).exists():
                reason = "Already mounted"
    return MountLocation(
        token=str(path),
        label=path.name or str(path),
        is_navigable=os.access(path, os.R_OK | os.X_OK),
        is_mountable=not reason,
        blocked_reason=reason,
    )
