"""Tests for Google Drive storage Mounts against a fake Drive v3 API."""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import unquote

import httpx2
import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from django.utils import timezone
from rebac import system_context

from angee.graphql.testing.documents import execute_schema, result_data
from angee.integrate.models import IntegrationLifecycle
from angee.integrate.testing.http import mock_transport
from angee.storage import exceptions as storage_exceptions
from angee.storage import views
from angee.storage_integrate.models import MountMode
from angee.storage_integrate_google_drive.backends import GoogleDriveStorageBackend
from angee.storage_integrate_google_drive.client import FOLDER_MIME, GoogleDriveError
from tests.conftest import (
    Backend,
    Drive,
    ExternalAccount,
    File,
    Folder,
    MimeType,
    Mount,
    OAuthClient,
    Vendor,
    addon_schema,
    create_platform_admin,
)

storage_integrate_schema = importlib.import_module("angee.storage_integrate.schema")

TOKEN = "drive-token"
MY_DRIVE_ID = "0AMyDriveRoot"
DOC_MIME = "application/vnd.google-apps.document"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
TOKEN_ENDPOINT = "https://accounts.example/token"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"


@dataclass
class FakeItem:
    """One file or folder of the fake Drive."""

    id: str
    name: str
    mime: str
    parent: str
    content: bytes = b""
    modified: str = "2026-01-01T00:00:00.000Z"
    created: str = "2026-01-01T00:00:00.000Z"
    drive_id: str = ""
    trashed: bool = False
    checksum: bool = True
    can_download: bool = True
    refused: str = ""

    def resource(self) -> dict[str, Any]:
        """Return the ``files`` resource Drive v3 answers for this item."""

        data: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "mimeType": self.mime,
            "modifiedTime": self.modified,
            "createdTime": self.created,
            "trashed": self.trashed,
            "parents": [self.parent] if self.parent else [],
            "capabilities": {"canDownload": self.can_download},
        }
        if self.drive_id:
            data["driveId"] = self.drive_id
        if not self.mime.startswith("application/vnd.google-apps."):
            data["size"] = str(len(self.content))
            if self.checksum:
                data["sha256Checksum"] = hashlib.sha256(self.content).hexdigest()
        return data


class FakeDrive:
    """An in-memory Google Drive answering the Drive v3 requests a mount sends, two items a page."""

    page_size = 2

    def __init__(self) -> None:
        self.items: dict[str, FakeItem] = {MY_DRIVE_ID: FakeItem(MY_DRIVE_ID, "My Drive", FOLDER_MIME, "")}
        self.shared: list[str] = []
        self.downloads: list[str] = []
        self.listings: list[dict[str, str]] = []
        self.refusal: Callable[[httpx2.Request], httpx2.Response | None] | None = None
        self.token_response = httpx2.Response(400, json={"error": "invalid_grant"})
        self.tokens = {TOKEN}
        self.bearers: list[str] = []

    def folder(self, item_id: str, name: str, parent: str = MY_DRIVE_ID) -> FakeItem:
        """Add a folder under ``parent``, in its drive."""

        item = FakeItem(item_id, name, FOLDER_MIME, parent, drive_id=self.items[parent].drive_id)
        self.items[item_id] = item
        return item

    def file(self, item_id: str, name: str, content: bytes = b"", parent: str = MY_DRIVE_ID, **values: Any) -> FakeItem:
        """Add a file under ``parent``, in its drive."""

        values.setdefault("mime", "text/plain")
        item = FakeItem(item_id, name, parent=parent, content=content, drive_id=self.items[parent].drive_id, **values)
        self.items[item_id] = item
        return item

    def shared_drive(self, drive_id: str, name: str) -> FakeItem:
        """Add a shared drive, whose root folder has the drive's id."""

        item = FakeItem(drive_id, name, FOLDER_MIME, "", drive_id=drive_id)
        self.items[drive_id] = item
        self.shared.append(drive_id)
        return item

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        """Answer one outbound request."""

        if str(request.url) == TOKEN_ENDPOINT:
            return self.token_response
        assert request.url.host == "www.googleapis.com"
        bearer = request.headers["authorization"].removeprefix("Bearer ")
        assert bearer in self.tokens
        self.bearers.append(bearer)
        if self.refusal is not None and (refused := self.refusal(request)) is not None:
            return refused
        params = dict(request.url.params)
        path = request.url.path
        if path == "/drive/v3/drives":
            drives = [{"id": drive, "name": self.items[drive].name} for drive in self.shared]
            return self._page(drives, params, "drives")
        if path == "/drive/v3/files":
            self.listings.append(params)
            if params["q"] == "trashed = false":
                drive_id = params["driveId"]
                listed = [item for item in self.items.values() if item.drive_id == drive_id and item.id != drive_id]
            else:
                match = re.match(r"'([^']+)' in parents and trashed = false", params["q"])
                assert match is not None
                folders_only = "mimeType = " in params["q"]
                listed = [
                    item for item in self.items.values()
                    if item.parent == match.group(1) and (item.mime == FOLDER_MIME or not folders_only)
                ]
            children = sorted((item for item in listed if not item.trashed), key=lambda item: (item.name, item.id))
            return self._page([child.resource() for child in children], params, "files")
        file_id = unquote(path.removeprefix("/drive/v3/files/"))
        item = self.items.get(MY_DRIVE_ID if file_id == "root" else file_id)
        if item is None:
            return httpx2.Response(404, json={"error": {"code": 404, "errors": [{"reason": "notFound"}]}})
        if params.get("alt") == "media":
            if item.refused:
                return httpx2.Response(403, json={"error": {"code": 403, "errors": [{"reason": item.refused}]}})
            self.downloads.append(item.id)
            return httpx2.Response(200, content=item.content)
        return httpx2.Response(200, json=item.resource())

    def _page(self, rows: list[dict[str, Any]], params: dict[str, str], key: str) -> httpx2.Response:
        """Return one page of ``rows``, with the next page's token while more remain."""

        start = int(params.get("pageToken") or 0)
        page: dict[str, Any] = {key: rows[start : start + self.page_size]}
        if start + self.page_size < len(rows):
            page["nextPageToken"] = str(start + self.page_size)
        return httpx2.Response(200, json=page)


def _connect_account(oauth_client: Any, user: Any, external_id: str, **tokens: Any) -> Any:
    """Link a Google account to ``user`` through integrate's connect verb; return its credential."""

    account = ExternalAccount.objects.connect_oauth(
        oauth_client,
        user=user,
        external_id=external_id,
        tokens={"access_token": TOKEN, "token_type": "Bearer", "scope": f"openid email {DRIVE_SCOPE}", **tokens},
        claims={"sub": external_id, "email": f"{external_id}@example.com"},
    )
    return account.credential


@contextmanager
def _drive_environment(tmp_path: Path) -> Iterator[SimpleNamespace]:
    """Seed default storage, the google vendor and client, one connected account and a fake Drive."""

    Backend._storage_cache.clear()
    managed_root = tmp_path / "managed"
    managed_root.mkdir()
    owner = create_platform_admin(username="drive-owner", email="drive-owner@example.com")
    with system_context(reason="test Google Drive mount environment"):
        backend = Backend._base_manager.create(
            slug="managed",
            label="Managed",
            backend_class="local",
            backend_config={"root": str(managed_root), "base_url": "/media/"},
            created_by=owner,
        )
        Drive._base_manager.create(backend=backend, slug="assets", name="Assets", prefix="assets", created_by=owner)
        for mime_type, category, label in (
            ("application/octet-stream", "other", "Binary file"),
            ("text/plain", "document", "Plain text"),
        ):
            MimeType._base_manager.create(mime_type=mime_type, category=category, label=label)
        Vendor._base_manager.create(slug="google", display_name="Google")
        Vendor._base_manager.create(slug="local", display_name="Local")
        google = OAuthClient.objects.create(
            slug="google",
            display_name="Google",
            client_id="google-cid",
            authorize_endpoint="https://accounts.example/auth",
            token_endpoint=TOKEN_ENDPOINT,
        )
    credential = _connect_account(google, owner, "google-owner")
    drive = FakeDrive()
    try:
        with mock_transport(drive) as requests:
            yield SimpleNamespace(
                owner=owner,
                backend=backend,
                managed_root=managed_root,
                tmp_path=tmp_path,
                google=google,
                credential=credential,
                drive=drive,
                requests=requests,
            )
    finally:
        Backend._storage_cache.clear()


@pytest.fixture()
def drive_env(tmp_path: Path, db: None) -> Iterator[SimpleNamespace]:
    """A Google Drive mount environment inside the test's rolled-back transaction."""

    del db
    with _drive_environment(tmp_path) as env:
        yield env


@pytest.fixture()
def queued_mounts(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Capture eager first-sync dispatches at the shared provisioning owner."""

    queued: list[Any] = []
    monkeypatch.setattr(Mount, "request_sync", lambda mount, **_kwargs: queued.append(mount))
    return queued


def _connect(
    env: SimpleNamespace, root: str, *, mode: MountMode = MountMode.REFERENCE, name: str = "Drive", **values: Any
) -> Any:
    """Provision one Google Drive Mount as ``connect_mount`` does."""

    values.setdefault("credential", env.credential)
    return Mount.objects.provision(
        env.owner, backend_class="google_drive", name=name, root=root, mode=mode, **values
    )


def _run_sync(mount: Any) -> int:
    """Run a Mount sync under the same system boundary as its worker."""

    drive = mount.drive
    with system_context(reason="test Google Drive mount sync"):
        result = mount.run_sync(now=timezone.now())
        mount.refresh_from_db()
    mount.drive = drive
    return result


def _rows(drive: Any) -> dict[str, Any]:
    """Return a mount drive's File rows, trashed included, by storage path."""

    with system_context(reason="test Google Drive mount rows"):
        rows = File.objects.filter(drive=drive).select_related("folder", "folder__parent")
        return {row.storage_path: row for row in rows}


def _folders(drive: Any) -> set[str]:
    """Return the names of a mount drive's folders."""

    with system_context(reason="test Google Drive mount folders"):
        return set(Folder.objects.filter(drive=drive).values_list("name", flat=True))


def _details(mount: Any) -> dict[str, Any]:
    """Return the last persisted Mount progress counters."""

    return dict(mount.sync_progress["details"])


def _console_schema() -> Any:
    """Return the console schema carrying the generic ``connect_mount``."""

    return addon_schema(storage_integrate_schema.schemas, "console")


def test_browse_lists_my_drive_and_shared_drives_then_child_folders(
    drive_env: SimpleNamespace, queued_mounts: list[Any]
) -> None:
    """The top lists My Drive and shared drives; a folder lists its child folders and its parent."""

    fake = drive_env.drive
    fake.folder("f-work", "Work")
    fake.folder("f-archive", "archive")
    fake.folder("f-plans", "Plans", parent="f-work")
    fake.file("x-loose", "loose.txt", b"loose")
    fake.shared_drive("0ATeam", "Team")
    fake.shared_drive("0AArt", "Art")

    top = Mount.objects.browse_source(drive_env.owner, "google_drive", credential=drive_env.credential)
    assert top.location.token == ""
    assert not top.location.is_mountable
    assert [(entry.token, entry.label) for entry in top.entries] == [
        (MY_DRIVE_ID, "My Drive"),
        ("0AArt", "Art"),
        ("0ATeam", "Team"),
    ]
    assert not top.supports_manual_token

    my_drive = Mount.objects.browse_source(
        drive_env.owner, "google_drive", credential=drive_env.credential, token=MY_DRIVE_ID
    )
    assert my_drive.location.is_mountable
    assert my_drive.parent_token == ""
    assert [entry.label for entry in my_drive.entries] == ["archive", "Work"]

    _connect(drive_env, "f-work", name="Work")
    work = Mount.objects.browse_source(drive_env.owner, "google_drive", credential=drive_env.credential, token="f-work")
    assert work.parent_token == MY_DRIVE_ID
    assert work.location.blocked_reason == "Already mounted"
    assert [entry.token for entry in work.entries] == ["f-plans"]

    with pytest.raises(ValidationError) as missing:
        Mount.objects.browse_source(drive_env.owner, "google_drive", credential=drive_env.credential, token="gone")
    assert set(missing.value.message_dict) == {"token"}
    with pytest.raises(ValidationError) as anonymous:
        Mount.objects.browse_source(drive_env.owner, "google_drive")
    assert set(anonymous.value.message_dict) == {"credential"}


def test_reference_mount_indexes_drive_checksums_streams_on_open_and_refuses_writes(
    drive_env: SimpleNamespace, queued_mounts: list[Any]
) -> None:
    """Reference mode indexes by file id with Drive's SHA-256, downloads on open, and stays read-only."""

    fake = drive_env.drive
    fake.folder("f-projects", "Projects")
    fake.folder("f-specs", "Specs", parent="f-projects")
    fake.folder("f-empty", "Empty", parent="f-projects")
    fake.file("x-readme", "readme.txt", b"read me", parent="f-projects")
    fake.file("x-spec", "spec.txt", b"the spec", parent="f-specs")
    fake.file("x-doc", "Plan", parent="f-projects", mime=DOC_MIME)
    fake.file("x-link", "Shortcut", parent="f-specs", mime=SHORTCUT_MIME)

    mount = _connect(drive_env, "f-projects", name="Projects")
    assert queued_mounts == [mount]
    assert mount.lifecycle == IntegrationLifecycle.CONNECTED
    assert mount.credential_id == drive_env.credential.pk
    assert mount.account_id == drive_env.credential.external_account_id
    with system_context(reason="test Google Drive mount provisioning"):
        assert mount.vendor.slug == "google"
        assert mount.drive.backend.backend_class == "google_drive"
        assert mount.drive.backend.backend_config == {}

    assert _run_sync(mount) == 2
    assert fake.downloads == []
    rows = _rows(mount.drive)
    assert set(rows) == {"x-readme", "x-spec"}
    assert rows["x-readme"].filename == "readme.txt"
    assert rows["x-readme"].folder_id is None
    assert rows["x-spec"].folder.name == "Specs"
    assert rows["x-spec"].content_hash == hashlib.sha256(b"the spec").hexdigest()
    assert _folders(mount.drive) == {"Specs", "Empty"}
    assert _details(mount)["skipped"] == 2

    with system_context(reason="test Google Drive mount open"):
        stream = rows["x-spec"].open_stream()
        try:
            assert stream.read() == b"the spec"
        finally:
            stream.close()
        storage = mount.drive.storage
        assert storage.exists("x-spec")
        assert not storage.exists("gone")
        assert storage.size("x-readme") == len(b"read me")
    assert fake.downloads == ["x-spec"]

    with pytest.raises(storage_exceptions.UploadConflict), system_context(reason="test read-only Drive"):
        File.objects.draft(filename="new.txt", drive_id=str(mount.drive.public_id))
    with pytest.raises(OSError):
        storage.save("new.txt", b"bytes")

    assert _run_sync(mount) == 0
    assert _details(mount)["unchanged"] == 2
    assert fake.downloads == ["x-spec"]


def test_reference_sync_keeps_identity_across_duplicates_renames_moves_and_removals(
    drive_env: SimpleNamespace, queued_mounts: list[Any]
) -> None:
    """Same-named siblings index apart; a rename or a move keeps the row; trash and removal trash it."""

    fake = drive_env.drive
    fake.folder("f-root", "Root")
    fake.folder("f-sub", "Sub", parent="f-root")
    fake.file("x-first", "notes.txt", b"first notes", parent="f-root", created="2026-01-01T00:00:00.000Z")
    fake.file("x-second", "notes.txt", b"second notes", parent="f-root", created="2026-01-02T00:00:00.000Z")
    fake.file("x-trash", "trash.txt", b"to trash", parent="f-root")
    fake.file("x-remove", "remove.txt", b"to remove", parent="f-root")
    mount = _connect(drive_env, "f-root", name="Identity")

    assert _run_sync(mount) == 4
    before = _rows(mount.drive)
    assert before["x-first"].filename == "notes.txt"
    assert before["x-second"].filename == "notes (x-second).txt"

    fake.items["x-first"].name = "renamed.txt"
    fake.items["x-second"].parent = "f-sub"
    fake.items["x-trash"].trashed = True
    del fake.items["x-remove"]
    assert _run_sync(mount) == 2
    after = _rows(mount.drive)
    assert after["x-first"].pk == before["x-first"].pk
    assert after["x-first"].filename == "renamed.txt"
    assert after["x-second"].pk == before["x-second"].pk
    assert after["x-second"].filename == "notes.txt"
    assert after["x-second"].folder.name == "Sub"
    assert after["x-trash"].is_trashed
    assert after["x-remove"].is_trashed
    assert _details(mount)["trashed"] == 2
    assert fake.downloads == []


def test_copy_mount_copies_into_the_chosen_backend_once(drive_env: SimpleNamespace, queued_mounts: list[Any]) -> None:
    """Copy mode stores bytes in the chosen writable backend; an unchanged re-sync copies nothing."""

    fake = drive_env.drive
    fake.folder("f-copy", "Copy")
    fake.file("x-one", "one.txt", b"one", parent="f-copy")
    fake.file("x-two", "two.txt", b"two", parent="f-copy")
    archive_root = drive_env.tmp_path / "archive"
    archive_root.mkdir()
    with system_context(reason="test Google Drive copy target"):
        archive = Backend._base_manager.create(
            slug="archive",
            label="Archive",
            backend_class="local",
            backend_config={"root": str(archive_root)},
            created_by=drive_env.owner,
        )

    mount = _connect(drive_env, "f-copy", mode=MountMode.COPY, name="Copied", copy_backend=archive)
    assert mount.drive.backend_id == archive.pk
    assert mount.drive.prefix == "mounts/copied"
    assert _run_sync(mount) == 2
    assert sorted(fake.downloads) == ["x-one", "x-two"]
    copied = {row.filename: row for row in _rows(mount.drive).values()}
    assert (archive_root / copied["one.txt"].storage_path).read_bytes() == b"one"
    assert copied["two.txt"].metadata["mount"]["source_path"] == "two.txt"

    assert _run_sync(mount) == 0
    assert _details(mount)["unchanged"] == 2
    assert sorted(fake.downloads) == ["x-one", "x-two"]


def test_shared_drive_mount_reads_its_own_corpus(drive_env: SimpleNamespace, queued_mounts: list[Any]) -> None:
    """A shared drive mounts like My Drive, listed through its own drive corpus."""

    fake = drive_env.drive
    fake.shared_drive("0ATeam", "Team")
    fake.folder("f-team-docs", "Docs", parent="0ATeam")
    fake.file("x-team", "team.txt", b"team", parent="f-team-docs")

    mount = _connect(drive_env, "0ATeam", name="Team")
    assert _run_sync(mount) == 1
    assert _rows(mount.drive)["x-team"].folder.name == "Docs"
    [listing] = fake.listings
    assert listing["q"] == "trashed = false"
    assert listing["corpora"] == "drive"
    assert listing["driveId"] == "0ATeam"
    assert listing["supportsAllDrives"] == "true"
    assert listing["includeItemsFromAllDrives"] == "true"

    fake.listings.clear()
    folder = _connect(drive_env, "f-team-docs", name="Team docs")
    assert _run_sync(folder) == 1
    [per_folder] = fake.listings
    assert per_folder["q"] == "'f-team-docs' in parents and trashed = false"
    assert per_folder["driveId"] == "0ATeam"


def test_a_file_without_a_drive_checksum_is_hashed_from_one_download(
    drive_env: SimpleNamespace, queued_mounts: list[Any]
) -> None:
    """Without Drive's SHA-256 the reference sync streams the file once to hash it."""

    fake = drive_env.drive
    fake.folder("f-legacy", "Legacy")
    fake.file("x-legacy", "legacy.txt", b"no checksum", parent="f-legacy", checksum=False)
    mount = _connect(drive_env, "f-legacy", name="Legacy")

    assert _run_sync(mount) == 1
    assert fake.downloads == ["x-legacy"]
    assert _rows(mount.drive)["x-legacy"].content_hash == hashlib.sha256(b"no checksum").hexdigest()
    assert _run_sync(mount) == 0
    assert fake.downloads == ["x-legacy"]


def test_rate_limit_fails_the_sync_as_retryable_without_trashing(
    drive_env: SimpleNamespace, queued_mounts: list[Any]
) -> None:
    """A Drive rate limit aborts the walk as a transient failure; nothing indexed is trashed."""

    fake = drive_env.drive
    fake.folder("f-busy", "Busy")
    fake.file("x-kept", "kept.txt", b"kept", parent="f-busy")
    mount = _connect(drive_env, "f-busy", name="Busy")
    assert _run_sync(mount) == 1

    def rate_limited(request: httpx2.Request) -> httpx2.Response | None:
        if request.url.path != "/drive/v3/files":
            return None
        return httpx2.Response(403, json={"error": {"code": 403, "errors": [{"reason": "userRateLimitExceeded"}]}})

    fake.refusal = rate_limited
    with pytest.raises(GoogleDriveError) as refused:
        _run_sync(mount)
    assert refused.value.transient
    mount.refresh_from_db()
    assert mount.sync_stage == mount.SyncStage.FAILED
    assert mount.sync_error == "Google Drive is rate limiting this account; try the sync again later."
    assert not _rows(mount.drive)["x-kept"].is_trashed


@pytest.mark.django_db(transaction=True)
def test_a_revoked_grant_renews_once_then_asks_for_reconnect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A token Google rejects is renewed once through the credential; a refused renewal requires reconnect."""

    monkeypatch.setattr(Mount, "request_sync", lambda mount, **_kwargs: None)
    with _drive_environment(tmp_path) as env:
        credential = _connect_account(env.google, env.owner, "google-owner", refresh_token="refresh")
        fake = env.drive
        fake.folder("f-revoked", "Revoked")
        mount = _connect(env, "f-revoked", name="Revoked", credential=credential)
        fake.refusal = lambda request: httpx2.Response(401, json={"error": {"code": 401, "status": "UNAUTHENTICATED"}})

        with pytest.raises(GoogleDriveError) as refused:
            _run_sync(mount)
        assert not refused.value.transient
        assert [str(request.url) for request in env.requests].count(TOKEN_ENDPOINT) == 1
        with system_context(reason="test revoked Google grant"):
            credential.refresh_from_db()
            assert credential.is_reconnect_required
            assert Mount.objects.get(pk=mount.pk).is_reconnect_required


def test_connect_requires_one_of_the_users_google_accounts_and_a_mountable_folder(
    drive_env: SimpleNamespace, queued_mounts: list[Any]
) -> None:
    """Provisioning refuses a missing, foreign or non-Google account, a non-folder and a mounted folder."""

    fake = drive_env.drive
    fake.folder("f-once", "Once")
    fake.file("x-file", "file.txt", b"file")
    stranger = get_user_model().objects.create_user(username="drive-stranger", email="drive-stranger@example.com")
    foreign = _connect_account(drive_env.google, stranger, "google-stranger")
    with system_context(reason="test non-Google OAuth client"):
        other = OAuthClient.objects.create(
            slug="other", display_name="Other", client_id="other-cid",
            authorize_endpoint="https://other.example/auth", token_endpoint="https://other.example/token",
        )
    not_google = _connect_account(other, drive_env.owner, "other-owner")

    for credential in (None, foreign, not_google):
        with pytest.raises(ValidationError) as refused:
            _connect(drive_env, "f-once", credential=credential)
        assert set(refused.value.message_dict) == {"credential"}
    for root in ("x-file", "gone"):
        with pytest.raises(ValidationError) as unusable:
            _connect(drive_env, root)
        assert set(unusable.value.message_dict) == {"root"}
    _connect(drive_env, "f-once", name="Once")
    with pytest.raises(ValidationError) as again:
        _connect(drive_env, "f-once", name="Again", mode=MountMode.COPY)
    assert again.value.message_dict == {"root": ["This Google Drive source is already mounted."]}

    local_root = drive_env.tmp_path / "local"
    local_root.mkdir()
    with pytest.raises(ValidationError) as local:
        Mount.objects.provision(
            drive_env.owner, backend_class="local_folder", name="Local", root=str(local_root),
            mode=MountMode.REFERENCE, credential=drive_env.credential,
        )
    assert set(local.value.message_dict) == {"credential"}
    assert len(queued_mounts) == 1
    assert Mount.objects.provision(
        drive_env.owner, backend_class="local_folder", name="Local", root=str(local_root), mode=MountMode.REFERENCE
    )


def test_graphql_lists_accounts_browses_and_connects_with_the_session_users_account(
    drive_env: SimpleNamespace, queued_mounts: list[Any]
) -> None:
    """The console offers the user's own Google accounts, browses with one and connects; others are denied."""

    fake = drive_env.drive
    fake.folder("f-gql", "GraphQL")
    schema = _console_schema()
    admin = drive_env.owner
    other_admin = create_platform_admin(username="drive-other-admin", email="drive-other-admin@example.com")

    accounts = result_data(
        execute_schema(
            schema,
            'query { mount_backend(backend_class: "google_drive") '
            "{ requires_credential oauth_client accounts { id display_name } } }",
            user=admin,
        )
    )["mount_backend"]
    assert accounts["requires_credential"] is True
    assert accounts["oauth_client"] == str(drive_env.google.public_id)
    assert [account["id"] for account in accounts["accounts"]] == [str(drive_env.credential.public_id)]
    others = result_data(
        execute_schema(
            schema,
            'query { mount_backend(backend_class: "google_drive") { accounts { id } } }',
            user=other_admin,
        )
    )["mount_backend"]
    assert others["accounts"] == []

    browse = """
        query($credential: ID) {
          browse_mount_source(backend_class: "google_drive", credential_id: $credential, token: "") {
            entries { token label }
          }
        }
    """
    listed = result_data(
        execute_schema(schema, browse, {"credential": str(drive_env.credential.public_id)}, user=admin)
    )["browse_mount_source"]
    assert listed["entries"] == [{"token": MY_DRIVE_ID, "label": "My Drive"}]
    own_account = {"credential": str(drive_env.credential.public_id)}
    assert execute_schema(schema, browse, own_account, user=other_admin).errors is not None

    connect = """
        mutation($credential: ID!, $root: String!) {
          connect_mount(
            backend_class: "google_drive", name: "Console drive", credential_id: $credential, source_root: $root,
            mode: REFERENCE
          ) {
            id mode lifecycle
          }
        }
    """
    variables = {"credential": str(drive_env.credential.public_id), "root": "f-gql"}
    assert execute_schema(schema, connect, variables, user=other_admin).errors is not None
    connected = result_data(execute_schema(schema, connect, variables, user=admin))["connect_mount"]
    assert connected["mode"] == "REFERENCE"
    assert connected["lifecycle"] == "CONNECTED"
    assert connected["id"] == str(queued_mounts[0].sqid)


def _storage_of(mount: Any) -> Any:
    """Return the storage backend serving a reference mount's drive."""

    with system_context(reason="test Google Drive mount storage"):
        return mount.drive.storage


def _read(storage: Any, key: str) -> bytes:
    """Open and read one stored object."""

    with system_context(reason="test Google Drive mount read"):
        stream = storage.open(key)
        try:
            return stream.read()
        finally:
            stream.close()


def test_serving_reads_with_the_mounts_own_credential_while_it_is_connected(
    drive_env: SimpleNamespace, queued_mounts: list[Any]
) -> None:
    """Bytes are read with the Mount's credential as it is now; a disconnected Mount or a forged backend serves none."""

    fake = drive_env.drive
    fake.folder("f-served", "Served")
    fake.file("x-served", "served.txt", b"served", parent="f-served")
    mount = _connect(drive_env, "f-served", name="Served")
    assert _run_sync(mount) == 1
    storage = _storage_of(mount)

    fake.bearers.clear()
    assert _read(storage, "x-served") == b"served"
    assert set(fake.bearers) == {TOKEN}

    fake.tokens.add("rotated-token")
    drive_env.credential.update_material(access_token="rotated-token")
    fake.bearers.clear()
    assert _read(storage, "x-served") == b"served"
    assert set(fake.bearers) == {"rotated-token"}

    stranger = get_user_model().objects.create_user(username="drive-lender", email="drive-lender@example.com")
    fake.tokens.add("lender-token")
    lender = _connect_account(drive_env.google, stranger, "google-lender", access_token="lender-token")
    with system_context(reason="test Google Drive mount re-pointed"):
        mount.attach_credential(lender)
    fake.bearers.clear()
    assert _read(storage, "x-served") == b"served"
    assert set(fake.bearers) == {"lender-token"}

    with system_context(reason="test Google Drive forged backends"):
        genuine = mount.drive.backend
        forged = Backend._base_manager.create(
            slug="forged", label="Forged", backend_class="google_drive", backend_config={},
            created_by=drive_env.owner,
        )
        twin = Backend._base_manager.create(
            slug="twin", label="Twin", backend_class="google_drive", backend_config=genuine.backend_config,
            created_by=drive_env.owner,
        )
        for imposter in (forged.storage, twin.storage, GoogleDriveStorageBackend()):
            with pytest.raises(PermissionError):
                _read(imposter, "x-served")
    assert _read(storage, "x-served") == b"served"

    with system_context(reason="test Google Drive mount disconnected"):
        mount.disconnect()
    with pytest.raises(PermissionError):
        _read(storage, "x-served")


async def _streamed_body(response: Any) -> bytes:
    """Collect a download's asynchronously streamed body."""

    return b"".join([block async for block in response.streaming_content])


def test_a_refused_download_answers_a_clean_status(drive_env: SimpleNamespace, queued_mounts: list[Any]) -> None:
    """The download view answers a trashed file 404, a Drive refusal 502 and a disconnected mount 403, never 500."""

    fake = drive_env.drive
    fake.folder("f-download", "Download")
    fake.file("x-fine", "fine.txt", b"fine", parent="f-download")
    fake.file("x-binned", "binned.txt", b"binned", parent="f-download")
    fake.file("x-blocked", "blocked.txt", b"blocked", parent="f-download")
    mount = _connect(drive_env, "f-download", name="Download")
    assert _run_sync(mount) == 3
    rows = _rows(mount.drive)

    def download(key: str) -> Any:
        token = rows[key].issue_download_token(drive_env.owner)
        return views.download(RequestFactory().get(f"/storage/download/{key}?token={token}"), rows[key].filename)

    fine = download("x-fine")
    assert fine.status_code == 200
    assert async_to_sync(_streamed_body)(fine) == b"fine"
    fake.items["x-binned"].trashed = True
    fake.items["x-blocked"].refused = "cannotDownloadFile"
    for key, status, code in (("x-binned", 404, "not_found"), ("x-blocked", 502, "unavailable")):
        refused = download(key)
        assert (refused.status_code, json.loads(refused.content)["code"]) == (status, code)

    def unreachable_host(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("Google is unreachable.", request=request)

    fake.refusal = unreachable_host
    unreachable = download("x-fine")
    assert (unreachable.status_code, json.loads(unreachable.content)["code"]) == (502, "unavailable")
    fake.refusal = None

    with system_context(reason="test Google Drive download of a disconnected mount"):
        mount.disconnect()
    denied = download("x-fine")
    assert (denied.status_code, json.loads(denied.content)["code"]) == (403, "denied")


def test_serving_opens_eagerly_and_refuses_a_trashed_or_changed_file(
    drive_env: SimpleNamespace, queued_mounts: list[Any]
) -> None:
    """Opening sends the media request at once and refuses what the last sync did not index."""

    fake = drive_env.drive
    fake.folder("f-open", "Open")
    fake.file("x-eager", "eager.txt", b"eager", parent="f-open")
    fake.file("x-trashed", "trashed.txt", b"trashed", parent="f-open")
    fake.file("x-changed", "changed.txt", b"changed", parent="f-open")
    fake.file("x-resized", "resized.txt", b"no checksum", parent="f-open", checksum=False)
    mount = _connect(drive_env, "f-open", name="Open")
    assert _run_sync(mount) == 4
    fake.downloads.clear()
    storage = _storage_of(mount)

    with system_context(reason="test Google Drive eager open"):
        stream = storage.open("x-eager")
        assert fake.downloads == ["x-eager"]
        stream.close()

    fake.items["x-trashed"].trashed = True
    fake.items["x-changed"].content = b"changed since"
    fake.items["x-resized"].content = b"no checksum, longer"
    fake.file("x-unindexed", "later.txt", b"added after the sync", parent="f-open")
    for key, refusal in (
        ("x-trashed", FileNotFoundError),
        ("x-changed", OSError),
        ("x-resized", OSError),
        ("x-unindexed", FileNotFoundError),
        ("x-missing", FileNotFoundError),
    ):
        with pytest.raises(refusal):
            _read(storage, key)
    assert fake.downloads == ["x-eager"]


def test_one_refused_file_is_counted_and_the_rest_sync(drive_env: SimpleNamespace, queued_mounts: list[Any]) -> None:
    """A file Drive will not serve is that file's error; a file it marks undownloadable is skipped in copy mode."""

    fake = drive_env.drive
    fake.folder("f-mixed", "Mixed")
    fake.file("x-ok-1", "one.txt", b"one", parent="f-mixed")
    fake.file("x-ok-2", "two.txt", b"two", parent="f-mixed")
    fake.file("x-refused", "refused.txt", b"refused", parent="f-mixed", refused="cannotDownloadAbusiveFile")
    fake.file("x-locked", "locked.txt", b"locked", parent="f-mixed", can_download=False)
    mount = _connect(drive_env, "f-mixed", mode=MountMode.COPY, name="Mixed")

    assert _run_sync(mount) == 2
    assert mount.sync_stage == mount.SyncStage.COMPLETED
    assert _details(mount)["errors"] == 1
    assert _details(mount)["skipped"] == 1
    assert sorted(fake.downloads) == ["x-ok-1", "x-ok-2"]

    fake.folder("f-legacy", "Legacy")
    fake.file("x-plain", "plain.txt", b"plain", parent="f-legacy", checksum=False)
    fake.file("x-unhashable", "unhashable.txt", b"no", parent="f-legacy", checksum=False, refused="cannotDownloadFile")
    reference = _connect(drive_env, "f-legacy", name="Legacy")
    assert _run_sync(reference) == 1
    assert _details(reference)["errors"] == 1
    assert set(_rows(reference.drive)) == {"x-plain"}


def test_a_rate_limit_carries_googles_retry_after(drive_env: SimpleNamespace, queued_mounts: list[Any]) -> None:
    """A 429 keeps Google's Retry-After on the transient failure."""

    fake = drive_env.drive
    fake.folder("f-throttled", "Throttled")
    mount = _connect(drive_env, "f-throttled", name="Throttled")
    fake.refusal = lambda request: httpx2.Response(429, headers={"Retry-After": "120"}, json={"error": {"code": 429}})

    with pytest.raises(GoogleDriveError) as refused:
        _run_sync(mount)
    assert refused.value.transient
    assert refused.value.retry_after == timedelta(seconds=120)


def test_a_source_refusal_keeps_its_reason_in_the_sync_failure(
    drive_env: SimpleNamespace, queued_mounts: list[Any]
) -> None:
    """A mounted folder that disappears fails the sync with Drive's reason, not a generic configuration error."""

    fake = drive_env.drive
    fake.folder("f-gone", "Gone")
    mount = _connect(drive_env, "f-gone", name="Gone")
    del fake.items["f-gone"]

    with pytest.raises(GoogleDriveError):
        _run_sync(mount)
    mount.refresh_from_db()
    assert mount.sync_error == "The Google Drive folder is no longer available to the account."


def test_accounts_without_the_drive_scope_are_neither_offered_nor_admitted(
    drive_env: SimpleNamespace, queued_mounts: list[Any]
) -> None:
    """Only an account whose grant covers the backend's scopes reads its source."""

    drive_env.drive.folder("f-scoped", "Scoped")
    narrow = _connect_account(drive_env.google, drive_env.owner, "google-owner", scope="openid email")
    with system_context(reason="test Google Drive scoped accounts"):
        assert not Mount.objects.connectable_credentials(drive_env.owner, "google_drive").exists()
    with pytest.raises(ValidationError) as refused:
        _connect(drive_env, "f-scoped", credential=narrow)
    assert set(refused.value.message_dict) == {"credential"}


def test_copy_mode_keeps_a_renamed_or_moved_file_by_its_drive_id(
    drive_env: SimpleNamespace, queued_mounts: list[Any]
) -> None:
    """A copy row follows its Drive file through a rename and a move without copying again."""

    fake = drive_env.drive
    fake.folder("f-copies", "Copies")
    fake.folder("f-moved", "Moved", parent="f-copies")
    fake.file("x-copied", "draft.txt", b"copied once", parent="f-copies")
    mount = _connect(drive_env, "f-copies", mode=MountMode.COPY, name="Copies")
    assert _run_sync(mount) == 1
    [before] = _rows(mount.drive).values()

    fake.items["x-copied"].name = "final.txt"
    fake.items["x-copied"].parent = "f-moved"
    assert _run_sync(mount) == 1
    [after] = _rows(mount.drive).values()
    assert after.pk == before.pk
    assert after.filename == "final.txt"
    assert after.folder.name == "Moved"
    assert after.metadata["mount"] == {
        "source_path": "Moved/final.txt",
        "mtime_ns": before.metadata["mount"]["mtime_ns"],
        "storage_key": "x-copied",
    }
    assert _details(mount)["vanished"] == 0
    assert fake.downloads == ["x-copied"]


def test_an_alias_root_is_normalized_before_the_mounted_check(
    drive_env: SimpleNamespace, queued_mounts: list[Any]
) -> None:
    """My Drive mounted as ``root`` is the same root as its id."""

    mount = _connect(drive_env, "root", name="Whole drive")
    assert mount.config["root"] == MY_DRIVE_ID
    with pytest.raises(ValidationError) as again:
        _connect(drive_env, MY_DRIVE_ID, name="Again")
    assert set(again.value.message_dict) == {"root"}
