"""Tests for local-folder Mount provisioning, reconciliation, and GraphQL."""

from __future__ import annotations

import hashlib
import importlib
import os
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.utils import timezone
from rebac import app_settings, system_context
from rebac.roles import grant

from angee.integrate.models import IntegrationLifecycle, IntegrationRuntimeStatus
from angee.storage_integrate.connect import create_local_folder_mount
from angee.storage_integrate.models import MountMode
from angee.storage_integrate.mounts import (
    LocalFolderMountBackend,
    browse_mount_source,
)
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
    make_mount,
    result_data,
)

storage_integrate_connect = importlib.import_module("angee.storage_integrate.connect")
storage_integrate_schema = importlib.import_module("angee.storage_integrate.schema")

MOUNT_TEST_MODELS = (
    IAM_CONNECTION_TEST_MODELS
    + INTEGRATE_TEST_MODELS
    + STORAGE_TEST_MODELS
    + STORAGE_INTEGRATE_TEST_MODELS
)
BASE_MTIME_NS = 1_700_000_000_000_000_000


@pytest.fixture()
def mount_tables(transactional_db: Any) -> Iterator[None]:
    """Create concrete integration, storage, and Mount tables with REBAC wiring."""

    del transactional_db
    created = _create_missing_tables(MOUNT_TEST_MODELS)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        _clear_model_tables(MOUNT_TEST_MODELS)
        if created:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created):
                    schema_editor.delete_model(model)


@pytest.fixture()
def mount_env(tmp_path: Path, mount_tables: None) -> SimpleNamespace:
    """Seed the local vendor and managed default drive used by Mount tests."""

    del mount_tables
    managed_root = tmp_path / "managed"
    managed_root.mkdir()
    owner = get_user_model().objects.create_user(
        username="mount-owner",
        email="mount-owner@example.com",
    )
    with system_context(reason="test mount environment"):
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
        MimeType._base_manager.create(
            mime_type="application/octet-stream",
            category="other",
            label="Binary file",
        )
        MimeType._base_manager.create(
            mime_type="text/plain",
            category="document",
            label="Plain text",
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
def queued_mounts(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Capture eager connect queues without dispatching a worker task."""

    queued: list[Any] = []
    monkeypatch.setattr(
        storage_integrate_connect,
        "queue_bridge_sync",
        lambda mount, **kwargs: queued.append((mount, kwargs)),
    )
    return queued


def _write(path: Path, content: bytes, *, mtime_ns: int) -> None:
    """Write one deterministic external file and pin its nanosecond timestamp."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, ns=(mtime_ns, mtime_ns))


def _connect(
    env: SimpleNamespace,
    root: Path,
    *,
    mode: MountMode,
    name: str,
) -> Mount:
    """Provision one real local-folder Mount through the public use-case."""

    return create_local_folder_mount(
        env.owner,
        name=name,
        path=str(root),
        mode=mode,
    )


def _run_sync(mount: Mount, *, now: Any | None = None) -> int:
    """Run a Mount sync under the same system boundary as its worker."""

    drive = mount.drive
    with system_context(reason="test mount sync"):
        result = mount.run_sync(now=now or timezone.now())
        mount.refresh_from_db()
    mount.drive = drive
    return result


def _files(drive: Drive) -> list[File]:
    """Return all rows for a mount drive, including soft-trashed files."""

    with system_context(reason="test mount file read"):
        return list(
            File.objects.filter(drive=drive)
            .select_related("folder", "folder__parent")
            .order_by("storage_path")
        )


def _file_map(drive: Drive) -> dict[str, File]:
    """Return mount-drive rows keyed by their current storage path."""

    return {row.storage_path: row for row in _files(drive)}


def _details(mount: Mount) -> dict[str, Any]:
    """Return the last persisted Mount progress counters."""

    return dict(mount.sync_progress["details"])


def _assert_path_error(error: pytest.ExceptionInfo[ValidationError], text: str) -> None:
    """Assert a connect refusal is keyed to the path input."""

    assert set(error.value.message_dict) == {"path"}
    assert text in " ".join(error.value.message_dict["path"])


@pytest.mark.django_db(transaction=True)
def test_connect_reference_provisions_read_only_backend_and_owned_drive(
    mount_env: SimpleNamespace,
    queued_mounts: list[Any],
) -> None:
    """Reference mode derives its Backend config from ``storage_backend_spec``."""

    root = mount_env.tmp_path / "reference"
    root.mkdir()
    mount = _connect(mount_env, root, mode=MountMode.REFERENCE, name="Reference docs")

    assert mount.mode == MountMode.REFERENCE
    assert mount.lifecycle == IntegrationLifecycle.CONNECTED
    assert mount.owner_id == mount_env.owner.pk
    assert mount.created_by_id == mount_env.owner.pk
    assert mount.drive.created_by_id == mount_env.owner.pk
    assert mount.drive.prefix == ""
    assert mount.drive.backend.backend_class == "local_folder"
    assert mount.drive.backend.backend_config == {"root": str(root.resolve())}
    assert mount.drive.backend.created_by_id == mount_env.owner.pk
    assert mount.backend.storage_backend_spec() == (
        "local_folder",
        {"root": str(root.resolve())},
    )
    assert queued_mounts == [(mount, {})]


@pytest.mark.django_db(transaction=True)
def test_connect_copy_reuses_managed_backend_and_stamps_drive_owner(
    mount_env: SimpleNamespace,
    queued_mounts: list[Any],
) -> None:
    """Copy mode creates a dedicated prefixed Drive on managed storage."""

    root = mount_env.tmp_path / "copy"
    root.mkdir()
    mount = _connect(mount_env, root, mode=MountMode.COPY, name="Copied docs")

    assert mount.mode == MountMode.COPY
    assert mount.drive.backend_id == mount_env.backend.pk
    assert mount.drive.prefix == "mounts/copied-docs"
    assert mount.drive.created_by_id == mount_env.owner.pk
    assert mount.owner_id == mount_env.owner.pk
    assert Backend._base_manager.count() == 1
    assert queued_mounts == [(mount, {})]


@pytest.mark.django_db(transaction=True)
def test_connect_refusals_are_field_keyed_for_unsafe_or_duplicate_roots(
    mount_env: SimpleNamespace,
    queued_mounts: list[Any],
    settings: Any,
) -> None:
    """Every local-root validation failure reports against the ``path`` field."""

    duplicate = mount_env.tmp_path / "duplicate"
    duplicate.mkdir()
    _connect(mount_env, duplicate, mode=MountMode.REFERENCE, name="First")
    assert len(queued_mounts) == 1
    with pytest.raises(ValidationError) as duplicate_error:
        _connect(mount_env, duplicate, mode=MountMode.COPY, name="Again")
    _assert_path_error(duplicate_error, "already mounted")

    with pytest.raises(ValidationError) as relative_error:
        create_local_folder_mount(
            mount_env.owner,
            name="Relative",
            path="relative/folder",
            mode=MountMode.REFERENCE,
        )
    _assert_path_error(relative_error, "absolute")

    media_root = mount_env.tmp_path / "media"
    media_source = media_root / "source"
    media_source.mkdir(parents=True)
    settings.MEDIA_ROOT = media_root
    with pytest.raises(ValidationError) as media_error:
        _connect(mount_env, media_source, mode=MountMode.REFERENCE, name="Media")
    _assert_path_error(media_error, "MEDIA_ROOT")

    data_root = mount_env.tmp_path / "data"
    data_source = data_root / "source"
    data_source.mkdir(parents=True)
    settings.MEDIA_ROOT = ""
    settings.ANGEE_DATA_DIR = data_root
    with pytest.raises(ValidationError) as data_error:
        _connect(mount_env, data_source, mode=MountMode.REFERENCE, name="Data")
    _assert_path_error(data_error, "ANGEE_DATA_DIR")


@pytest.mark.django_db(transaction=True)
def test_browse_mount_source_dispatches_local_neutral_locations(
    mount_env: SimpleNamespace,
    queued_mounts: list[Any],
    monkeypatch: pytest.MonkeyPatch,
    settings: Any,
) -> None:
    """The neutral browser dispatches local directories and explains blocked roots."""

    home = mount_env.tmp_path / "home"
    home.mkdir()
    mounted = home / "Mounted"
    mounted.mkdir()
    protected = home / ".Library"
    protected.mkdir()
    (home / "file.txt").write_text("not a directory")
    (home / "linked").symlink_to(protected, target_is_directory=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    settings.MEDIA_ROOT = protected
    _connect(mount_env, mounted, mode=MountMode.REFERENCE, name="Mounted")
    assert len(queued_mounts) == 1

    direct = LocalFolderMountBackend.browse()
    listing = browse_mount_source("local_folder")

    assert direct == listing
    assert listing.location.token == str(home.resolve())
    assert listing.location.label == "home"
    assert listing.parent_token == str(home.parent.resolve())
    assert listing.location.is_navigable
    assert listing.location.is_mountable
    assert listing.location.blocked_reason == ""
    assert not listing.truncated
    assert listing.supports_manual_token
    assert [entry.label for entry in listing.entries] == [".Library", "Mounted"]
    entries = {entry.label: entry for entry in listing.entries}
    assert entries["Mounted"].token == str(mounted.resolve())
    assert entries["Mounted"].is_navigable
    assert entries["Mounted"].blocked_reason == "Already mounted"
    assert not entries["Mounted"].is_mountable
    assert "MEDIA_ROOT" in entries[".Library"].blocked_reason
    assert not entries[".Library"].is_mountable

    with pytest.raises(ValidationError) as unknown_backend:
        browse_mount_source("missing")
    assert "backend_class" in unknown_backend.value.message_dict


@pytest.mark.django_db(transaction=True)
def test_reference_sync_creates_updates_prunes_restores_and_mirrors_folders(
    mount_env: SimpleNamespace,
    queued_mounts: list[Any],
) -> None:
    """Reference reconciliation preserves row identity across the full lifecycle."""

    root = mount_env.tmp_path / "reference-lifecycle"
    root.mkdir()
    _write(root / "top.txt", b"top", mtime_ns=BASE_MTIME_NS)
    _write(root / "nested" / "deep" / "child.txt", b"child", mtime_ns=BASE_MTIME_NS + 1)
    mount = _connect(mount_env, root, mode=MountMode.REFERENCE, name="Lifecycle")
    assert queued_mounts == [(mount, {})]

    assert _run_sync(mount) == 2
    initial = _file_map(mount.drive)
    top_pk = initial["top.txt"].pk
    child_pk = initial["nested/deep/child.txt"].pk
    assert initial["top.txt"].folder_id is None
    assert initial["nested/deep/child.txt"].folder.name == "deep"
    assert initial["nested/deep/child.txt"].folder.parent.name == "nested"
    with system_context(reason="test mount folders"):
        assert set(Folder.objects.filter(drive=mount.drive).values_list("name", flat=True)) == {
            "nested",
            "deep",
        }

    _write(root / "top.txt", b"top updated", mtime_ns=BASE_MTIME_NS + 10)
    (root / "nested" / "deep" / "child.txt").unlink()
    _write(root / "nested" / "new.txt", b"new", mtime_ns=BASE_MTIME_NS + 11)
    assert _run_sync(mount) == 2
    changed = _file_map(mount.drive)
    assert changed["top.txt"].pk == top_pk
    assert changed["top.txt"].content_hash == hashlib.sha256(b"top updated").hexdigest()
    assert changed["nested/deep/child.txt"].pk == child_pk
    assert changed["nested/deep/child.txt"].is_trashed
    new_row = changed["nested/new.txt"]
    assert not new_row.is_trashed

    (root / "nested" / "new.txt").unlink()
    assert _run_sync(mount) == 0
    assert _file_map(mount.drive)["nested/new.txt"].is_trashed
    _write(root / "nested" / "new.txt", b"new", mtime_ns=BASE_MTIME_NS + 12)
    assert _run_sync(mount) == 1
    restored = _file_map(mount.drive)["nested/new.txt"]
    assert restored.pk == new_row.pk
    assert not restored.is_trashed


@pytest.mark.django_db(transaction=True)
def test_reference_sync_mirrors_and_prunes_empty_source_directories(
    mount_env: SimpleNamespace,
    queued_mounts: list[Any],
) -> None:
    """Empty source directories mirror into the tree, then prune when removed."""

    root = mount_env.tmp_path / "empty-dirs"
    root.mkdir()
    _write(root / "full" / "doc.txt", b"doc", mtime_ns=BASE_MTIME_NS)
    (root / "empty" / "deeper").mkdir(parents=True)
    mount = _connect(mount_env, root, mode=MountMode.REFERENCE, name="Empty dirs")
    del queued_mounts

    assert _run_sync(mount) == 1
    with system_context(reason="test mount empty folders"):
        assert set(Folder.objects.filter(drive=mount.drive).values_list("name", flat=True)) == {
            "full",
            "empty",
            "deeper",
        }

    (root / "empty" / "deeper").rmdir()
    (root / "empty").rmdir()
    assert _run_sync(mount) == 0
    with system_context(reason="test mount empty folders pruned"):
        assert set(Folder.objects.filter(drive=mount.drive).values_list("name", flat=True)) == {"full"}
    assert _file_map(mount.drive)["full/doc.txt"].folder.name == "full"


@pytest.mark.django_db(transaction=True)
def test_reference_sync_uses_mtime_ns_freshness_before_hashing(
    mount_env: SimpleNamespace,
    queued_mounts: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Equal size/mtime rows skip hashing; a timestamp change retries the entry."""

    root = mount_env.tmp_path / "freshness"
    root.mkdir()
    target = root / "same.txt"
    _write(target, b"same", mtime_ns=BASE_MTIME_NS)
    mount = _connect(mount_env, root, mode=MountMode.REFERENCE, name="Freshness")
    del queued_mounts
    assert _run_sync(mount) == 1

    hashes: list[str] = []
    original = LocalFolderMountBackend.entry_hash

    def observe(backend: LocalFolderMountBackend, entry: Any) -> str:
        hashes.append(entry.path)
        return original(backend, entry)

    monkeypatch.setattr(LocalFolderMountBackend, "entry_hash", observe)
    assert _run_sync(mount) == 0
    assert hashes == []
    assert _details(mount)["unchanged"] == 1

    os.utime(target, ns=(BASE_MTIME_NS + 1, BASE_MTIME_NS + 1))
    assert _run_sync(mount) == 1
    assert hashes == ["same.txt"]


@pytest.mark.django_db(transaction=True)
def test_reference_rename_converges_across_two_syncs(
    mount_env: SimpleNamespace,
    queued_mounts: list[Any],
) -> None:
    """A live hash collision trashes the old path, then repoints it next run."""

    root = mount_env.tmp_path / "rename"
    root.mkdir()
    old_path = root / "old.txt"
    new_path = root / "new.txt"
    _write(old_path, b"rename me", mtime_ns=BASE_MTIME_NS)
    mount = _connect(mount_env, root, mode=MountMode.REFERENCE, name="Rename")
    del queued_mounts
    assert _run_sync(mount) == 1
    original = _file_map(mount.drive)["old.txt"]

    old_path.rename(new_path)
    assert _run_sync(mount) == 0
    assert _details(mount)["duplicates"] == 1
    assert _details(mount)["trashed"] == 1
    assert _file_map(mount.drive)["old.txt"].is_trashed

    assert _run_sync(mount) == 1
    converged = _file_map(mount.drive)["new.txt"]
    assert converged.pk == original.pk
    assert not converged.is_trashed


@pytest.mark.django_db(transaction=True)
def test_reference_duplicate_content_is_counted_and_retried_each_sync(
    mount_env: SimpleNamespace,
    queued_mounts: list[Any],
) -> None:
    """The skipped second path remains visible as one duplicate on every pass."""

    root = mount_env.tmp_path / "duplicates"
    root.mkdir()
    _write(root / "a.txt", b"same", mtime_ns=BASE_MTIME_NS)
    _write(root / "b.txt", b"same", mtime_ns=BASE_MTIME_NS + 1)
    mount = _connect(mount_env, root, mode=MountMode.REFERENCE, name="Duplicates")
    del queued_mounts

    assert _run_sync(mount) == 1
    assert _details(mount) == {
        "backend": "local_folder",
        "mode": "reference",
        "changed": 1,
        "unchanged": 0,
        "duplicates": 1,
        "errors": 0,
        "trashed": 0,
        "vanished": 0,
        "scanned": 2,
    }
    assert len(_files(mount.drive)) == 1

    assert _run_sync(mount) == 0
    assert _details(mount)["unchanged"] == 1
    assert _details(mount)["duplicates"] == 1
    assert _details(mount)["scanned"] == 2


@pytest.mark.django_db(transaction=True)
def test_reference_vanished_file_reappears_at_new_path_in_one_sync(
    mount_env: SimpleNamespace,
    queued_mounts: list[Any],
) -> None:
    """Once the old path is trashed, identical bytes can repoint it immediately."""

    root = mount_env.tmp_path / "vanish"
    root.mkdir()
    old_path = root / "gone.txt"
    _write(old_path, b"return", mtime_ns=BASE_MTIME_NS)
    mount = _connect(mount_env, root, mode=MountMode.REFERENCE, name="Vanish")
    del queued_mounts
    assert _run_sync(mount) == 1
    original = _file_map(mount.drive)["gone.txt"]

    old_path.unlink()
    assert _run_sync(mount) == 0
    assert _file_map(mount.drive)["gone.txt"].is_trashed

    _write(root / "returned.txt", b"return", mtime_ns=BASE_MTIME_NS + 1)
    assert _run_sync(mount) == 1
    returned = _file_map(mount.drive)["returned.txt"]
    assert returned.pk == original.pk
    assert not returned.is_trashed


@pytest.mark.django_db(transaction=True)
def test_reference_stat_race_is_counted_without_trashing_observed_path(
    mount_env: SimpleNamespace,
    queued_mounts: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file vanishing between walk and stat is an error, not a prune signal."""

    root = mount_env.tmp_path / "stat-race"
    root.mkdir()
    target = root / "racy.txt"
    _write(target, b"racy", mtime_ns=BASE_MTIME_NS)
    mount = _connect(mount_env, root, mode=MountMode.REFERENCE, name="Stat race")
    del queued_mounts
    assert _run_sync(mount) == 1

    original_stat = Path.stat

    def racing_stat(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == target:
            raise FileNotFoundError(path)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", racing_stat)
    assert _run_sync(mount) == 0
    assert _details(mount)["errors"] == 1
    assert _details(mount)["scanned"] == 1
    assert _details(mount)["trashed"] == 0
    assert not _file_map(mount.drive)["racy.txt"].is_trashed


@pytest.mark.django_db(transaction=True)
def test_reference_entry_validation_error_is_contained_and_counted(
    mount_env: SimpleNamespace,
    queued_mounts: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One invalid entry does not fail the bridge-level reconciliation run."""

    root = mount_env.tmp_path / "validation-error"
    root.mkdir()
    _write(root / "bad.txt", b"bad", mtime_ns=BASE_MTIME_NS)
    mount = _connect(mount_env, root, mode=MountMode.REFERENCE, name="Invalid entry")
    del queued_mounts

    def invalid_entry(backend: LocalFolderMountBackend, entry: Any) -> str:
        del backend, entry
        raise ValidationError({"path": "invalid external entry"})

    monkeypatch.setattr(LocalFolderMountBackend, "entry_hash", invalid_entry)
    assert _run_sync(mount) == 0
    assert mount.last_sync_status == "ok"
    assert _details(mount)["errors"] == 1
    assert _details(mount)["scanned"] == 1


@pytest.mark.django_db(transaction=True)
def test_reference_sync_skips_unmirrorable_directory_without_aborting(
    mount_env: SimpleNamespace,
    queued_mounts: list[Any],
) -> None:
    """An un-mirrorable source directory is skipped, never fatal to the run.

    ``FolderManager.ensure_path`` rejects a path segment carrying a separator, so
    a directory named with a backslash raises ``UploadError`` out of the mirror
    pass. Without per-directory containment that aborts the whole sync — latching
    ``runtime_status=ERROR`` with nothing indexed. Contained, the odd directory
    is counted and skipped while normal directories still index their files.
    """

    root = mount_env.tmp_path / "unmirrorable-dir"
    root.mkdir()
    _write(root / "good" / "keep.txt", b"keep", mtime_ns=BASE_MTIME_NS)
    (root / "bad\\dir").mkdir()
    mount = _connect(mount_env, root, mode=MountMode.REFERENCE, name="Unmirrorable dir")
    del queued_mounts

    assert _run_sync(mount) == 1
    assert mount.last_sync_status == "ok"
    assert mount.runtime_status == IntegrationRuntimeStatus.OK
    assert mount.sync_stage == mount.SyncStage.COMPLETED
    assert _details(mount)["errors"] == 1

    files = _file_map(mount.drive)
    assert set(files) == {"good/keep.txt"}
    assert not files["good/keep.txt"].is_trashed
    assert files["good/keep.txt"].folder.name == "good"
    with system_context(reason="test unmirrorable dir folders"):
        assert set(Folder.objects.filter(drive=mount.drive).values_list("name", flat=True)) == {"good"}


@pytest.mark.django_db(transaction=True)
def test_reference_content_change_collision_becomes_external_duplicate(
    mount_env: SimpleNamespace,
    queued_mounts: list[Any],
) -> None:
    """Updating one path onto another live hash is counted without overwriting."""

    root = mount_env.tmp_path / "hash-collision"
    root.mkdir()
    a_path = root / "a.txt"
    b_path = root / "b.txt"
    _write(a_path, b"alpha", mtime_ns=BASE_MTIME_NS)
    _write(b_path, b"bravo", mtime_ns=BASE_MTIME_NS + 1)
    mount = _connect(mount_env, root, mode=MountMode.REFERENCE, name="Hash collision")
    del queued_mounts
    assert _run_sync(mount) == 2
    before = _file_map(mount.drive)
    b_pk = before["b.txt"].pk
    b_hash = before["b.txt"].content_hash

    _write(b_path, b"alpha", mtime_ns=BASE_MTIME_NS + 2)
    assert _run_sync(mount) == 0
    assert _details(mount)["duplicates"] == 1
    after = _file_map(mount.drive)
    assert after["b.txt"].pk == b_pk
    assert after["b.txt"].content_hash == b_hash
    assert not after["b.txt"].is_trashed


@pytest.mark.django_db(transaction=True)
def test_copy_sync_ingests_dedups_stamps_metadata_and_never_prunes(
    mount_env: SimpleNamespace,
    queued_mounts: list[Any],
) -> None:
    """Copy mode owns managed bytes while source disappearance remains non-destructive."""

    root = mount_env.tmp_path / "copy-sync"
    root.mkdir()
    source = root / "nested" / "copy.txt"
    _write(source, b"copied", mtime_ns=BASE_MTIME_NS)
    mount = _connect(mount_env, root, mode=MountMode.COPY, name="Copy sync")
    del queued_mounts

    assert _run_sync(mount) == 1
    [row] = _files(mount.drive)
    original_pk = row.pk
    assert row.metadata["mount"] == {
        "source_path": "nested/copy.txt",
        "mtime_ns": BASE_MTIME_NS,
    }
    assert row.storage_path.startswith("mounts/copy-sync/")
    assert (mount_env.managed_root / row.storage_path).read_bytes() == b"copied"

    os.utime(source, ns=(BASE_MTIME_NS + 1, BASE_MTIME_NS + 1))
    assert _run_sync(mount) == 1
    [dedup] = _files(mount.drive)
    assert dedup.pk == original_pk
    assert dedup.metadata["mount"]["mtime_ns"] == BASE_MTIME_NS + 1

    assert _run_sync(mount) == 0
    assert _details(mount)["unchanged"] == 1
    source.unlink()
    assert _run_sync(mount) == 0
    [retained] = _files(mount.drive)
    assert retained.pk == original_pk
    assert not retained.is_trashed
    assert _details(mount)["vanished"] == 1
    assert _details(mount)["trashed"] == 0


@pytest.mark.django_db(transaction=True)
def test_run_sync_records_success_and_missing_root_error_telemetry(
    mount_env: SimpleNamespace,
    queued_mounts: list[Any],
) -> None:
    """Bridge telemetry records both a completed run and source validation failure."""

    root = mount_env.tmp_path / "telemetry"
    root.mkdir()
    source = root / "one.txt"
    _write(source, b"one", mtime_ns=BASE_MTIME_NS)
    mount = _connect(mount_env, root, mode=MountMode.REFERENCE, name="Telemetry")
    del queued_mounts
    first_now = timezone.now()

    assert _run_sync(mount, now=first_now) == 1
    assert mount.last_sync_started_at == first_now
    assert mount.last_sync_completed_at == first_now
    assert mount.last_sync_status == "ok"
    assert mount.last_sync_items == 1
    assert mount.sync_stage == mount.SyncStage.COMPLETED
    assert mount.runtime_status == IntegrationRuntimeStatus.OK
    assert mount.next_sync_at is None

    source.unlink()
    root.rmdir()
    failure_now = first_now + timedelta(minutes=1)
    with pytest.raises(ValidationError), system_context(reason="test missing mount root"):
        mount.run_sync(now=failure_now)
    mount.refresh_from_db()
    assert mount.last_sync_started_at == failure_now
    assert mount.last_sync_completed_at == first_now
    assert mount.last_sync_status == "error"
    assert mount.sync_stage == mount.SyncStage.FAILED
    assert mount.sync_error.startswith("ValidationError:")
    assert mount.runtime_status == IntegrationRuntimeStatus.ERROR
    assert mount.next_sync_at is None


@pytest.mark.django_db(transaction=True)
def test_next_sync_at_requires_poll_enabled(mount_env: SimpleNamespace) -> None:
    """Manual mounts stay unscheduled unless their own config opts into polling."""

    root = mount_env.tmp_path / "schedule"
    root.mkdir()
    mount = make_mount(
        "schedule",
        drive=mount_env.default_drive,
        root=root,
        mode=MountMode.COPY,
    )
    now = timezone.now()

    assert mount._next_sync_at(now=now) is None
    mount.config = {**mount.config, "poll_enabled": True}
    mount.poll_interval = 37
    assert mount._next_sync_at(now=now) == now + timedelta(seconds=37)


@pytest.mark.django_db(transaction=True)
def test_mount_graphql_connect_sync_list_and_non_admin_reader_denials(
    mount_env: SimpleNamespace,
    queued_mounts: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The console exposes enum connect/list/sync while retaining admin action gates."""

    admin = mount_env.owner
    grant(actor=admin, role=app_settings.REBAC_UNIVERSAL_ADMIN_ROLE)
    root = mount_env.tmp_path / "graphql"
    root.mkdir()
    schema = addon_schema(storage_integrate_schema.schemas, "console")
    assert "mode: MountMode!" in schema.as_str()

    connected = result_data(
        execute_schema(
            schema,
            """
            mutation Connect($path: String!, $mode: MountMode!) {
              connect_local_folder(name: "GraphQL mount", path: $path, mode: $mode) {
                id
                mode
                drive
                lifecycle
              }
            }
            """,
            {"path": str(root), "mode": "REFERENCE"},
            user=admin,
        )
    )["connect_local_folder"]
    assert connected["mode"] == "REFERENCE"
    assert connected["lifecycle"] == "CONNECTED"
    assert len(queued_mounts) == 1
    mount = queued_mounts[0][0]
    assert connected["id"] == str(mount.sqid)
    assert connected["drive"] == str(mount.drive.sqid)

    listed = result_data(
        execute_schema(
            schema,
            "query { mounts { id mode drive } }",
            user=admin,
        )
    )["mounts"]
    assert listed == [
        {
            "id": str(mount.sqid),
            "mode": "REFERENCE",
            "drive": str(mount.drive.sqid),
        }
    ]

    sync_calls: list[tuple[Any, dict[str, Any]]] = []
    monkeypatch.setattr(
        storage_integrate_schema,
        "queue_bridge_sync",
        lambda row, **kwargs: sync_calls.append((row, kwargs)),
    )
    synced = result_data(
        execute_schema(
            schema,
            "mutation($id: ID!){ sync_mount(id: $id){ ok message } }",
            {"id": str(mount.sqid)},
            user=admin,
        )
    )["sync_mount"]
    assert synced == {"ok": True, "message": "Queued mount sync."}
    assert len(sync_calls) == 1 and sync_calls[0][0].pk == mount.pk

    reader = get_user_model().objects.create_user(
        username="mount-reader",
        email="mount-reader@example.com",
    )
    with system_context(reason="test mount reader"):
        mount.owner = reader
        mount.save(update_fields=["owner", "updated_at"])
    reader_list = result_data(
        execute_schema(
            schema,
            "query { mounts { id } }",
            user=reader,
        )
    )["mounts"]
    assert reader_list == [{"id": str(mount.sqid)}]

    denied_sync = execute_schema(
        schema,
        "mutation($id: ID!){ sync_mount(id: $id){ ok } }",
        {"id": str(mount.sqid)},
        user=reader,
    )
    assert denied_sync.errors is not None
    denied_connect = execute_schema(
        schema,
        """
        mutation($path: String!, $mode: MountMode!) {
          connect_local_folder(name: "Denied", path: $path, mode: $mode) { id }
        }
        """,
        {"path": str(root), "mode": "COPY"},
        user=reader,
    )
    assert denied_connect.errors is not None
