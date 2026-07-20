"""Source model for external storage mounts."""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, cast

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models
from rebac import system_context

from angee.base.fields import StateField
from angee.base.impl import ImplClassField
from angee.base.models import AngeeManager
from angee.integrate.models import Bridge
from angee.integrate.sync import current_bridge_progress
from angee.storage import exceptions
from angee.storage_integrate.mounts import MountBackend, MountEntry

_PROGRESS_BATCH_SIZE = 200


@dataclass(frozen=True)
class _MountFileState:
    """File-owned freshness facts loaded once for one mount sync."""

    pk: Any
    content_hash: str
    size_bytes: int
    is_trashed: bool
    mtime_ns: int | None


class MountMode(models.TextChoices):
    """How a mount handles bytes discovered at its external source."""

    COPY = "copy", "Copy files"
    REFERENCE = "reference", "Leave files in place"


class Mount(Bridge):
    """An external storage source synchronized into a dedicated drive.

    Per-file freshness lives on the synchronized File rows, so the Bridge cursor
    never grows with source size.
    """

    runtime = True
    extends = "integrate.Integration"
    integration_kind_label = "Mount"
    live_impl_field = "backend_class"

    backend_class = ImplClassField(
        base_class=MountBackend,
        registry_setting="ANGEE_STORAGE_MOUNT_BACKEND_CLASSES",
        default="local_folder",
    )
    drive = models.ForeignKey(
        "storage.Drive",
        on_delete=models.PROTECT,
        related_name="mounts",
        editable=False,
    )
    mode = StateField(
        choices_enum=MountMode,
        default=MountMode.REFERENCE,
        editable=False,
    )

    objects = AngeeManager()

    class Meta:
        """Django model options for the Mount integration child."""

        abstract = True
        rebac_resource_type = "storage_integrate/mount"
        rebac_id_attr = "sqid"

    @property
    def backend(self) -> MountBackend:
        """Return the selected mount backend bound to this row."""

        return cast(MountBackend, self.live_impl)

    def _next_sync_at(self, *, now: Any) -> Any:
        """Keep manual mounts unscheduled unless polling is explicitly enabled."""

        if not self.config.get("poll_enabled"):
            return None
        return super()._next_sync_at(now=now)

    def sync(self) -> int:
        """Reconcile the external source into this mount's storage drive."""

        backend = self.backend
        backend.check_source()
        file_model = apps.get_model("storage", "File")
        folder_model = apps.get_model("storage", "Folder")
        if isinstance(self.cursor, Mapping) and "index" in self.cursor:
            self.cursor = {key: value for key, value in self.cursor.items() if key != "index"}
        previous = self._freshness_map(file_model)
        previous_present = {path for path, state in previous.items() if not state.is_trashed}
        seen_paths: set[str] = set()
        folder_cache: dict[tuple[str, ...], Any | None] = {(): None}
        mime_cache: dict[str, Any | None] = {}
        counts = {
            "changed": 0,
            "unchanged": 0,
            "duplicates": 0,
            "errors": 0,
            "trashed": 0,
            "vanished": 0,
            "scanned": 0,
        }

        self._mirror_directories(backend, folder_model=folder_model, cache=folder_cache, counts=counts)

        for entry in backend.iter_entries():
            counts["scanned"] += 1
            seen_paths.add(entry.path)
            prior = previous.get(entry.path)
            if self._entry_unchanged(entry, prior):
                counts["unchanged"] += 1
                self._report_batch(counts)
                continue

            digest = ""
            try:
                digest = backend.entry_hash(entry)
                folder = self._entry_folder(
                    entry,
                    folder_model=folder_model,
                    cache=folder_cache,
                )
                if self.mode == MountMode.REFERENCE:
                    file_model.objects.index_external(
                        drive=self.drive,
                        storage_path=entry.path,
                        filename=PurePosixPath(entry.path).name,
                        content_hash=digest,
                        size_bytes=entry.size_bytes,
                        mime_type=backend.entry_mime(entry),
                        folder=folder,
                        metadata={"mount": {"mtime_ns": entry.mtime_ns}},
                        mime_cache=mime_cache,
                        existing_pk=prior.pk if prior is not None else None,
                        owner_id=self.owner_id,
                    )
                else:
                    with contextlib.closing(backend.open_entry(entry)) as reader:
                        file_model.objects.ingest_stream(
                            reader,
                            filename=PurePosixPath(entry.path).name,
                            content_hash=digest,
                            size_bytes=entry.size_bytes,
                            metadata={
                                "mount": {
                                    "source_path": entry.path,
                                    "mtime_ns": entry.mtime_ns,
                                }
                            },
                            owner_id=self.owner_id,
                            drive_id=str(self.drive.public_id),
                            folder_id=str(folder.public_id) if folder is not None else "",
                        )
                counts["changed"] += 1
            except exceptions.ExternalDuplicate:
                counts["duplicates"] += 1
                self._report_batch(counts)
                continue
            except (exceptions.UploadError, OSError, ValidationError):
                counts["errors"] += 1
                self._report_batch(counts)
                continue

            self._report_batch(counts)

        seen_paths.update(backend.observed_paths)
        counts["scanned"] += backend.scan_errors
        counts["errors"] += backend.scan_errors
        counts["vanished"] = len(previous_present.difference(seen_paths))
        if self.mode == MountMode.REFERENCE:
            counts["trashed"] = file_model.objects.trash_missing_external(
                self.drive,
                seen_paths,
            )
            folder_model.objects.prune_missing(
                self.drive,
                [parts for parts in folder_cache if parts],
            )
        self._report_progress(counts, complete=True)
        return counts["changed"]

    def _freshness_map(self, file_model: Any) -> dict[str, _MountFileState]:
        """Load this drive's File-owned mount freshness in one streamed query."""

        freshness: dict[str, _MountFileState] = {}
        with system_context(reason="storage_integrate.mount.freshness"):
            rows = (
                file_model.objects.filter(drive_id=self.drive_id)
                .order_by("pk")
                .values_list(
                    "pk",
                    "storage_path",
                    "content_hash",
                    "size_bytes",
                    "is_trashed",
                    "metadata",
                )
            )
            for pk, storage_path, content_hash, size_bytes, is_trashed, metadata in rows.iterator(
                chunk_size=2000
            ):
                mount_metadata = metadata.get("mount") if isinstance(metadata, Mapping) else None
                mount_values = mount_metadata if isinstance(mount_metadata, Mapping) else {}
                source_path = (
                    str(storage_path)
                    if self.mode == MountMode.REFERENCE
                    else str(mount_values.get("source_path") or "")
                )
                if not source_path:
                    continue
                raw_mtime_ns = mount_values.get("mtime_ns")
                try:
                    mtime_ns = int(raw_mtime_ns) if raw_mtime_ns is not None else None
                except (TypeError, ValueError):
                    mtime_ns = None
                freshness[source_path] = _MountFileState(
                    pk=pk,
                    content_hash=str(content_hash),
                    size_bytes=int(size_bytes),
                    is_trashed=bool(is_trashed),
                    mtime_ns=mtime_ns,
                )
        return freshness

    def _entry_unchanged(self, entry: MountEntry, previous: _MountFileState | None) -> bool:
        """Return whether File-owned source facts prove ``entry`` is unchanged."""

        return (
            previous is not None
            and not previous.is_trashed
            and previous.size_bytes == entry.size_bytes
            and previous.mtime_ns == entry.mtime_ns
            and bool(previous.content_hash)
        )

    def _mirror_directories(
        self,
        backend: MountBackend,
        *,
        folder_model: Any,
        cache: dict[tuple[str, ...], Any | None],
        counts: dict[str, int],
    ) -> None:
        """Ensure a Folder row for every source directory, empty ones included.

        Runs before the file pass so an empty directory still mirrors into the
        tree, and so each file's parent folder is already resolved in ``cache``.
        The shared cache means an unchanged tree is walked once per run without
        re-resolving a directory the pass already ensured.

        One un-mirrorable directory (a name that is whitespace-only after strip,
        or carries a path separator) makes ``ensure_path`` raise; contained
        per-directory the way the file pass contains a bad entry, so an odd
        directory name is skipped and counted, never fatal to the whole sync.
        """

        for directory in backend.iter_directories():
            try:
                folder_model.objects.ensure_path(
                    self.drive,
                    PurePosixPath(directory).parts,
                    cache=cache,
                )
            except (exceptions.UploadError, OSError, ValidationError):
                counts["errors"] += 1
                continue

    def _entry_folder(
        self,
        entry: MountEntry,
        *,
        folder_model: Any,
        cache: dict[tuple[str, ...], Any | None],
    ) -> Any | None:
        """Return the cached storage folder mirroring ``entry``'s parent path."""

        parts = tuple(PurePosixPath(entry.path).parts[:-1])
        if parts not in cache:
            cache[parts] = folder_model.objects.ensure_path(self.drive, parts, cache=cache)
        return cache[parts]

    def _report_batch(self, counts: dict[str, int]) -> None:
        """Publish progress after each complete batch."""

        if counts["scanned"] % _PROGRESS_BATCH_SIZE == 0:
            self._report_progress(counts)

    def _report_progress(self, counts: dict[str, int], *, complete: bool = False) -> None:
        """Publish mount counters through the generic bridge change stream."""

        reporter = current_bridge_progress()
        if reporter is None:
            return
        reporter.report(
            "syncing",
            message="Mount sync completed" if complete else "Mount sync in progress",
            details={"backend": self.backend_class, "mode": str(self.mode), **counts},
        )
