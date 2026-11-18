"""Google Drive implementation of the external storage Mount contract."""

from __future__ import annotations

import posixpath
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, BinaryIO, ClassVar

from django.core.exceptions import ValidationError

from angee.storage_integrate.models import MountMode
from angee.storage_integrate.mounts import MountBackend, MountBrowseResult, MountEntry, MountLocation
from angee.storage_integrate_google_drive.client import MY_DRIVE, DriveItem, GoogleDriveClient, GoogleDriveError

_TAG_LENGTH = 8
"""Characters of a file id that tag a duplicate sibling name."""
_BROWSE_TOP_LABEL = "Google Drive"


@dataclass
class _DriveWalk:
    """One sync's full walk of the mounted folder: directories, files and their Drive facts."""

    directories: list[str] = field(default_factory=list)
    entries: list[MountEntry] = field(default_factory=list)
    items: dict[str, DriveItem] = field(default_factory=dict)


class GoogleDriveMountBackend(MountBackend):
    """Mirror a Google Drive folder (My Drive, a folder in it, or a shared drive) read with the connecting account.

    Each file's storage key is its Drive file id, so a reference mount reads
    bytes by id and a rename or a move inside the mount keeps its row in either
    mode. Paths come from the parent chain; siblings sharing a name are told
    apart by a tag from their file id. Google-native documents and shortcuts
    carry no bytes and are skipped, as are, in copy mode, files Drive does not
    let the account download; each is counted. Reads only: the reference storage
    backend refuses writes.
    """

    key = "google_drive"
    label = "Google Drive"
    requires_credential = True
    vendor_slug = "google"
    oauth_scopes: ClassVar[tuple[str, ...]] = ("https://www.googleapis.com/auth/drive.readonly",)
    """The Google OAuth scopes this backend reads with; the connected account must grant them."""

    @classmethod
    def browse(
        cls,
        *,
        credential: Any | None = None,
        token: str = "",
    ) -> MountBrowseResult:
        """List My Drive and the shared drives at the top, then a folder's child folders.

        ``credential`` is one the Mount manager admitted for the browsing user.
        """

        client = GoogleDriveClient(credential)
        raw_token = str(token or "").strip()
        if not raw_token:
            my_drive = client.get(MY_DRIVE)
            places = (DriveItem(id=my_drive.id, name="My Drive", mime_type=my_drive.mime_type),)
            shared = sorted(client.shared_drives(), key=lambda drive: (drive.name.casefold(), drive.id))
            return MountBrowseResult(
                location=MountLocation(
                    token="",
                    label=_BROWSE_TOP_LABEL,
                    is_navigable=True,
                    is_mountable=False,
                    blocked_reason="Choose My Drive or a shared drive",
                ),
                parent_token=None,
                entries=tuple(_drive_location(item) for item in (*places, *shared)),
                truncated=False,
                supports_manual_token=False,
            )
        try:
            folder = client.get(raw_token)
        except OSError as error:
            raise ValidationError({"token": "This Google Drive folder is not available to the account."}) from error
        if not folder.is_folder or folder.trashed:
            raise ValidationError({"token": "This Google Drive item is not a folder."})
        children = list(client.children(folder, folders_only=True))
        children.sort(key=lambda child: (child.name.casefold(), child.name, child.id))
        return MountBrowseResult(
            location=_drive_location(folder),
            parent_token=folder.parents[0] if folder.parents else "",
            entries=tuple(_drive_location(child) for child in children),
            truncated=False,
            supports_manual_token=False,
        )

    def __init__(self, integration: Any) -> None:
        """Bind one Mount; the client, root and walk load on first use."""

        super().__init__(integration)
        self._client: GoogleDriveClient | None = None
        self._root: DriveItem | None = None
        self._walk: _DriveWalk | None = None

    @property
    def client(self) -> GoogleDriveClient:
        """Return the Drive client reading with this Mount's attached credential."""

        if self._client is None:
            credential = self.bridge.load_connection_credential()
            if credential is None:
                raise GoogleDriveError("This Google Drive mount has no account attached; reconnect it.")
            self._client = GoogleDriveClient(credential)
        return self._client

    def check_source(self) -> None:
        """Require the configured root to be a folder the account still reads; refusals keep their reason."""

        root_id = str(self.bridge.config.get("root") or "").strip()
        if not root_id:
            raise GoogleDriveError("Choose a Google Drive folder to mount.")
        try:
            root = self.client.get(root_id)
        except OSError as error:
            raise GoogleDriveError("The Google Drive folder is no longer available to the account.") from error
        if not root.is_folder or root.trashed:
            raise GoogleDriveError("The mounted Google Drive item is not a folder.")
        self._root = root

    def source_root(self) -> str:
        """Return the folder's own id, so an alias (``root`` for My Drive) and the id are one root."""

        self.check_source()
        return self._checked_root().id

    def _checked_root(self) -> DriveItem:
        """Return the root folder this instance checked, checking it first if needed."""

        if self._root is None:
            self.check_source()
        assert self._root is not None  # check_source sets it or raises.
        return self._root

    def iter_directories(self) -> Iterator[str]:
        """Yield every folder beneath the root, empty ones included, parents first."""

        yield from self._walked().directories

    def iter_entries(self) -> Iterator[MountEntry]:
        """Yield every file with bytes beneath the root, keyed by its Drive file id."""

        yield from self._walked().entries

    def entry_hash(self, entry: MountEntry) -> str:
        """Use Drive's SHA-256 metadata, so an unchanged-content sync downloads nothing; stream only without it."""

        digest = self._walked().items[entry.storage_key].sha256
        return digest if digest else super().entry_hash(entry)

    def entry_mime(self, entry: MountEntry) -> str:
        """Return the MIME type Drive records for the file."""

        return self._walked().items[entry.storage_key].mime_type or super().entry_mime(entry)

    def open_entry(self, entry: MountEntry) -> BinaryIO:
        """Return a reader of the file's bytes, requested from Drive on first read."""

        return self.client.open_media(entry.storage_key)

    def storage_backend_spec(self) -> tuple[str, dict[str, Any]]:
        """Serve reference bytes from Drive through the Mount whose drive is stored on that backend row.

        The backend needs no configuration: it finds its Mount by its own row.
        """

        # This key belongs to ANGEE_STORAGE_BACKEND_CLASSES, not the Mount registry.
        return "google_drive", {}

    def _walked(self) -> _DriveWalk:
        """Walk the mounted folder once per sync, depth first with siblings in name order.

        A whole shared drive is listed in one paged pass and its tree built from
        each item's parents; any other root is listed folder by folder.
        """

        if self._walk is not None:
            return self._walk
        root = self._checked_root()
        children_of = self._listed_children(root)
        copying = self.bridge.mode == MountMode.COPY
        walk = _DriveWalk()
        seen = {root.id}
        pending: list[tuple[str, DriveItem]] = [("", root)]
        while pending:
            prefix, folder = pending.pop()
            children = children_of(folder)
            unmirrored = {
                child.id
                for child in children
                if child.is_google_native or (copying and not child.is_folder and not child.can_download)
            }
            self.skipped_entries += len(unmirrored)
            files = [child for child in children if not child.is_folder and child.id not in unmirrored]
            for item, name in _sibling_names(files, keep_suffix=True):
                if item.id in seen:
                    continue
                seen.add(item.id)
                walk.items[item.id] = item
                walk.entries.append(
                    MountEntry(
                        path=posixpath.join(prefix, name),
                        size_bytes=item.size,
                        mtime_ns=item.modified_ns,
                        storage_key=item.id,
                    )
                )
            subfolders: list[tuple[str, DriveItem]] = []
            for item, name in _sibling_names((child for child in children if child.is_folder), keep_suffix=False):
                if item.id in seen:
                    continue
                seen.add(item.id)
                path = posixpath.join(prefix, name)
                walk.directories.append(path)
                subfolders.append((path, item))
            pending.extend(reversed(subfolders))
        self._walk = walk
        return walk

    def _listed_children(self, root: DriveItem) -> Callable[[DriveItem], list[DriveItem]]:
        """Return how to list a folder's children: from one listing when the root is a whole shared drive.

        A folder inside a shared drive, or in My Drive, is listed folder by
        folder, so mounting a small folder never lists its whole drive.
        """

        if not root.drive_id or root.id != root.drive_id:
            return lambda folder: list(self.client.children(folder))
        by_parent: dict[str, list[DriveItem]] = {}
        for item in self.client.drive_items(root.drive_id):
            for parent in item.parents:
                by_parent.setdefault(parent, []).append(item)
        return lambda folder: by_parent.get(folder.id, [])


def _sibling_names(items: Iterable[DriveItem], *, keep_suffix: bool) -> list[tuple[DriveItem, str]]:
    """Give each sibling a distinct path segment, deterministically.

    Drive lets one folder hold several items of one name. The earliest created
    keeps the name; each later one is tagged with the start of its file id, so
    the same items always get the same names and a tag never moves to another file.
    """

    groups: dict[str, list[DriveItem]] = {}
    for item in items:
        groups.setdefault(_segment(item.name), []).append(item)
    placed: list[tuple[DriveItem, str]] = []
    for segment, group in sorted(groups.items()):
        first, *later = sorted(group, key=lambda item: (item.created, item.id))
        placed.append((first, segment))
        placed.extend((item, _tagged(segment, item.id, keep_suffix=keep_suffix)) for item in later)
    return placed


def _segment(name: str) -> str:
    """Return a Drive name as one safe path segment: separators replaced, surrounding space dropped."""

    segment = name.replace("/", "_").replace("\\", "_").strip()
    return segment if segment not in {"", ".", ".."} else "_"


def _tagged(segment: str, item_id: str, *, keep_suffix: bool) -> str:
    """Tag ``segment`` with the start of ``item_id``, before a file's extension."""

    tag = f" ({item_id[:_TAG_LENGTH]})"
    suffix = PurePosixPath(segment).suffix if keep_suffix else ""
    stem = segment[: len(segment) - len(suffix)] if suffix else segment
    return f"{stem}{tag}{suffix}"


def _drive_location(item: DriveItem) -> MountLocation:
    """Project one Drive folder into the neutral browser contract."""

    return MountLocation(
        token=item.id,
        label=item.name or item.id,
        is_navigable=True,
        is_mountable=True,
        blocked_reason="",
    )
