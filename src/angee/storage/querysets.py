"""Native querysets owners for storage."""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, Self

from django.conf import settings
from django.db import models, transaction
from rebac import (
    generic_target,
    system_context,
)

from angee.base.models.hierarchy import HierarchyQuerySet
from angee.base.models.ownership import OwnerQuerySet
from angee.base.models.trash import ArchiveQuerySet, TrashQuerySet
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet
from angee.storage.choices import UploadState

if TYPE_CHECKING:
    from angee.storage.models.attachment import FileAttachment
    from angee.storage.models.backend import Drive
    from angee.storage.models.file import File
    from angee.storage.models.folder import Folder

logger = logging.getLogger(__name__)


class StorageMasterQuerySet[StorageT: models.Model](ArchiveQuerySet[StorageT], AngeeHistoryQuerySet[StorageT]):
    """AngeeHistoryQuerySet plus the archive read vocabulary for storage master rows.

    Shared by the archivable admin/infra rows (:class:`Backend`, :class:`Drive`)
    so ``.archived()`` / ``.unarchived()`` compose over the REBAC row scope.
    """


class DriveQuerySet[DriveT: Drive](OwnerQuerySet[DriveT], StorageMasterQuerySet[DriveT]):
    """Archive reads and protected ownership writes for storage volumes."""

    def browsable(self) -> DriveQuerySet[DriveT]:
        """Return the drives listings offer: all but the image drive, as with ``File.objects.browsable``."""

        return self.exclude(slug=settings.ANGEE_STORAGE_IMAGE_DRIVE)


class FolderQuerySet[FolderT: Folder](HierarchyQuerySet[FolderT], AngeeHistoryQuerySet[FolderT]):
    """Actor-scoped folder trees with paths maintained by HierarchyMixin."""

    def prune_missing(self, drive: Any, present: Collection[Sequence[str]]) -> int:
        """Delete real folders whose source directory vanished and hold no rows.

        The folder mirror of :meth:`FileManager.trash_missing_external`: a
        directory removed from an external source must not linger in the tree.
        Only a truly empty leaf is removed — a folder that still parents a file
        (a live one, or a soft-trashed one kept for history) or a surviving
        subfolder is retained, so the reconcile never orphans a row and needs no
        recursion. The ``folders_with_files`` guard is load-bearing precisely
        because ``File.folder`` is ``SET_NULL`` (not ``PROTECT``): deleting a
        folder that still parents a file would silently null the file's folder
        rather than being blocked, so the guard — not the FK constraint — is what
        keeps a live sync from detaching a row. Deepest paths are removed first,
        so the ``parent`` PROTECT constraint always holds. ``present`` is the set
        of drive-relative directory paths the current sync mirrored.
        """

        present_paths = {tuple(parts) for parts in present}
        file_model = self.model._meta.get_field("files").related_model
        assert file_model is not None
        with system_context(reason="storage.folder.prune_missing"):
            eligible = self.filter(drive=drive, is_virtual=False)
            folders = list(eligible.only("pk", "parent_id", "name", "path"))
            selected_ids = [folder.pk for folder in folders]
            ancestors = (
                self.model.objects.sudo(reason="storage.folder.prune_ancestry")
                .ancestors_of(folders)
                .only(
                    "path",
                    "name",
                )
            )
            names = {folder.path: folder.name for folder in (*ancestors, *folders)}
            children = self.model._base_manager.filter(parent_id__in=selected_ids)
            child_counts = dict(
                children.order_by()
                .values("parent_id")
                .annotate(
                    count=models.Count("pk"),
                )
                .values_list("parent_id", "count")
            )
            folders_with_files = set(
                file_model._base_manager.filter(folder_id__in=selected_ids)
                .order_by()
                .values_list("folder_id", flat=True)
                .distinct()
            )
            pruned = 0
            for folder in sorted(folders, key=lambda row: len(row.ancestor_paths()), reverse=True):
                source_path = tuple(names[path] for path in (*folder.ancestor_paths(), folder.path))
                if source_path in present_paths:
                    continue
                if folder.pk in folders_with_files or child_counts.get(folder.pk, 0):
                    continue
                with transaction.atomic(using=self.db):
                    current = eligible.filter(
                        pk=folder.pk, path=folder.path, name=folder.name,
                    ).lock_if_supported().first()
                    if current is None:
                        continue
                    # Foreign-key admission shares this row lock; recheck an empty
                    # candidate before SET_NULL could detach a concurrently inserted file.
                    if self.model._base_manager.filter(parent_id=folder.pk).exists():
                        continue
                    if file_model._base_manager.filter(folder_id=folder.pk).exists():
                        continue
                    eligible.filter(pk=current.pk).delete()
                pruned += 1
                if folder.parent_id is not None:
                    child_counts[folder.parent_id] = child_counts.get(folder.parent_id, 1) - 1
            return pruned


class FileQuerySet[FileT: File](TrashQuerySet[FileT], OwnerQuerySet[FileT], AngeeHistoryQuerySet[FileT]):
    """REBAC-scoped reads for file rows; ``trashed()`` backs the Trash smart folder."""

    def trash_missing_external(self, drive: Any, present_paths: Collection[str]) -> int:
        """Soft-trash live rows in ``drive`` whose external path vanished."""

        present = {str(path) for path in present_paths}
        with system_context(reason="storage.file.trash_missing_external"):
            rows = self.filter(drive=drive).untrashed().values_list("pk", "storage_path")
            missing = [pk for pk, storage_path in rows.iterator(chunk_size=2000) if str(storage_path) not in present]
            if not missing:
                return 0
            return int(self.filter(pk__in=missing).trash())

    def purge(
        self,
        *,
        chunk_size: int = 500,
        on_error: Callable[[Any, Exception], None] | None = None,
    ) -> int:
        """Purge still-selected rows individually, retaining error continuation."""

        purged = 0
        for pk in self.values_list("pk", flat=True).iterator(chunk_size=max(1, chunk_size)):
            try:
                with transaction.atomic(using=self.db):
                    row = self.filter(pk=pk).lock_if_supported().first()
                    if row is None:
                        continue
                    row.purge()
            except Exception as error:
                if on_error is not None:
                    on_error(pk, error)
                else:
                    logger.exception("storage.file.purge failed for pk=%s", pk)
                continue
            purged += 1
        return purged

    def stale_drafts(self, cutoff: datetime) -> Self:
        """Return unfinished DRAFT/FAILED rows reserved before ``cutoff``."""

        return self.filter(upload_state__in=(UploadState.DRAFT, UploadState.FAILED), created_at__lt=cutoff)

    def expired_trash(self, cutoff: datetime) -> Self:
        """Return rows whose trash stay lapsed before ``cutoff``."""

        return self.trashed().filter(trashed_at__lt=cutoff)

    def images(self) -> Self:
        """Return the live images on the image drive (:meth:`FileManager.ingest_image`)."""

        return self.untrashed().filter(drive__slug=settings.ANGEE_STORAGE_IMAGE_DRIVE, upload_state=UploadState.READY)

    def browsable(self) -> Self:
        """Return the files listings and pickers offer: all but the image drive's.

        Every signed-in user reads the image drive, so its images reach a reader
        only through the records that show them, never as a browsable gallery.
        """

        return self.exclude(drive__slug=settings.ANGEE_STORAGE_IMAGE_DRIVE)

    def delete(self) -> tuple[int, dict[str, int]]:
        """Refuse bulk delete: it bypasses soft-trash and orphans backend bytes.

        ``QuerySet.delete()`` issues SQL without calling :meth:`File.delete`, so
        callers must iterate and soft-delete, or :meth:`File.purge` to hard-delete.
        """

        raise NotImplementedError(
            "Bulk File delete is disabled; call File.delete() per row to trash, or File.purge() to hard-delete."
        )


class FileAttachmentQuerySet[AttachmentT: FileAttachment](AngeeHistoryQuerySet[AttachmentT]):
    """Actor-scoped attachment sets for declared record identities."""

    def with_same_content(self, file: Any, record: models.Model) -> Self:
        """Readable edges of this record type carrying the same verified bytes."""
        return (
            self.filter(
                content_type=generic_target(record).content_type,
                file__content_hash=file.content_hash,
                file__upload_state=UploadState.READY,
                file__is_trashed=False,
            )
            if file.content_hash and file.upload_state == UploadState.READY
            else self.none()
        )

    def for_record(self, record: models.Model) -> Self:
        """Return actor-readable file edges for a canonical record."""

        return self.filter(
            **generic_target(record).lookups(self.model, "target"),
            file__is_trashed=False,
        ).select_related("file")
