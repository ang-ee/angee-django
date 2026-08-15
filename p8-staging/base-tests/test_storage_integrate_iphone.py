"""Tests for iPhone-backup storage Mount provisioning and byte serving."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import SuspiciousFileOperation
from django.core.management import call_command
from django.db import connection
from django.utils import timezone
from rebac import system_context

from angee.storage_integrate.models import MountMode
from angee.storage_integrate_iphone.connect import create_iphone_backup_mount
from angee.storage_integrate_iphone.mounts import IphoneBackupMountBackend
from tests.conftest import (
    IAM_CONNECTION_TEST_MODELS,
    INTEGRATE_TEST_MODELS,
    STORAGE_INTEGRATE_TEST_MODELS,
    STORAGE_TEST_MODELS,
    Backend,
    Drive,
    File,
    Folder,
    MimeType,
    Mount,
    Vendor,
    _clear_model_tables,
    _create_missing_tables,
    addon_schema,
    execute_schema,
)
from tests.test_integrate_iphone import (
    BASE_MTIME_NS,
    CAMERA_BYTES,
    CAMERA_DOMAIN,
    CAMERA_PATH,
    NOTES_BYTES,
    NOTES_DOMAIN,
    NOTES_PATH,
    build_iphone_backup,
)

storage_integrate_connect = importlib.import_module("angee.storage_integrate.connect")
storage_integrate_iphone_schema = importlib.import_module(
    "angee.storage_integrate_iphone.schema"
)
storage_integrate_schema = importlib.import_module("angee.storage_integrate.schema")

IPHONE_TEST_MODELS = (
    IAM_CONNECTION_TEST_MODELS
    + INTEGRATE_TEST_MODELS
    + STORAGE_TEST_MODELS
    + STORAGE_INTEGRATE_TEST_MODELS
)


@pytest.fixture()
def iphone_tables(transactional_db: Any) -> Iterator[None]:
    """Create the integration, storage, and Mount tables used by this addon."""

    del transactional_db
    Backend._storage_cache.clear()
    created = _create_missing_tables(IPHONE_TEST_MODELS)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        Backend._storage_cache.clear()
        _clear_model_tables(IPHONE_TEST_MODELS)
        if created:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created):
                    schema_editor.delete_model(model)


@pytest.fixture()
def iphone_env(tmp_path: Path, iphone_tables: None) -> SimpleNamespace:
    """Seed the local vendor and managed default drive used by connect flows."""

    del iphone_tables
    managed_root = tmp_path / "managed"
    managed_root.mkdir()
    owner = get_user_model().objects.create_user(
        username="iphone-owner",
        email="iphone-owner@example.com",
    )
    with system_context(reason="test iPhone mount environment"):
        backend = Backend._base_manager.create(
            slug="managed",
            label="Managed",
            backend_class="local",
            backend_config={"root": str(managed_root), "base_url": "/media/"},
            created_by=owner,
        )
        default_drive = Drive._base_manager.create(
            backend=backend,
            slug="assets",
            name="Assets",
            prefix="assets",
            created_by=owner,
        )
        for mime_type, category, label in (
            ("application/octet-stream", "other", "Binary file"),
            ("image/jpeg", "image", "JPEG image"),
            ("text/plain", "document", "Plain text"),
        ):
            MimeType._base_manager.create(
                mime_type=mime_type,
                category=category,
                label=label,
            )
        Vendor._base_manager.create(slug="local", display_name="Local")
    return SimpleNamespace(
        owner=owner,
        backend=backend,
        default_drive=default_drive,
        managed_root=managed_root,
        tmp_path=tmp_path,
    )


@pytest.fixture()
def queued_mounts(monkeypatch: pytest.MonkeyPatch) -> list[Mount]:
    """Capture eager first-sync dispatches at the shared provisioning owner."""

    queued: list[Mount] = []
    monkeypatch.setattr(
        storage_integrate_connect,
        "queue_bridge_sync",
        lambda mount, **_kwargs: queued.append(mount),
    )
    return queued


def _run_sync(mount: Mount) -> int:
    """Run a Mount sync at the same system boundary as its worker."""

    with system_context(reason="test iPhone mount sync"):
        result = mount.run_sync(now=timezone.now())
        mount.refresh_from_db()
    return result


def _files(drive: Drive) -> list[File]:
    """Return every indexed row for one iPhone Mount drive."""

    with system_context(reason="test iPhone mounted files"):
        return list(
            File.objects.filter(drive=drive)
            .select_related("folder", "folder__parent")
            .order_by("filename")
        )


def _console_schema() -> Any:
    """Compose the iPhone mutation onto its storage_integrate dependency."""

    parts = {
        key: list(values)
        for key, values in storage_integrate_schema.schemas["console"].items()
    }
    for key, values in storage_integrate_iphone_schema.schemas["console"].items():
        parts.setdefault(key, []).extend(values)
    return addon_schema({"console": parts}, "console")


@pytest.mark.django_db(transaction=True)
def test_browse_marks_only_manifest_roots_mountable(iphone_env: SimpleNamespace) -> None:
    """The shared local browser guides admins to a concrete device backup root."""

    parent = iphone_env.tmp_path / "backups"
    parent.mkdir()
    build_iphone_backup(parent / "device")
    (parent / "ordinary-folder").mkdir()

    listing = IphoneBackupMountBackend.browse(token=str(parent))
    entries = {entry.label: entry for entry in listing.entries}

    assert not listing.location.is_mountable
    assert listing.location.blocked_reason == "Folder does not contain Manifest.db"
    assert entries["device"].is_mountable
    assert entries["device"].blocked_reason == ""
    assert not entries["ordinary-folder"].is_mountable
    assert entries["ordinary-folder"].blocked_reason == "Folder does not contain Manifest.db"


@pytest.mark.django_db(transaction=True)
def test_reference_mount_indexes_and_serves_bytes_across_threads(
    iphone_env: SimpleNamespace,
    queued_mounts: list[Mount],
) -> None:
    """Reference mode serves logical paths without retaining a thread-bound DB."""

    root = build_iphone_backup(iphone_env.tmp_path / "reference-device")
    mount = create_iphone_backup_mount(
        iphone_env.owner,
        name="Reference iPhone",
        path=str(root),
        mode=MountMode.REFERENCE,
    )

    assert queued_mounts == [mount]
    assert mount.backend_class == "iphone_backup"
    assert mount.drive.backend.backend_class == "iphone_backup"
    assert mount.drive.backend.backend_config == {"root": str(root.resolve())}
    assert mount.drive.prefix == ""
    assert _run_sync(mount) == 2

    rows = _files(mount.drive)
    by_name = {row.filename: row for row in rows}
    image = by_name["IMG_0001.JPG"]
    note = by_name["note.txt"]
    assert image.storage_path == f"{CAMERA_DOMAIN}/{CAMERA_PATH}"
    assert note.storage_path == f"{NOTES_DOMAIN}/{NOTES_PATH}"
    assert image.folder.name == "100APPLE"
    assert image.folder.parent.name == "DCIM"
    assert image.metadata["mount"]["mtime_ns"] == BASE_MTIME_NS

    storage = image.storage
    assert storage.exists(image.storage_path)

    def read_from_worker() -> tuple[int, bytes]:
        with storage.open(image.storage_path) as stream:
            return storage.size(image.storage_path), stream.read()

    with ThreadPoolExecutor(max_workers=1) as executor:
        size, content = executor.submit(read_from_worker).result()
    assert size == len(CAMERA_BYTES)
    assert content == CAMERA_BYTES

    with note.open_stream() as stream:
        assert stream.read() == NOTES_BYTES
    storage.discard(image.storage_path, context="test")
    assert storage.exists(image.storage_path)
    with pytest.raises(OSError, match="read-only"):
        storage.delete(image.storage_path)
    with pytest.raises(ValueError, match="not URL-accessible"):
        storage.url(image.storage_path)
    with pytest.raises(SuspiciousFileOperation):
        storage.exists("../Manifest.db")

    with system_context(reason="test iPhone folder roots"):
        assert Folder.objects.filter(
            drive=mount.drive,
            parent__isnull=True,
            name=CAMERA_DOMAIN,
        ).exists()


@pytest.mark.django_db(transaction=True)
def test_reference_open_entry_uses_the_iterated_blob_map_without_a_query(
    iphone_env: SimpleNamespace,
    queued_mounts: list[Mount],
) -> None:
    """The hashing/open pass never resolves a logical path through Manifest.db."""

    root = build_iphone_backup(iphone_env.tmp_path / "mapped-device")
    mount = create_iphone_backup_mount(
        iphone_env.owner,
        name="Mapped iPhone",
        path=str(root),
        mode=MountMode.REFERENCE,
    )
    del queued_mounts
    backend = cast(IphoneBackupMountBackend, mount.backend)
    entries = list(backend.iter_entries())
    backup = backend.backup
    queries: list[str] = []
    backup._manifest.set_trace_callback(queries.append)

    with backend.open_entry(entries[0]) as stream:
        assert stream.read() == CAMERA_BYTES

    assert backend.backup is backup
    assert queries == []


@pytest.mark.django_db(transaction=True)
def test_copy_mount_ingests_bytes_with_logical_source_metadata(
    iphone_env: SimpleNamespace,
    queued_mounts: list[Mount],
) -> None:
    """Copy mode lands managed bytes while retaining each manifest source path."""

    root = build_iphone_backup(iphone_env.tmp_path / "copy-device")
    mount = create_iphone_backup_mount(
        iphone_env.owner,
        name="Copied iPhone",
        path=str(root),
        mode=MountMode.COPY,
    )

    assert queued_mounts == [mount]
    assert mount.drive.backend_id == iphone_env.backend.pk
    assert mount.drive.prefix == "mounts/copied-iphone"
    assert _run_sync(mount) == 2

    rows = _files(mount.drive)
    sources = {row.metadata["mount"]["source_path"]: row for row in rows}
    image = sources[f"{CAMERA_DOMAIN}/{CAMERA_PATH}"]
    assert image.filename == "IMG_0001.JPG"
    assert image.storage_path != f"{CAMERA_DOMAIN}/{CAMERA_PATH}"
    with image.open_stream() as stream:
        assert stream.read() == CAMERA_BYTES


@pytest.mark.django_db(transaction=True)
def test_connect_iphone_backup_graphql_denies_non_admin(
    iphone_env: SimpleNamespace,
    queued_mounts: list[Mount],
) -> None:
    """Only administrators may provision an iPhone-backup Mount."""

    root = build_iphone_backup(iphone_env.tmp_path / "denied-device")
    reader = get_user_model().objects.create_user(
        username="iphone-reader",
        email="iphone-reader@example.com",
    )
    result = execute_schema(
        _console_schema(),
        """
        mutation($path: String!, $mode: MountMode!) {
          connect_iphone_backup(name: "Denied", path: $path, mode: $mode) { id }
        }
        """,
        {"path": str(root), "mode": "REFERENCE"},
        user=reader,
    )

    assert result.errors is not None
    assert queued_mounts == []
