"""Google Drive API v3, read with one account's OAuth credential over integrate's HTTP owner.

The one owner of the Drive wire protocol for this addon: the Mount backend walks
and reads through it, and the reference storage backend serves bytes through it.
Requests go through the one Google REST transport
(``angee.integrate_google.api.GoogleApi``: integrate's SSRF-pinned HTTP owner, the
credential's authorization, one renew of a rejected token, Google's error
envelope). No Google client library is involved.

Refusals keep their reach. One file's refusal (not found, not downloadable, a
broken download) is an ``OSError`` (:class:`DriveFileError`,
``FileNotFoundError``), which a sync counts against that file and moves past.
An account-wide refusal is a :class:`GoogleDriveError`, as the transport names it
(``GoogleApi.account_error``).
"""

from __future__ import annotations

import contextlib
import io
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, BinaryIO
from urllib.parse import quote, urlencode

import httpx2

from angee.integrate.http import HttpRefusal
from angee.integrate_google.api import GoogleApi, GoogleApiError, bounded_body, error_reason

DRIVE_API = "https://www.googleapis.com/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"
GOOGLE_APPS_MIME_PREFIX = "application/vnd.google-apps."
"""Google-native types (Docs, Sheets, Slides, Forms, Drawings, shortcuts) carry no bytes to mirror."""
MY_DRIVE = "root"
"""Drive's alias for the account's My Drive root folder."""

_FILE_FIELDS = (
    "id,name,mimeType,size,modifiedTime,createdTime,sha256Checksum,parents,driveId,trashed,capabilities(canDownload)"
)
_LIST_FIELDS = f"nextPageToken,files({_FILE_FIELDS})"
_PAGE_SIZE = 1000
_MEDIA_CHUNK_BYTES = 256 * 1024
_FILE_REASONS = {
    "cannotDownloadFile": "Google Drive does not allow downloading this file.",
    "cannotDownloadAbusiveFile": "Google Drive flagged this file as abusive and does not allow downloading it.",
    "downloadQuotaExceeded": "Google Drive has exhausted this file's download quota.",
    "insufficientFilePermissions": "This Google account may not read this file.",
}
"""The reasons that refuse one file, each in this addon's words; any other file refusal names only its status."""
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class GoogleDriveError(GoogleApiError):
    """An account-wide Google Drive refusal whose message is safe to show an operator."""


class DriveFileError(OSError):
    """One Drive file was refused or could not be read; the rest of the drive is unaffected."""


@dataclass(frozen=True)
class DriveItem:
    """The facts of one Drive file or folder a mount reads."""

    id: str
    name: str
    mime_type: str
    size: int = 0
    modified_ns: int = 0
    created: str = ""
    sha256: str = ""
    parents: tuple[str, ...] = ()
    drive_id: str = ""
    trashed: bool = False
    can_download: bool = True

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> DriveItem:
        """Read one ``files`` resource as returned for :data:`_FILE_FIELDS`."""

        capabilities = data.get("capabilities")
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or ""),
            mime_type=str(data.get("mimeType") or ""),
            size=int(data.get("size") or 0),
            modified_ns=_timestamp_ns(str(data.get("modifiedTime") or "")),
            created=str(data.get("createdTime") or ""),
            sha256=str(data.get("sha256Checksum") or ""),
            parents=tuple(str(parent) for parent in data.get("parents") or ()),
            drive_id=str(data.get("driveId") or ""),
            trashed=bool(data.get("trashed")),
            can_download=bool(capabilities.get("canDownload", True)) if isinstance(capabilities, Mapping) else True,
        )

    @property
    def is_folder(self) -> bool:
        """Whether this item is a folder (a shared drive's root is one)."""

        return self.mime_type == FOLDER_MIME

    @property
    def is_google_native(self) -> bool:
        """Whether this is a Google-native document or a shortcut, which has no bytes of its own."""

        return self.mime_type.startswith(GOOGLE_APPS_MIME_PREFIX) and not self.is_folder


class GoogleDriveClient:
    """One Google account's Drive, read with its OAuth credential.

    Which credential may read whose source is the Mount owner's admission
    (``Mount.reads_with``, ``Integration.connectable_credentials``); the client only reads with it.
    """

    def __init__(self, credential: Any) -> None:
        """Bind the credential every request authenticates with, through the Google transport."""

        self.api = GoogleApi(credential, service="Google Drive", grant="Drive", error=GoogleDriveError)

    def get(self, file_id: str) -> DriveItem:
        """Return one file or folder, in My Drive or a shared drive; ``FileNotFoundError`` when absent."""

        return DriveItem.from_api(
            self._get_json(f"files/{quote(file_id, safe='')}", {"fields": _FILE_FIELDS, "supportsAllDrives": "true"})
        )

    def children(self, folder: DriveItem, *, folders_only: bool = False) -> Iterator[DriveItem]:
        """Yield the untrashed children of ``folder``, page by page, from its own corpus."""

        query = f"'{_query_literal(folder.id)}' in parents and trashed = false"
        if folders_only:
            query += f" and mimeType = '{FOLDER_MIME}'"
        corpus = {"corpora": "drive", "driveId": folder.drive_id} if folder.drive_id else {"corpora": "user"}
        params = {
            "q": query,
            "fields": _LIST_FIELDS,
            "pageSize": str(_PAGE_SIZE),
            "orderBy": "name",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
            **corpus,
        }
        yield from (DriveItem.from_api(item) for item in self._pages("files", params, "files"))

    def drive_items(self, drive_id: str) -> Iterator[DriveItem]:
        """Yield every untrashed item of one shared drive in one paged listing of its corpus."""

        params = {
            "q": "trashed = false",
            "fields": _LIST_FIELDS,
            "pageSize": str(_PAGE_SIZE),
            "corpora": "drive",
            "driveId": drive_id,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        yield from (DriveItem.from_api(item) for item in self._pages("files", params, "files"))

    def shared_drives(self) -> Iterator[DriveItem]:
        """Yield the shared drives the account belongs to, each as its root folder (its id is the drive's)."""

        params = {"fields": "nextPageToken,drives(id,name)", "pageSize": "100"}
        for drive in self._pages("drives", params, "drives"):
            drive_id = str(drive.get("id") or "")
            yield DriveItem(id=drive_id, name=str(drive.get("name") or ""), mime_type=FOLDER_MIME, drive_id=drive_id)

    def open_media(self, file_id: str, *, eager: bool = False) -> BinaryIO:
        """Return a reader of one file's bytes.

        Lazily, the request goes out on the first read, so a reader closed unread
        (a copy that deduplicated) downloads nothing. ``eager`` sends it now, so
        a refusal surfaces before a response starts.
        """

        stream = _MediaStream(self, file_id)
        if eager:
            stream.open()
        return io.BufferedReader(stream, buffer_size=_MEDIA_CHUNK_BYTES)

    def media_chunks(self, file_id: str, stack: contextlib.ExitStack) -> Iterator[bytes]:
        """Open ``files.get?alt=media`` inside ``stack`` and return its byte chunks."""

        url = f"{DRIVE_API}/files/{quote(file_id, safe='')}?{urlencode({'alt': 'media', 'supportsAllDrives': 'true'})}"
        response = self.api.stream(url, stack)
        if not response.is_success:
            self._raise_for_status(response, bounded_body(response), per_file=True)
        return response.iter_bytes(chunk_size=_MEDIA_CHUNK_BYTES)

    def _pages(self, path: str, params: Mapping[str, str], items_key: str) -> Iterator[Mapping[str, Any]]:
        """Yield each item of a paged ``list`` method, following ``nextPageToken``."""

        token = ""
        while True:
            page = self._get_json(path, {**params, **({"pageToken": token} if token else {})})
            yield from page.get(items_key) or ()
            token = str(page.get("nextPageToken") or "")
            if not token:
                return

    def _get_json(self, path: str, params: Mapping[str, str]) -> dict[str, Any]:
        """GET one Drive JSON resource through the Google transport."""

        response = self.api.send("GET", f"{DRIVE_API}/{path}?{urlencode(params)}")
        self._raise_for_status(response, response.content, per_file=path.startswith("files/"))
        return self.api.json(response)

    def _raise_for_status(self, response: httpx2.Response, body: bytes, *, per_file: bool) -> None:
        """Map a Drive refusal onto the error of its reach.

        ``per_file`` marks a request about one file, whose refusals other than
        the account-wide ones (``GoogleApi.account_error``) are that file's.
        """

        def own(refusal: HttpRefusal) -> Exception | None:
            if refusal.status == 404:
                return FileNotFoundError("Google Drive item not found.")
            reason = error_reason(refusal.body)
            if refusal.status == 416 or reason in _FILE_REASONS or per_file:
                return DriveFileError(
                    _FILE_REASONS.get(reason, f"Google Drive refused this file (HTTP {refusal.status}).")
                )
            return None

        self.api.raise_for_status(response, body, own=own)


class _MediaStream(io.RawIOBase):
    """A raw reader of one file's media, opened on demand and closed with itself."""

    def __init__(self, client: GoogleDriveClient, file_id: str) -> None:
        """Remember what to read without sending anything yet."""

        super().__init__()
        self._client = client
        self._file_id = file_id
        self._stack = contextlib.ExitStack()
        self._chunks: Iterator[bytes] | None = None
        self._pending = b""

    def readable(self) -> bool:
        """This stream reads."""

        return True

    def open(self) -> None:
        """Send the media request, if it has not gone out yet."""

        if self._chunks is None:
            try:
                self._chunks = self._client.media_chunks(self._file_id, self._stack)
            except httpx2.TransportError as error:
                raise DriveFileError("The Google Drive download could not start.") from error

    def readinto(self, buffer: Any) -> int:
        """Fill ``buffer`` from the media response; a broken transfer is this file's error."""

        self.open()
        assert self._chunks is not None  # open() set it or raised.
        try:
            while not self._pending:
                chunk = next(self._chunks, None)
                if chunk is None:
                    return 0
                self._pending = chunk
        except httpx2.TransportError as error:
            raise DriveFileError("The Google Drive download was interrupted.") from error
        view = memoryview(buffer).cast("B")
        size = min(len(view), len(self._pending))
        view[:size] = self._pending[:size]
        self._pending = self._pending[size:]
        return size

    def close(self) -> None:
        """Close the media response, if one was opened."""

        try:
            self._stack.close()
        finally:
            super().close()


def _timestamp_ns(value: str) -> int:
    """Return an RFC 3339 timestamp as integer nanoseconds since the epoch, or 0."""

    if not value:
        return 0
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return 0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return (moment - _EPOCH) // timedelta(microseconds=1) * 1000


def _query_literal(value: str) -> str:
    """Escape a value for a single-quoted Drive query string."""

    return value.replace("\\", "\\\\").replace("'", "\\'")
