"""Native managers owners for storage."""

from __future__ import annotations

import hashlib
import logging
import secrets
from collections.abc import Sequence
from typing import IO, TYPE_CHECKING, Any, NoReturn, cast

from django.conf import settings
from django.core import signing
from django.core.exceptions import (
    ObjectDoesNotExist,
    ValidationError,
)
from django.core.files.base import ContentFile
from django.core.files.base import File as DjangoFile
from django.db import DEFAULT_DB_ALIAS, IntegrityError, models, transaction
from rebac import (
    GenericTarget,
    PermissionDenied,
    SubjectRef,
    check_permission,
    current_actor,
    database_context,
    generic_target,
    system_context,
    to_object_ref,
    user_subject_type,
)
from rebac.backends import backend as rebac_backend
from rebac.database import write_alias
from rebac.field_backing import resolve_field_backing
from rebac.resources import model_resource_type
from rebac.schema.introspection import permission_sources

from angee.base.querysets import row_lock_paths
from angee.iam.identity import canonical_subject_ref
from angee.iam.permissions import DirectRecordAccess
from angee.provenance.contrib.angee.managers import AngeeHistoryManager
from angee.storage import exceptions
from angee.storage.choices import FileVisibility, UploadState
from angee.storage.querysets import (
    DriveQuerySet,
    FileAttachmentQuerySet,
    FileQuerySet,
    FolderQuerySet,
    StorageMasterQuerySet,
)
from angee.storage.uploads import (
    DOWNLOAD_TOKEN_MAX_AGE,
    DOWNLOAD_TOKEN_SALT,
    FALLBACK_MIME,
    UPLOAD_TOKEN_MAX_AGE,
    UPLOAD_TOKEN_SALT,
    _normalized_hash,
    attachment_extension,
    require_image,
    truncate_filename,
)

if TYPE_CHECKING:
    from angee.storage.models.attachment import FileAttachment
    from angee.storage.models.backend import Drive
    from angee.storage.models.file import File
    from angee.storage.models.folder import Folder

logger = logging.getLogger(__name__)


StorageMasterManager = AngeeHistoryManager.from_queryset(StorageMasterQuerySet)


_DriveManagerBase = AngeeHistoryManager.from_queryset(DriveQuerySet)


class DriveManager[DriveT: Drive](_DriveManagerBase[DriveT]):
    """Provision independent volumes on the configured default backend."""

    def configured_default(self) -> DriveT | None:
        """Read the configured infrastructure drive without changing caller authority."""

        return (
            self.model.system_queryset()
            .using(write_alias(self.model, using=self._db))
            .filter(slug=settings.ANGEE_STORAGE_DEFAULT_DRIVE)
            .select_related("backend")
            .first()
        )

    def create_on_default_backend(self, *, slug: str, name: str, owns_items: bool = False) -> DriveT:
        """Create an ownerless drive with its own key namespace.

        The caller must hold native drive creation authority. Reading the
        infrastructure backend is elevated, while insertion retains its caller's
        permissions and attribution.
        """

        self = self.db_manager(write_alias(self.model, using=self._db))
        with database_context(self.db):
            default = self.configured_default()
            if default is None:
                raise ValidationError({"drive": "Configure an existing default drive before creating a volume."})
            drive = self.model(
                slug=slug,
                name=name,
                backend_id=default.backend_id,
                prefix=f"drives/{slug}",
                owns_items=owns_items,
            )
            try:
                with transaction.atomic(using=self.db):
                    drive.save(using=self.db, ownerless=True)
            except IntegrityError as error:
                raise ValidationError({"slug": "This drive slug or key namespace is already in use."}) from error
            return drive

    def image_drive(self) -> DriveT | None:
        """Return the image drive (``ANGEE_STORAGE_IMAGE_DRIVE``), creating it at first use.

        An ownerless volume on the default backend (:meth:`create_on_default_backend`),
        so images land wherever the deployment keeps its files, apart from any user
        file. Its one grant, ``viewer`` to every signed-in user (``auth/user:*``),
        is written when it is provisioned: whoever holds an image's id reads it,
        while which image a record shows stays with that record's readers. A drive
        already holding the slug serves only while it keeps that shape, with no
        other grant and no file anyone owns: an operator's drive of the name is
        refused and logged, so images never deduplicate onto its files nor open it
        to every user. ``None`` then, and while there is no default drive to
        provision it beside (a fresh database before resources load).
        """

        self = self.db_manager(write_alias(self.model, using=self._db))
        with database_context(self.db):
            slug = str(settings.ANGEE_STORAGE_IMAGE_DRIVE)
            everyone = DirectRecordAccess(relation="viewer", subject=SubjectRef.of(user_subject_type(), "*"))
            with system_context(reason="storage.drive.image_drive"):
                drive = (
                    self.model.system_queryset()
                    .using(write_alias(self.model, using=self._db))
                    .filter(slug=slug)
                    .first()
                )
                if drive is None:
                    try:
                        drive = self.create_on_default_backend(slug=slug, name="Images")
                    except ValidationError:
                        # A concurrent first use created it, or no default drive exists yet.
                        drive = (
                            self.model.system_queryset()
                            .using(write_alias(self.model, using=self._db))
                            .filter(slug=slug)
                            .first()
                        )
                if drive is None:
                    return None
                grants = set(drive.direct_record_access())
                if grants - {everyone} or drive.files.exclude(owner=None).exists():
                    logger.error("Drive %r holds grants or owned files, so it cannot serve as the image drive.", slug)
                    return None
                if not grants:
                    drive.system_grant_record_access(everyone.relation, everyone.subject)
                return drive


_FolderManagerBase = AngeeHistoryManager.from_queryset(FolderQuerySet)


class FolderManager[FolderT: Folder](_FolderManagerBase[FolderT]):
    """Manager owning gated folder creation."""

    def create_in_drive(self, *, drive_id: str, name: str, parent_id: str = "", description: str = "") -> FolderT:
        """Create a real folder after checking ``write`` on its drive.

        The drive-write check is this factory's create gate. Its existing
        per-instance ``sudo`` covers cross-row validation and insertion,
        while the ambient actor stamps ``created_by``. Mirrors
        :meth:`FileManager.draft`.
        """

        self = self.db_manager(write_alias(self.model, using=self._db))
        with database_context(self.db):
            drive_model = self.model.drive.field.related_model
            drive = (
                drive_model._default_manager.db_manager(write_alias(self.model, using=self._db))
                .all()
                .from_public_id(str(drive_id))
            )
            if drive is None:
                raise exceptions.UploadTargetNotFound("drive not found")
            if not drive.storage.writable:
                raise exceptions.UploadConflict("drive is read-only")
            if not drive.has_access("write"):
                raise exceptions.UploadDenied("write access to the drive is required")
            parent = None
            if parent_id:
                parent = self.all().from_public_id(str(parent_id))
                if parent is None or parent.is_virtual:
                    raise exceptions.UploadTargetNotFound("parent folder not found")
            actor = current_actor()
            folder = self.model(
                drive_id=drive.pk,
                parent_id=parent.pk if parent is not None else None,
                name=name,
                description=description,
            )
            folder.sudo(reason="storage.folder.create")
            try:
                folder.full_clean()
            except ValidationError as error:
                raise exceptions.UploadError(f"invalid folder request: {error}") from error
            folder.save(using=self.db)
            if actor is not None:
                folder.with_actor(actor)
            return folder

    def ensure_path(
        self, drive: Any, parts: Sequence[str], *, cache: dict[tuple[str, ...], Any | None] | None = None
    ) -> FolderT | None:
        """Return the real folder at ``parts``, creating missing segments.

        External indexers already own a drive and need idempotent tree
        materialization rather than the actor-gated GraphQL creation path.
        The database uniqueness constraints serialize concurrent attempts. A
        caller-owned prefix cache lets a tree walk resolve each directory once.
        """

        self = self.db_manager(write_alias(self.model, using=self._db or drive._state.db, instance=drive))
        if drive._state.db != self.db:
            raise ValueError("Drive must use the selected storage database.")
        with database_context(self.db):
            prefixes: dict[tuple[str, ...], Any | None] = cache if cache is not None else {(): None}
            if any(
                parent is not None and (parent._state.db != self.db or parent.drive_id != drive.pk)
                for parent in prefixes.values()
            ):
                raise ValueError("Folder cache must belong to the selected drive and database.")
            prefixes.setdefault((), None)
            prefix: tuple[str, ...] = ()
            parent = prefixes[()]
            with system_context(reason="storage.folder.ensure_path"):
                for raw_part in parts:
                    name = str(raw_part).strip()
                    if not name or name in {".", ".."} or "/" in name or "\\" in name:
                        raise exceptions.UploadError(f"invalid folder path segment: {raw_part!r}")
                    prefix = (*prefix, name)
                    if prefix in prefixes:
                        parent = prefixes[prefix]
                        continue
                    parent, _created = self.get_or_create(
                        drive_id=drive.pk,
                        parent_id=parent.pk if parent is not None else None,
                        name=name,
                        defaults={"is_virtual": False},
                    )
                    prefixes[prefix] = parent
            return parent


_FileManagerBase = AngeeHistoryManager.from_queryset(FileQuerySet)


class FileManager[FileT: File](_FileManagerBase[FileT]):
    """Manager owning File creation and the proxy-token lookup.

    :meth:`draft` reserves the row; the byte intake and publish verbs live on
    the row (see the module docstring for the source model).
    """

    def draft(
        self,
        *,
        filename: str,
        mime_type: str = "",
        size_bytes: int = 0,
        drive_id: str = "",
        drive_slug: str = "",
        folder_id: str = "",
        content_hash: str = "",
        visibility: FileVisibility | str = "inherited",
        record: models.Model | None = None,
        owner_id: Any = None,
    ) -> FileT:
        """Reserve a DRAFT File targeting a backend key, or return the dedup hit.

        Content-addressed get-or-create: when ``content_hash`` names bytes the
        drive already holds READY with inherited visibility, that row is
        returned (restored if trashed) and nothing needs writing — callers
        branch on ``upload_state``. New drafts use a unique reservation key,
        so unverified bytes never overwrite an existing file's backend object.
        The actor must hold ``write`` on the drive, and the record edge's
        ``create`` (write on the record) admits its insert in the same
        transaction; a missing, unwritable or undeclared record is one uniform
        denial. A record-visibility upload then requires a contributed attachment
        read/write arm. It never returns an existing dedup row.
        """

        self = self.db_manager(write_alias(self.model, using=self._db))
        with database_context(self.db):
            drive = self._drive_for(drive_id=drive_id, drive_slug=drive_slug, folder_id=folder_id)
            if not drive.storage.writable:
                raise exceptions.UploadConflict("drive is read-only")
            folder = self._folder_for(folder_id, drive=drive)
            if not drive.has_access("write"):
                raise exceptions.UploadDenied("write access to the drive is required")

            try:
                visibility = FileVisibility(visibility)
            except ValueError as error:
                raise exceptions.UploadError("invalid file visibility") from error
            if visibility == FileVisibility.RECORD and record is None:
                raise exceptions.UploadError("record visibility requires an attachment record")
            attachment_model = self.model._meta.get_field("attachments").related_model
            assert attachment_model is not None
            attachments = cast(
                "FileAttachmentManager[FileAttachment]", attachment_model._default_manager.db_manager(self.db)
            )
            if record is not None and record._state.db is not None and record._state.db != self.db:
                raise exceptions.UploadRecordDenied()
            try:
                target = generic_target(record) if record is not None else None
            except ValueError as error:
                raise exceptions.UploadRecordDenied() from error

            digest = _normalized_hash(content_hash) if content_hash else ""
            actor = current_actor()
            # The checked factory owns both writes; attribution keeps the real actor.
            with transaction.atomic(using=self.db):
                if target is not None:
                    try:
                        attachments._lock_target(target)
                    except ObjectDoesNotExist as error:
                        raise exceptions.UploadRecordDenied() from error
                if digest and visibility == FileVisibility.INHERITED:
                    existing = (
                        self.select_for_update()
                        .filter(
                            drive_id=drive.pk,
                            content_hash=digest,
                            upload_state=UploadState.READY,
                            visibility=FileVisibility.INHERITED,
                        )
                        .first()
                    )
                    if existing is not None:
                        if target is not None:
                            self._attach_record(attachments, existing, target)
                        if existing.is_trashed:
                            existing.restore()
                        return existing
                placeholder = secrets.token_hex(32)
                mime = _mime_row(self.model, mime_type, using=self.db)
                row = self.model(
                    drive=drive,
                    owner_id=owner_id,
                    folder_id=folder.pk if folder is not None else None,
                    filename=filename,
                    mime_type_id=mime.pk if mime is not None else None,
                    size_bytes=max(int(size_bytes or 0), 0),
                    content_hash=placeholder,
                    storage_path=drive.object_key(placeholder, filename),
                    upload_state=UploadState.DRAFT,
                    visibility=visibility,
                )
                row.sudo(reason="storage.file.draft")
                try:
                    row.full_clean()
                except ValidationError as error:
                    raise exceptions.UploadError(f"invalid file request: {error}") from error
                row.save(using=self.db)
                if target is not None:
                    self._attach_record(attachments, row, target)
                    # After the edge authorized the record, so the arm reveals nothing.
                    if visibility == FileVisibility.RECORD:
                        attachments.require_record_arm(target)
            if actor is not None:
                row.with_actor(actor)
            return row

    @staticmethod
    def _attach_record(attachments: Any, file: Any, target: GenericTarget) -> None:
        """Insert the upload's record edge under the actor; a refused edge denies the upload."""

        try:
            attachments._attach(file, target)
        except PermissionDenied as error:
            raise exceptions.UploadRecordDenied() from error

    def ingest_bytes(
        self,
        content: bytes,
        *,
        filename: str,
        owner_id: Any = None,
        drive_id: str = "",
        drive_slug: str = "",
        folder_id: str = "",
    ) -> FileT:
        """Compatibility file-return API over the canonical creation-status factory."""

        return self.get_or_ingest_bytes(
            content,
            filename=filename,
            owner_id=owner_id,
            drive_id=drive_id,
            drive_slug=drive_slug,
            folder_id=folder_id,
        )[0]

    def get_or_ingest_bytes(
        self,
        content: bytes,
        *,
        filename: str,
        owner_id: Any = None,
        drive_id: str = "",
        drive_slug: str = "",
        folder_id: str = "",
    ) -> tuple[FileT, bool]:
        """Ingest bytes already in hand into a drive and return the READY File.

        The server-side sibling of the proxy upload: the bytes are trusted (a
        sync fetched them), so this writes them to a unique reserved key and
        finalizes in one call instead of minting an upload token. Existing READY
        bytes with inherited visibility return their row; a record-scoped
        collision raises ``UploadConflict`` without identifying that row.
        ``owner_id`` sets ``owner`` independently of attribution; the whole verb runs
        elevated, so a sync needs no per-actor drive grant, and read access is
        scoped afterwards by that owner.
        """

        digest = hashlib.sha256(content).hexdigest()
        return self.get_or_ingest_stream(
            ContentFile(content),
            filename=filename,
            content_hash=digest,
            size_bytes=len(content),
            owner_id=owner_id,
            drive_id=drive_id,
            drive_slug=drive_slug,
            folder_id=folder_id,
        )

    def ingest_image(self, content: bytes) -> FileT:
        """Ingest an image the server renders inline (an avatar) and return its READY File.

        The bytes must pass :func:`require_image`. The stored filename takes the
        sniffed type's extension. Images land ownerless on the image drive
        (:meth:`DriveManager.image_drive`), so deduplication only ever returns
        another such image, never a user's file, and every signed-in user reads it
        through the drive's grant. Raises :class:`exceptions.UploadTooLarge` or
        :class:`exceptions.UploadTypeRefused` before anything is stored, and
        :class:`exceptions.UploadTargetNotFound` while no image drive can exist.
        """

        self = self.db_manager(write_alias(self.model, using=self._db))
        with database_context(self.db):
            mime = require_image(content)
            drive = self.model.drive.field.related_model.objects.db_manager(self.db).image_drive()
            if drive is None:
                raise exceptions.UploadTargetNotFound("The image drive needs a default drive to provision it.")
            return self.ingest_bytes(content, filename=f"image{attachment_extension(mime)}", drive_slug=drive.slug)

    def ingest_stream(
        self,
        reader: IO[bytes],
        *,
        filename: str,
        content_hash: str,
        size_bytes: int,
        metadata: dict[str, Any] | None = None,
        owner_id: Any = None,
        drive_id: str = "",
        drive_slug: str = "",
        folder_id: str = "",
    ) -> FileT:
        """Compatibility file-return API over the canonical creation-status factory."""

        return self.get_or_ingest_stream(
            reader,
            filename=filename,
            content_hash=content_hash,
            size_bytes=size_bytes,
            metadata=metadata,
            owner_id=owner_id,
            drive_id=drive_id,
            drive_slug=drive_slug,
            folder_id=folder_id,
        )[0]

    def get_or_ingest_stream(
        self,
        reader: IO[bytes],
        *,
        filename: str,
        content_hash: str,
        size_bytes: int,
        metadata: dict[str, Any] | None = None,
        owner_id: Any = None,
        drive_id: str = "",
        drive_slug: str = "",
        folder_id: str = "",
    ) -> tuple[FileT, bool]:
        """Ingest a pre-hashed stream without materializing it in memory.

        The caller hashes outside this write path. An inherited READY hit returns
        before the reader is consumed; otherwise the bytes stream through the
        selected Django storage backend and the normal finalize contract
        verifies the declared digest and size. Source filenames are display text,
        truncated to the File field's limit; content identity stays lossless.
        """

        self = self.db_manager(write_alias(self.model, using=self._db))
        with database_context(self.db):
            digest = _normalized_hash(content_hash)
            filename_limit = self.model.filename.field.max_length
            assert filename_limit is not None
            filename = truncate_filename(filename, filename_limit)
            expected_size = max(int(size_bytes), 0)
            with system_context(reason="storage.file.ingest_stream"):
                row = self.draft(
                    filename=filename,
                    size_bytes=expected_size,
                    drive_id=drive_id,
                    drive_slug=drive_slug,
                    folder_id=folder_id,
                    content_hash=digest,
                    owner_id=owner_id,
                )
                if row.upload_state == UploadState.READY:
                    row._grant_dedup_reach(owner_id)
                    row.merge_metadata(metadata)
                    return row, False  # content-addressed dedup hit
                storage = row.storage
                saved_path = storage.save(row.storage_path, DjangoFile(reader, name=row.storage_path))
                if saved_path and saved_path != row.storage_path:
                    row.storage_path = saved_path
                    row.save(using=self.db, update_fields=["storage_path"])
                try:
                    result = row.finalize(expected_hash=digest, expected_size=expected_size)
                except exceptions.UploadConflict:
                    if row.upload_envelope.get("failure_reason") == "duplicate":
                        row.sudo(reason="storage.ingest.duplicate").purge()
                    # A concurrent ingest of identical bytes won the dedup race; the
                    # winner is READY, so resolve to it instead of failing the sync.
                    winner = self.filter(
                        drive_id=row.drive_id,
                        content_hash=digest,
                        upload_state=UploadState.READY,
                        visibility=FileVisibility.INHERITED,
                    ).first()
                    if winner is not None:
                        winner._grant_dedup_reach(owner_id)
                        result = winner
                    else:
                        raise
                result.merge_metadata(metadata)
                return result, result.pk == row.pk

    def index_external(
        self,
        *,
        drive: Any,
        storage_path: str,
        filename: str,
        content_hash: str,
        size_bytes: int,
        mime_type: str = "",
        folder: Any | None = None,
        metadata: dict[str, Any] | None = None,
        mime_cache: dict[str, Any | None] | None = None,
        existing_pk: Any = None,
        owner_id: Any = None,
    ) -> FileT:
        """Upsert one externally-owned object by its drive-relative path.

        External bytes are already present, so this path creates READY rows
        directly. Path is the identity; the drive's content-hash uniqueness
        remains authoritative and reports a second path with identical bytes
        as :class:`~angee.storage.exceptions.ExternalDuplicate`.
        """

        self = self.db_manager(write_alias(self.model, using=self._db or drive._state.db, instance=drive))
        if drive._state.db != self.db:
            raise ValueError("Drive must use the selected storage database.")
        if folder is not None and (folder._state.db != self.db or folder.drive_id != drive.pk):
            raise ValueError("Folder must belong to the selected drive and database.")
        with database_context(self.db):
            path = str(storage_path).strip()
            if not path:
                raise exceptions.UploadError("storage_path is required")
            digest = _normalized_hash(content_hash)
            with system_context(reason="storage.file.index_external"), transaction.atomic(using=self.db):
                mime = self._cached_mime_row(mime_type, cache=mime_cache)
                row = None
                if existing_pk is not None:
                    row = self.filter(drive=drive, pk=existing_pk).first()
                if row is None:
                    row = self.filter(drive=drive, storage_path=path).first()

                if row is None:
                    duplicate = self.filter(
                        drive=drive,
                        content_hash=digest,
                        upload_state=UploadState.READY,
                    ).first()
                    if duplicate is not None:
                        if not duplicate.is_trashed:
                            self._raise_external_duplicate(duplicate)
                        row = duplicate

                metadata_patch = dict(metadata or {})

                if row is None:
                    row = self.model(
                        drive_id=drive.pk,
                        folder_id=folder.pk if folder is not None else None,
                        filename=filename,
                        content_hash=digest,
                        size_bytes=max(int(size_bytes), 0),
                        mime_type_id=mime.pk if mime is not None else None,
                        storage_path=path,
                        metadata=self.model.merged_metadata({}, metadata_patch),
                        upload_state=UploadState.READY,
                        owner_id=owner_id,
                    )
                    try:
                        row.full_clean()
                    except ValidationError as error:
                        raise exceptions.UploadError(f"invalid external file: {error}") from error
                    try:
                        with transaction.atomic(using=self.db):
                            row.save(using=self.db)
                    except IntegrityError as error:
                        duplicate = self.filter(
                            drive=drive,
                            content_hash=digest,
                            upload_state=UploadState.READY,
                        ).first()
                        if duplicate is not None:
                            self._raise_external_duplicate(duplicate, cause=error)
                        raise
                    row._emit_finalized()
                    return row

                content_changed = row.content_hash != digest
                if content_changed:
                    duplicate = (
                        self.filter(
                            drive=drive,
                            content_hash=digest,
                            upload_state=UploadState.READY,
                        )
                        .exclude(pk=row.pk)
                        .first()
                    )
                    if duplicate is not None:
                        self._raise_external_duplicate(duplicate)
                updates: list[str] = []
                values = {
                    "folder": folder,
                    "filename": filename,
                    "content_hash": digest,
                    "size_bytes": max(int(size_bytes), 0),
                    "mime_type": mime,
                    "storage_path": path,
                    "upload_state": UploadState.READY,
                    **self.model.UNTRASHED_VALUES,
                }
                if metadata_patch:
                    values["metadata"] = self.model.merged_metadata(row.metadata, metadata_patch)
                for field, value in values.items():
                    if getattr(row, field) != value:
                        setattr(row, field, value)
                        updates.append(field.removesuffix("_id"))
                if updates:
                    try:
                        row.full_clean()
                    except ValidationError as error:
                        raise exceptions.UploadError(f"invalid external file: {error}") from error
                    try:
                        with transaction.atomic(using=self.db):
                            row.save(using=self.db, update_fields=[*updates, "updated_at"])
                    except IntegrityError as error:
                        duplicate = (
                            self.filter(
                                drive=drive,
                                content_hash=digest,
                                upload_state=UploadState.READY,
                            )
                            .exclude(pk=row.pk)
                            .first()
                        )
                        if duplicate is not None:
                            self._raise_external_duplicate(duplicate, cause=error)
                        raise
                if content_changed:
                    row._emit_finalized()
                return row

    def _cached_mime_row(self, mime_type: str, *, cache: dict[str, Any | None] | None) -> Any | None:
        """Resolve one MIME taxonomy row, caching distinct values for a sync run."""

        key = str(mime_type or "").strip().lower() or FALLBACK_MIME
        if cache is not None and key in cache:
            return cache[key]
        row = _mime_row(self.model, key, using=self.db)
        if row is None and key != FALLBACK_MIME:
            if cache is not None and FALLBACK_MIME in cache:
                row = cache[FALLBACK_MIME]
            else:
                row = _mime_row(self.model, FALLBACK_MIME, using=self.db)
                if cache is not None:
                    cache[FALLBACK_MIME] = row
        if cache is not None:
            cache[key] = row
        return row

    def _raise_external_duplicate(
        self,
        duplicate: Any,
        *,
        cause: Exception | None = None,
    ) -> NoReturn:
        """Raise the canonical collision for a live row at another path."""

        raise exceptions.ExternalDuplicate(f"identical external bytes already exist: {duplicate.sqid}") from cause

    def _token_database(self, payload: object) -> str:
        """Select only a verified signed token's configured database identity."""

        if not isinstance(payload, dict):
            raise exceptions.UploadDenied("invalid file token")
        using = payload.get("database", DEFAULT_DB_ALIAS)
        if not isinstance(using, str) or using not in settings.DATABASES:
            raise exceptions.UploadDenied("invalid file token database")
        if self._db is not None and self._db != using:
            raise exceptions.UploadDenied("file token database does not match the selected database")
        return using

    def for_upload_token(self, token: str) -> FileT:
        """Return the DRAFT row a signed proxy upload token addresses.

        Validates the signature, expiry, draft state, and unspent nonce — but
        does not consume it; :meth:`File.receive_bytes` does that atomically
        after its own actor check. The token's nonce is pinned on the returned
        instance so the consume step binds to *this* token, not merely to the
        row's current envelope.
        """

        try:
            payload = signing.loads(token, salt=UPLOAD_TOKEN_SALT, max_age=UPLOAD_TOKEN_MAX_AGE)
        except signing.SignatureExpired as error:
            raise exceptions.UploadDenied("upload token expired") from error
        except signing.BadSignature as error:
            raise exceptions.UploadDenied("invalid upload token") from error
        using = self._token_database(payload)
        self = self.db_manager(using)
        with database_context(using):
            file_id = str(payload.get("file") or "")
            nonce = str(payload.get("nonce") or "")
            if not file_id or not nonce:
                raise exceptions.UploadDenied("invalid upload token")
            row = (
                self.system_context(reason="storage.upload.proxy").select_related("drive").filter(sqid=file_id).first()
            )
            if row is None:
                raise exceptions.UploadTargetNotFound("file not found")
            if row.upload_state != UploadState.DRAFT:
                raise exceptions.UploadConflict("file is not awaiting bytes")
            envelope = dict(row.upload_envelope or {})
            if envelope.get("nonce") != nonce or envelope.get("used"):
                raise exceptions.UploadDenied("upload token already used")
            row._upload_nonce = nonce
            return row

    def for_download_token(self, token: str) -> FileT:
        """Return the READY file a signed proxy download token addresses.

        The mirror of :meth:`for_upload_token`: re-check ``read`` for the token's
        actor after the elevated lookup. The request needs no credentials: any
        bearer may use the token until it expires or its actor loses access.
        Tokens without an actor claim are rejected. Trashed and unfinished
        rows have no servable bytes.
        """

        try:
            payload = signing.loads(token, salt=DOWNLOAD_TOKEN_SALT, max_age=DOWNLOAD_TOKEN_MAX_AGE)
        except signing.SignatureExpired as error:
            raise exceptions.UploadDenied("download token expired") from error
        except signing.BadSignature as error:
            raise exceptions.UploadDenied("invalid download token") from error
        using = self._token_database(payload)
        self = self.db_manager(using)
        with database_context(using):
            file_id = str(payload.get("file") or "")
            actor_claim = str(payload.get("actor") or "")
            if not file_id:
                raise exceptions.UploadDenied("invalid download token")
            try:
                actor = canonical_subject_ref(actor_claim)
            except ValueError as error:
                raise exceptions.UploadDenied("invalid download token") from error
            row = (
                self.system_context(reason="storage.download.proxy")
                .select_related("mime_type")
                .filter(sqid=file_id, upload_state=UploadState.READY, is_trashed=False)
                .first()
            )
            if row is None:
                raise exceptions.UploadTargetNotFound("file not found")
            row.with_actor(actor)
            if not row.has_access("read"):
                raise exceptions.UploadDenied("read access to the file is required")
            return row

    def _drive_for(self, *, drive_id: str, drive_slug: str, folder_id: str = "") -> Any:
        """Return the actor-readable, unarchived drive a draft targets.

        A named folder belongs to exactly one drive, so a draft that names only
        its folder targets that folder's drive.
        """

        drive_model = self.model.drive.field.related_model
        if drive_id:
            drive = (
                drive_model._default_manager.db_manager(write_alias(self.model, using=self._db))
                .all()
                .from_public_id(str(drive_id))
            )
        elif folder_id and not drive_slug:
            folder_model = self.model.folder.field.related_model
            folder = (
                folder_model._default_manager.db_manager(write_alias(self.model, using=self._db))
                .all()
                .from_public_id(str(folder_id))
            )
            drive = None if folder is None or folder.is_virtual else folder.drive
        else:
            slug = drive_slug or str(settings.ANGEE_STORAGE_DEFAULT_DRIVE)
            drive = (
                drive_model._default_manager.db_manager(write_alias(self.model, using=self._db))
                .filter(slug=slug)
                .first()
            )
        if drive is None:
            raise exceptions.UploadTargetNotFound("drive not found")
        if drive.is_archived:
            raise exceptions.UploadConflict("drive is archived")
        return drive

    def _folder_for(self, folder_id: str, *, drive: Any) -> Any | None:
        """Return the actor-readable real folder for a draft, if one is named."""

        if not folder_id:
            return None
        folder_model = self.model.folder.field.related_model
        folder = (
            folder_model._default_manager.db_manager(write_alias(self.model, using=self._db))
            .all()
            .from_public_id(str(folder_id))
        )
        try:
            self.model.validate_placement(folder, drive.pk)
        except ValidationError as error:
            if error.code == "folder_not_found":
                raise exceptions.UploadTargetNotFound("folder not found") from error
            raise exceptions.UploadConflict("folder does not belong to the drive") from error
        return folder


_FileAttachmentManagerBase = AngeeHistoryManager.from_queryset(FileAttachmentQuerySet)


class FileAttachmentManager[AttachmentT: FileAttachment](_FileAttachmentManagerBase[AttachmentT]):
    """Owns the polymorphic file edge, stored at :func:`rebac.generic_target`.

    A record and each of its REBAC-typed MTI ancestors share one attachment set.
    The edge's ``create`` in ``permissions.zed`` requires write on the target
    through the relation its owning app declares; edges are inserted under the
    actor, so a target type without a relation is refused.
    """

    def attach(self, file: Any, record: models.Model, *, label: str = "") -> AttachmentT:
        """Attach a readable file to a writable record, idempotently per edge.

        The ambient actor needs ``read`` on the file and the edge's ``create``
        (write on the record). Where ``storage/file`` reads record files through
        their attachments, a new edge changes the file's own relation, so the
        library's backed-edge gate also requires ``write`` on the file.
        ``ValueError`` for a record without a REBAC type.
        """

        self = self.db_manager(write_alias(self.model, using=self._db or file._state.db, instance=file))
        with database_context(self.db):
            if not check_permission("read", to_object_ref(file)).allowed:
                raise PermissionDenied("read access to the file is required")
            if file._state.db != self.db or (record._state.db is not None and record._state.db != self.db):
                raise ValueError("File attachment target must use the file's database.")
            target = generic_target(record)
            with transaction.atomic(using=self.db):
                self._lock_target(target)
                file = type(file).system_queryset(lock=("self",)).using(self.db).get(pk=file.pk)
                return self._attach(file, target, label=label)

    def has_record_arm(self, target: GenericTarget) -> bool:
        """Whether record-scoped files read and write through this target type's attachments.

        The effective schema's ``storage/file`` arm, which a record-scoped upload
        needs beyond the attachment edge's own target relation.
        """

        schema = rebac_backend().schema()
        resource_type = model_resource_type(self.model.file.field.related_model) or ""
        definition = schema.get_definition(resource_type)
        if definition is None:
            return False
        reads = permission_sources(schema, resource_type, "read").arrows
        writes = permission_sources(schema, resource_type, "write").arrows
        for relation in definition.relations:
            if (relation.name, "read") not in reads or (relation.name, "write") not in writes:
                continue
            backing = resolve_field_backing(definition, relation)
            if (
                backing is not None
                and backing.path.startswith("attachments__")
                and backing.filters == {"visibility": FileVisibility.RECORD}
                and backing.target_model is target.content_type.model_class()
            ):
                return True
        return False

    def require_record_arm(self, target: GenericTarget) -> None:
        """Reject record-scoped uploads without the declared read/write arm."""

        if not self.has_record_arm(target):
            raise exceptions.UploadError("record model has no file attachment read/write arm")

    def _lock_target(self, target: GenericTarget) -> None:
        """Lock the canonical target before a file insert or attachment write."""

        target_model = target.content_type.model_class()
        if target_model is None:
            raise ValueError("File attachment target model is unavailable.")
        target_model._base_manager.db_manager(self.db).select_for_update(of=row_lock_paths(target_model)).get(
            pk=target.object_id
        )

    def _attach(self, file: Any, target: GenericTarget, *, label: str = "") -> AttachmentT:
        """Get or insert one edge under the ambient actor, after its verb locked the target.

        ``attach`` also requires file read; ``draft`` encloses the new file and
        its edge in one transaction.
        """

        attachment, _created = self.get_or_create(
            file_id=file.pk,
            **target.lookups(self.model, "target"),
            defaults={"label": label},
        )
        return attachment


def _mime_row(file_model: type[Any], mime_type: str, *, using: str | None = None) -> Any | None:
    """Return the taxonomy row for one MIME string, if catalogued.

    Shared by ``FileManager.draft`` (the begin-time hint) and ``File.finalize``
    (the detected value), so it lives beside the model both reach through.
    """

    if not mime_type:
        return None
    mime_model = file_model.mime_type.field.related_model
    return mime_model._default_manager.db_manager(using).filter(mime_type=mime_type.strip().lower()).first()
