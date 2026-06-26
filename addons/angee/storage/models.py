"""Source models for the storage addon.

The file domain in six models: :class:`Backend` (credentialed backend
instance), :class:`Drive` (addressable volume with its own key prefix),
:class:`Folder` (tree node or per-user smart folder), :class:`MimeType`
(reference taxonomy), :class:`File` (content-addressed row, deduplicated per
drive, soft-deleted to Trash), and :class:`FileAttachment` (polymorphic edge
from any model row to a file).

A File is created as a DRAFT targeting a backend key, then bytes arrive from
some source and :meth:`File.finalize` verifies and publishes them. Two byte
sources converge on that one ``finalize``: a client upload (``File.objects.draft``
reserves the row, :meth:`File.issue_upload_token` hands the client a one-shot URL,
:meth:`File.receive_bytes` lands the bytes) and a server-side ingest of bytes
already in hand (:meth:`FileManager.ingest_bytes` — what a sync fetching message
attachments or contact photos uses). A presigned backend or adopting bytes
already on the backend are further siblings on the same path. Renditions, virus
scanning,
extraction, and search belong to downstream addons that subscribe to
``storage.signals.file_finalized`` or attach rows through ``FileAttachment``.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import posixpath
import re
import secrets
from datetime import datetime
from typing import Any, BinaryIO, ClassVar, cast
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core import signing
from django.core.exceptions import (
    SuspiciousFileOperation,
    ValidationError,
)
from django.core.files.base import ContentFile
from django.core.files.base import File as DjangoFile
from django.db import IntegrityError, models, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.text import get_valid_filename
from rebac import (
    PermissionDenied,
    current_actor,
    system_context,
    to_object_ref,
)
from rebac.backends import backend as rebac_backend
from rebac.managers import RebacManager

from angee.base.fields import ImplClassField, StateField
from angee.base.mixins import AuditMixin, SqidMixin, actor_user_id
from angee.base.models import AngeeManager, AngeeModel, AngeeQuerySet
from angee.storage import exceptions
from angee.storage.autoconfig import setting
from angee.storage.backends import DOWNLOAD_URL_TTL_SECONDS, StorageBackend
from angee.storage.signals import file_finalized
from angee.storage.uploads import (
    DOWNLOAD_TOKEN_MAX_AGE,
    DOWNLOAD_TOKEN_SALT,
    FALLBACK_MIME,
    MIME_SNIFF_BYTES,
    UPLOAD_TOKEN_MAX_AGE,
    UPLOAD_TOKEN_SALT,
    BodyTooLarge,
    CappedReader,
    detect_mime,
    sha256_stream,
)

_SHA256_HEX = re.compile(r"[a-f0-9]{64}")


class UploadState(models.TextChoices):
    """File byte lifecycle.

    DRAFT rows hold a reserved ``storage_path`` whose backend bytes may not
    exist yet; READY rows have verified bytes, so ``content_hash`` and
    ``size_bytes`` are authoritative; FAILED rows are retained for audit and
    user feedback after a rejected upload.

    Module-scoped (not nested on :class:`File`) because :class:`FileManager`
    is defined before the model and the ``storage_prune`` command consumes
    it without loading a concrete model.
    """

    DRAFT = "draft", "Draft"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"


class Backend(SqidMixin, AuditMixin, AngeeModel):
    """Credentialed storage backend instance.

    One row names a :class:`~angee.storage.backends.StorageBackend` subclass by
    a key in ``ANGEE_STORAGE_BACKEND_CLASSES`` and carries its constructor
    config. Many drives can share one backend; credentials are written once,
    here, never on the drive.
    """

    runtime = True

    sqid_prefix = "bkd_"
    slug = models.SlugField(unique=True)
    label = models.CharField(max_length=200)
    backend_class = ImplClassField(base_class=StorageBackend, registry_setting="ANGEE_STORAGE_BACKEND_CLASSES")
    backend_config = models.JSONField(default=dict, blank=True)
    is_default = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False, db_index=True)

    _storage_cache: ClassVar[dict[tuple[Any, Any], StorageBackend]] = {}
    """Resolved backend instances keyed by ``(pk, frozen config)``."""

    class Meta:
        """Django model options for storage backends."""

        abstract = True
        ordering = ("slug",)
        rebac_resource_type = "storage/backend"
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return the operator-facing backend label."""

        return self.label or self.slug

    def resolved_config(self) -> dict[str, Any]:
        """Return ``backend_config`` with ``{"env": "VAR"}`` placeholders expanded.

        Credentials stay out of the database row; env references resolve
        against the process environment each time an instance is built.
        """

        def expand(value: Any) -> Any:
            if isinstance(value, dict):
                if set(value) == {"env"} and isinstance(value["env"], str):
                    return os.environ.get(value["env"], "")
                return {key: expand(item) for key, item in value.items()}
            if isinstance(value, list):
                return [expand(item) for item in value]
            return value

        return {key: expand(item) for key, item in dict(self.backend_config or {}).items()}

    @property
    def storage(self) -> StorageBackend:
        """Return the resolved backend instance, cached per ``(row, config)``.

        The cache key includes the raw config, so rotating credentials takes
        effect on the next lookup without a worker restart.
        """

        key = (self.pk, _frozen(self.backend_config or {}))
        cached = self._storage_cache.get(key)
        if cached is not None:
            return cached
        field = cast(ImplClassField, type(self)._meta.get_field("backend_class"))
        instance = cast(StorageBackend, field.resolve_class(self.backend_class)(backend_config=self.resolved_config()))
        self._storage_cache[key] = instance
        return instance


class Drive(SqidMixin, AuditMixin, AngeeModel):
    """Addressable storage volume on top of a backend.

    Object keys live under ``{prefix}/…`` inside the parent backend's
    namespace, so two drives can share one bucket as long as their prefixes
    differ. Drive rows are the unit of access control: per-row ``editor`` /
    ``viewer`` grants (see ``permissions.zed``) scope every folder and file
    underneath.
    """

    runtime = True

    sqid_prefix = "drv_"
    backend = models.ForeignKey(
        "storage.Backend",
        on_delete=models.PROTECT,
        related_name="drives",
    )
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    prefix = models.CharField(max_length=512, blank=True)
    is_archived = models.BooleanField(default=False, db_index=True)

    class Meta:
        """Django model options for drives."""

        abstract = True
        ordering = ("slug",)
        rebac_resource_type = "storage/drive"
        rebac_id_attr = "sqid"
        constraints = (
            # Two drives on one backend must not share a key space — purge in
            # one drive could delete bytes a row in the other references.
            models.UniqueConstraint(
                fields=("backend", "prefix"),
                name="uniq_storage_drive_backend_prefix",
            ),
        )

    def __str__(self) -> str:
        """Return the drive display name."""

        return self.name or self.slug

    @property
    def storage(self) -> StorageBackend:
        """Return the resolved backend instance for this drive.

        Backend rows are admin-gated infrastructure; the fetch runs elevated
        so any actor allowed to use the drive can perform storage IO without
        read access to the backend row itself.
        """

        with system_context(reason="storage.drive.storage"):
            return self.backend.storage

    def object_key(self, content_hash: str, filename: str) -> str:
        """Return the deterministic backend key for one digest and filename.

        ``{prefix}/{hash[:2]}/{hash[2:4]}/{hash}/{safe_filename}`` — the
        two-level digest sharding keeps any single directory small.
        """

        digest = str(content_hash).lower()
        try:
            safe_name = get_valid_filename(posixpath.basename(str(filename)))
        except SuspiciousFileOperation:
            safe_name = ""
        parts = [digest[:2], digest[2:4], digest, safe_name or "upload.bin"]
        prefix = str(self.prefix or "").strip("/")
        if prefix:
            parts.insert(0, prefix)
        return posixpath.join(*parts)


class FolderManager(AngeeManager):
    """Manager owning gated folder creation."""

    def create_in_drive(
        self,
        *,
        drive_id: str,
        name: str,
        parent_id: str = "",
        description: str = "",
    ) -> Any:
        """Create a real folder after checking ``write`` on its drive.

        The drive-write check is the create gate — a per-row REBAC ``create``
        cannot evaluate a not-yet-inserted row — so the insert rides
        per-instance ``sudo`` while the ambient actor stamps ``created_by``.
        Mirrors :meth:`FileManager.draft`.
        """

        drive_model = self.model._meta.get_field("drive").related_model
        drive = drive_model._default_manager.all().from_public_id(str(drive_id))
        if drive is None:
            raise exceptions.UploadTargetNotFound("drive not found")
        if not drive.has_access("write"):
            raise exceptions.UploadDenied("write access to the drive is required")
        parent = None
        if parent_id:
            parent = self.all().from_public_id(str(parent_id))
            if parent is None or parent.is_virtual:
                raise exceptions.UploadTargetNotFound("parent folder not found")
        folder = self.model(drive=drive, parent=parent, name=name, description=description)
        try:
            folder.full_clean()
        except ValidationError as error:
            raise exceptions.UploadError(f"invalid folder request: {error}") from error
        folder.sudo(reason="storage.folder.create")
        folder.save()
        return folder


class Folder(SqidMixin, AuditMixin, AngeeModel):
    """Tree node inside a drive, or a per-user smart folder.

    A real folder has a ``drive`` and filesystem-style uniqueness on
    ``(drive, parent, name)``. A smart folder has no drive: it belongs to one
    ``owner``, is flagged ``is_virtual``, and surfaces files through a backing
    query — Trash lists ``File.objects.trashed()`` — so it stores no edges.
    """

    runtime = True

    sqid_prefix = "fld_"

    objects = FolderManager()

    class SmartKind(models.TextChoices):
        """Backing queries a smart folder can surface."""

        TRASH = "trash", "Trash"

    drive = models.ForeignKey(
        "storage.Drive",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="folders",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    is_virtual = models.BooleanField(default=False, db_index=True, editable=False)
    smart_kind = models.CharField(
        max_length=32,
        blank=True,
        choices=SmartKind,
        editable=False,
    )

    class Meta:
        """Django model options for folders."""

        abstract = True
        ordering = ("name", "sqid")
        rebac_resource_type = "storage/folder"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("drive", "parent", "name"),
                condition=Q(is_virtual=False),
                name="uniq_storage_folder_drive_parent_name",
            ),
            # SQL unique treats NULLs as distinct, so root folders (NULL
            # parent) need their own uniqueness arm.
            models.UniqueConstraint(
                fields=("drive", "name"),
                condition=Q(is_virtual=False) & Q(parent__isnull=True),
                name="uniq_storage_folder_drive_root_name",
            ),
            models.UniqueConstraint(
                fields=("owner", "smart_kind"),
                condition=Q(is_virtual=True) & ~Q(smart_kind=""),
                name="uniq_storage_folder_owner_smart_kind",
            ),
            models.CheckConstraint(
                condition=Q(is_virtual=True) | Q(drive__isnull=False),
                name="storage_folder_real_requires_drive",
            ),
        )

    def __str__(self) -> str:
        """Return the folder name."""

        return self.name

    def clean(self) -> None:
        """Reject drive-less real folders, cross-drive parents, and cycles."""

        super().clean()
        if self.is_virtual:
            return
        if self.drive_id is None:
            raise ValidationError({"drive": "A folder requires a drive."})
        if not self.parent_id:
            return
        parent_drive_id = type(self)._base_manager.filter(pk=self.parent_id).values_list("drive_id", flat=True).first()
        if parent_drive_id != self.drive_id:
            raise ValidationError({"parent": "Parent folder belongs to another drive."})
        ancestor_id = self.parent_id
        while ancestor_id is not None:
            if ancestor_id == self.pk:
                raise ValidationError({"parent": "A folder cannot contain itself."})
            ancestor_id = type(self)._base_manager.filter(pk=ancestor_id).values_list("parent_id", flat=True).first()


class MimeType(SqidMixin, AngeeModel):
    """Reference row for one MIME type.

    The master-tier taxonomy seed is the source of truth; rows are read-only
    at runtime and deliberately carry no REBAC type, like the resource
    ledger, so any caller may read the catalogue.
    """

    runtime = True

    sqid_prefix = "mim_"
    mime_type = models.CharField(max_length=200, unique=True)
    category = models.CharField(max_length=32, db_index=True)
    label = models.CharField(max_length=200)
    icon_key = models.CharField(max_length=64, blank=True)

    class Meta:
        """Django model options for MIME types."""

        abstract = True
        ordering = ("mime_type",)
        verbose_name = "MIME type"

    def __str__(self) -> str:
        """Return the canonical MIME string."""

        return self.mime_type


class FileQuerySet(AngeeQuerySet["File"]):
    """REBAC-scoped reads for file rows."""

    def live(self) -> FileQuerySet:
        """Return rows that are not soft-deleted."""

        return cast(FileQuerySet, self.filter(is_trashed=False))

    def trashed(self) -> FileQuerySet:
        """Return soft-deleted rows — the Trash smart folder's backing query."""

        return cast(FileQuerySet, self.filter(is_trashed=True))

    def stale_drafts(self, cutoff: datetime) -> FileQuerySet:
        """Return DRAFT rows reserved before ``cutoff`` that never finalized."""

        return cast(
            FileQuerySet,
            self.filter(upload_state=UploadState.DRAFT, created_at__lt=cutoff),
        )

    def expired_trash(self, cutoff: datetime) -> FileQuerySet:
        """Return rows whose trash stay lapsed before ``cutoff``."""

        return cast(FileQuerySet, self.trashed().filter(trashed_at__lt=cutoff))

    def delete(self) -> tuple[int, dict[str, int]]:
        """Refuse bulk delete: it bypasses soft-trash and orphans backend bytes.

        ``QuerySet.delete()`` issues SQL without calling :meth:`File.delete`, so
        callers must iterate and soft-delete, or :meth:`File.purge` to hard-delete.
        """

        raise NotImplementedError(
            "Bulk File delete is disabled; call File.delete() per row to trash, or File.purge() to hard-delete."
        )


class FileManager(RebacManager.from_queryset(FileQuerySet)):  # type: ignore[misc]
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
    ) -> Any:
        """Reserve a DRAFT File targeting a backend key, or return the dedup hit.

        Content-addressed get-or-create: when ``content_hash`` names bytes the
        drive already holds READY, that row is returned (restored if trashed)
        and nothing needs writing — callers branch on ``upload_state``. The
        actor must hold ``write`` on the drive; that check is the create gate,
        since a per-row REBAC ``create`` cannot evaluate a not-yet-inserted row.
        """

        drive = self._drive_for(drive_id=drive_id, drive_slug=drive_slug)
        folder = self._folder_for(folder_id, drive=drive)
        if not drive.has_access("write"):
            raise exceptions.UploadDenied("write access to the drive is required")

        digest = _normalized_hash(content_hash) if content_hash else ""
        if digest:
            existing = self.filter(
                drive_id=drive.pk,
                content_hash=digest,
                upload_state=UploadState.READY,
            ).first()
            if existing is not None:
                # A trashed hit would be purge-doomed and would block the
                # re-upload through the dedup constraint — bring it back.
                if existing.is_trashed:
                    existing.restore()
                return existing

        placeholder = secrets.token_hex(32)
        row = self.model(
            drive=drive,
            folder=folder,
            filename=filename,
            mime_type=_mime_row(self.model, mime_type),
            size_bytes=max(int(size_bytes or 0), 0),
            content_hash=placeholder,
            storage_path=drive.object_key(digest or placeholder, filename),
            upload_state=UploadState.DRAFT,
        )
        try:
            row.full_clean()
        except ValidationError as error:
            raise exceptions.UploadError(f"invalid file request: {error}") from error
        # The insert rides per-instance sudo (the gate above already ran) while
        # the ambient actor still stamps created_by — the file's owner relation.
        row.sudo(reason="storage.file.draft")
        row.save()
        return row

    def ingest_bytes(
        self,
        content: bytes,
        *,
        filename: str,
        owner_id: Any = None,
        drive_id: str = "",
        drive_slug: str = "",
        folder_id: str = "",
    ) -> Any:
        """Ingest bytes already in hand into a drive and return the READY File.

        The server-side sibling of the proxy upload: the bytes are trusted (a
        sync fetched them), so this writes them to the content-addressed key and
        finalizes in one call instead of minting an upload token. Idempotent —
        bytes the drive already holds READY return the existing row. ``owner_id``
        stamps ``created_by`` (the file's ``owner`` relation); the whole verb runs
        elevated, so a sync needs no per-actor drive grant, and read access is
        scoped afterwards by the stamped owner.
        """

        digest = hashlib.sha256(content).hexdigest()
        with system_context(reason="storage.file.ingest_bytes"):
            row = self.draft(
                filename=filename,
                size_bytes=len(content),
                drive_id=drive_id,
                drive_slug=drive_slug,
                folder_id=folder_id,
                content_hash=digest,
            )
            if row.upload_state == UploadState.READY:
                return row  # content-addressed dedup hit — the bytes already exist
            if owner_id is not None and row.created_by_id is None:
                # AuditMixin only stamps created_by when unset, so set the sync's
                # owner before the elevated context erases the ambient actor.
                row.created_by_id = owner_id
                row.save(update_fields=["created_by"])
            storage = row.storage
            saved_path = storage.save(row.storage_path, ContentFile(content, name=row.storage_path))
            if saved_path and saved_path != row.storage_path:
                row.storage_path = saved_path
                row.save(update_fields=["storage_path"])
            try:
                return row.finalize(expected_hash=digest, expected_size=len(content))
            except exceptions.UploadConflict:
                # A concurrent ingest of identical bytes won the dedup race; the
                # winner is READY, so resolve to it instead of failing the sync.
                winner = self.filter(
                    drive_id=row.drive_id,
                    content_hash=digest,
                    upload_state=UploadState.READY,
                ).first()
                if winner is not None:
                    return winner
                raise

    def for_upload_token(self, token: str) -> Any:
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
        file_id = str(payload.get("file") or "")
        nonce = str(payload.get("nonce") or "")
        if not file_id or not nonce:
            raise exceptions.UploadDenied("invalid upload token")
        row = self.system_context(reason="storage.upload.proxy").select_related("drive").filter(sqid=file_id).first()
        if row is None:
            raise exceptions.UploadTargetNotFound("file not found")
        if row.upload_state != UploadState.DRAFT:
            raise exceptions.UploadConflict("file is not awaiting bytes")
        envelope = dict(row.upload_envelope or {})
        if envelope.get("nonce") != nonce or envelope.get("used"):
            raise exceptions.UploadDenied("upload token already used")
        row._upload_nonce = nonce
        return row

    def for_download_token(self, token: str) -> Any:
        """Return the READY file a signed proxy download token addresses.

        The mirror of :meth:`for_upload_token`. The token is a capability: it is
        minted on the file's ``url`` field, which only resolves for an actor that
        already read the row, so the download view re-validates the signature and
        expiry alone (no second actor check). Trashed or unfinished rows have no
        servable bytes, so they are excluded here.
        """

        try:
            payload = signing.loads(token, salt=DOWNLOAD_TOKEN_SALT, max_age=DOWNLOAD_TOKEN_MAX_AGE)
        except signing.SignatureExpired as error:
            raise exceptions.UploadDenied("download token expired") from error
        except signing.BadSignature as error:
            raise exceptions.UploadDenied("invalid download token") from error
        file_id = str(payload.get("file") or "")
        if not file_id:
            raise exceptions.UploadDenied("invalid download token")
        row = (
            self.system_context(reason="storage.download.proxy")
            .select_related("mime_type")
            .filter(sqid=file_id, upload_state=UploadState.READY, is_trashed=False)
            .first()
        )
        if row is None:
            raise exceptions.UploadTargetNotFound("file not found")
        return row

    def _drive_for(self, *, drive_id: str, drive_slug: str) -> Any:
        """Return the actor-readable, unarchived drive a draft targets."""

        drive_model = self.model._meta.get_field("drive").related_model
        if drive_id:
            drive = drive_model._default_manager.all().from_public_id(str(drive_id))
        else:
            slug = drive_slug or str(setting("ANGEE_STORAGE_DEFAULT_DRIVE"))
            drive = drive_model._default_manager.filter(slug=slug).first()
        if drive is None:
            raise exceptions.UploadTargetNotFound("drive not found")
        if drive.is_archived:
            raise exceptions.UploadConflict("drive is archived")
        return drive

    def _folder_for(self, folder_id: str, *, drive: Any) -> Any | None:
        """Return the actor-readable real folder for a draft, if one is named."""

        if not folder_id:
            return None
        folder_model = self.model._meta.get_field("folder").related_model
        folder = folder_model._default_manager.all().from_public_id(str(folder_id))
        if folder is None or folder.is_virtual:
            raise exceptions.UploadTargetNotFound("folder not found")
        if folder.drive_id != drive.pk:
            raise exceptions.UploadConflict("folder does not belong to the drive")
        return folder


class File(SqidMixin, AuditMixin, AngeeModel):
    """A stored asset, deduplicated per drive by content hash.

    ``created_by`` (stamped by :class:`~angee.base.mixins.AuditMixin`) is the
    uploader and backs the ``owner`` relation in ``permissions.zed``.
    ``delete()`` soft-trashes; :meth:`purge` is the real delete.
    """

    runtime = True

    sqid_prefix = "fil_"
    drive = models.ForeignKey(
        "storage.Drive",
        on_delete=models.PROTECT,
        related_name="files",
        editable=False,
    )
    folder = models.ForeignKey(
        "storage.Folder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        limit_choices_to={"is_virtual": False},
        related_name="files",
    )
    filename = models.CharField(max_length=512)
    title = models.CharField(max_length=512, blank=True)
    content_hash = models.CharField(max_length=64, db_index=True, editable=False)
    size_bytes = models.PositiveBigIntegerField(default=0, editable=False)
    mime_type = models.ForeignKey(
        "storage.MimeType",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        editable=False,
    )
    storage_path = models.CharField(max_length=2048, editable=False)
    metadata = models.JSONField(default=dict, blank=True)
    # Server-owned upload bookkeeping (proxy token nonce, failure reason).
    # Kept apart from client-writable ``metadata`` so a file writer can never
    # reset a consumed token, and never exposed on the GraphQL type.
    upload_envelope = models.JSONField(default=dict, blank=True, editable=False)
    upload_state = StateField(
        choices_enum=UploadState,
        default=UploadState.DRAFT,
        editable=False,
    )
    is_trashed = models.BooleanField(default=False, db_index=True, editable=False)
    trashed_at = models.DateTimeField(null=True, blank=True, editable=False)
    trashed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        editable=False,
    )

    objects = FileManager()

    class Meta:
        """Django model options for files."""

        abstract = True
        ordering = ("-updated_at", "filename", "sqid")
        rebac_resource_type = "storage/file"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("drive", "content_hash"),
                name="uniq_storage_file_drive_content_hash",
            ),
        )

    def __str__(self) -> str:
        """Return the display title or original filename."""

        return self.title or self.filename

    def clean(self) -> None:
        """Reject placement in a smart folder or a folder outside this drive."""

        super().clean()
        if self.folder_id:
            folder_model = type(self)._meta.get_field("folder").related_model
            folder = folder_model._base_manager.filter(pk=self.folder_id).values("drive_id", "is_virtual").first()
            if folder is None or folder["is_virtual"] or folder["drive_id"] != self.drive_id:
                raise ValidationError({"folder": "Folder must be a real folder in this file's drive."})

    @property
    def storage(self) -> StorageBackend:
        """Return the resolved backend for this row's drive.

        One elevated query joins drive and backend; the instance itself comes
        from the per-``(row, config)`` backend cache.
        """

        drive_model = type(self)._meta.get_field("drive").related_model
        with system_context(reason="storage.file.storage"):
            drive = drive_model._base_manager.select_related("backend").get(pk=self.drive_id)
            return drive.storage

    @property
    def url(self) -> str:
        """Return the backend download URL — presigned when the backend supports it.

        The GraphQL ``url`` field serves the token proxy URL instead (see
        :meth:`download_url`); this is the raw backend address its fallback uses.
        """

        storage = self.storage
        presigned = storage.presigned_get(self.storage_path, expires_in=DOWNLOAD_URL_TTL_SECONDS)
        return presigned or storage.url(self.storage_path)

    def issue_download_token(self) -> str:
        """Return a TTL-limited signed token authorizing a proxy download.

        The mirror of :meth:`issue_upload_token`, minus the nonce — a download is
        idempotent (re-fetchable, range-requestable) within the token's life, so
        it is a reusable capability rather than one-shot. Expiry rides on the
        signature (``DOWNLOAD_TOKEN_MAX_AGE``).
        """

        return signing.dumps({"file": str(self.sqid)}, salt=DOWNLOAD_TOKEN_SALT)

    def download_url(self, request: Any | None = None) -> str:
        """Return the token-authenticated proxy download URL for this file.

        The filename rides in the path (so the browser saves under it and the URL
        reads cleanly); the signed ``token`` identifies the row. Built absolute
        against ``request`` when one is given, otherwise root-relative.
        """

        query = urlencode({"token": self.issue_download_token()})
        path = f"{reverse('storage_download', args=[self.filename])}?{query}"
        return request.build_absolute_uri(path) if request is not None else path

    def open_stream(self) -> BinaryIO:
        """Open this file's stored bytes for reading (the download view streams it)."""

        return self.storage.open(self.storage_path, "rb")

    def issue_upload_token(self) -> str:
        """Return a one-shot signed token authorizing a proxy byte push.

        The nonce persists in :attr:`upload_envelope` so the token can be
        consumed exactly once; expiry rides on the signature
        (``UPLOAD_TOKEN_MAX_AGE``).
        """

        nonce = secrets.token_urlsafe(24)
        envelope = dict(self.upload_envelope or {})
        envelope.update(nonce=nonce, used=False)
        type(self)._base_manager.filter(pk=self.pk).update(upload_envelope=envelope)
        self.upload_envelope = envelope
        return signing.dumps({"file": str(self.sqid), "nonce": nonce}, salt=UPLOAD_TOKEN_SALT)

    def receive_bytes(self, body: BinaryIO) -> None:
        """Stream a proxied request body into this row's backend key.

        One byte source for the upload flow: the actor must be the uploader
        (``created_by``) or hold ``write`` on the drive. The one-shot token is
        consumed atomically before the write, the body is capped at
        ``ANGEE_STORAGE_PROXY_UPLOAD_MAX_BYTES``, and an overflow or backend
        error marks the row FAILED and removes the partial object.
        """

        self._authorize_push()
        self._consume_upload_token()
        max_bytes = int(setting("ANGEE_STORAGE_PROXY_UPLOAD_MAX_BYTES"))
        storage = self.storage
        wrapper = DjangoFile(CappedReader(body, max_bytes=max_bytes), name=self.storage_path)
        try:
            saved_path = storage.save(self.storage_path, wrapper)
        except BodyTooLarge as error:
            storage.discard(self.storage_path, context="proxy.too_large")
            self._fail(reason="too_large")
            raise exceptions.UploadTooLarge(f"proxy upload exceeds {max_bytes} bytes") from error
        except Exception:
            self._fail(reason="proxy_error")
            raise
        if saved_path and saved_path != self.storage_path:
            # The backend rewrote the key (e.g. a name collision); trust it.
            self.storage_path = saved_path
            with system_context(reason="storage.upload.proxy_path"):
                self.save(update_fields=["storage_path"])

    def finalize(self, *, expected_hash: str = "", expected_size: int | None = None) -> File:
        """Verify the bytes at this row's key and publish it READY.

        Computes the SHA-256, size, and MIME from the actual stored bytes,
        dedups per drive, and flips to READY. ``expected_hash`` /
        ``expected_size`` are asserted against the computed values when a
        source supplies them (the upload path does); a mismatch fails the row
        and raises :class:`exceptions.UploadConflict`. Idempotent on an already
        READY row; a late READY duplicate restores the winner and conflicts.
        """

        if self.upload_state == UploadState.READY:
            return self
        if self.upload_state == UploadState.FAILED:
            raise exceptions.UploadConflict("upload already failed")
        if not self.has_access("write"):
            raise exceptions.UploadDenied("write access to the file is required")

        storage = self.storage
        try:
            stream = storage.open(self.storage_path, "rb")
        except OSError as error:
            raise exceptions.UploadTargetNotFound("the bytes are not visible yet") from error
        try:
            actual_hash, actual_size, head = sha256_stream(stream, capture_head=MIME_SNIFF_BYTES)
        finally:
            with contextlib.suppress(OSError):
                stream.close()

        if expected_hash and _normalized_hash(expected_hash) != actual_hash:
            storage.discard(self.storage_path, context="finalize.mismatch")
            self._fail(reason="hash_mismatch")
            raise exceptions.UploadConflict("the bytes do not match the declared hash")
        if expected_size is not None and int(expected_size) != actual_size:
            storage.discard(self.storage_path, context="finalize.mismatch")
            self._fail(reason="size_mismatch")
            raise exceptions.UploadConflict("the bytes do not match the declared size")

        duplicate = (
            type(self)
            ._base_manager.filter(drive_id=self.drive_id, content_hash=actual_hash, upload_state=UploadState.READY)
            .exclude(pk=self.pk)
            .first()
        )
        if duplicate is not None:
            self._yield_to_duplicate(duplicate, storage=storage)

        self.content_hash = actual_hash
        self.size_bytes = actual_size
        self.mime_type = _mime_row(type(self), detect_mime(head, self.filename)) or _mime_row(type(self), FALLBACK_MIME)
        self.upload_state = cast(UploadState, UploadState.READY)
        try:
            with transaction.atomic():
                self.save(update_fields=["content_hash", "size_bytes", "mime_type", "upload_state", "updated_at"])
        except IntegrityError as error:
            # A concurrent finalize for the same bytes won the dedup constraint.
            winner = (
                type(self)
                ._base_manager.filter(drive_id=self.drive_id, content_hash=actual_hash)
                .exclude(pk=self.pk)
                .first()
            )
            if winner is not None:
                self._yield_to_duplicate(winner, storage=storage)
            self._fail(reason="duplicate")
            raise exceptions.UploadConflict("identical bytes already exist") from error
        self._emit_finalized()
        return self

    def _authorize_push(self) -> None:
        """Allow the uploader (``created_by``) or any drive writer to push bytes."""

        actor = current_actor()
        user_id = actor_user_id(actor)
        if actor is None or user_id is None:
            raise exceptions.UploadDenied("an authenticated user is required")
        if str(self.created_by_id or "") == str(user_id):
            return
        allowed = rebac_backend().check_access(subject=actor, action="write", resource=to_object_ref(self.drive))
        if not allowed.allowed:
            raise exceptions.UploadDenied("only the uploader may push bytes")

    def _consume_upload_token(self) -> None:
        """Spend the one-shot proxy token atomically, failing closed on reuse.

        The lock is real on PostgreSQL; SQLite ignores ``FOR UPDATE`` and
        relies on its single-writer serialization instead.
        """

        nonce = getattr(self, "_upload_nonce", None)
        with system_context(reason="storage.upload.consume_token"), transaction.atomic():
            locked = type(self)._base_manager.select_for_update().filter(pk=self.pk).first()
            if locked is None:
                raise exceptions.UploadTargetNotFound("file not found")
            if locked.upload_state != UploadState.DRAFT:
                raise exceptions.UploadConflict("file is not awaiting bytes")
            envelope = dict(locked.upload_envelope or {})
            # Bind to the token resolved by for_upload_token: a re-issued token
            # rotates the nonce, so spending under the lock must match it.
            if envelope.get("used") or (nonce is not None and envelope.get("nonce") != nonce):
                raise exceptions.UploadDenied("upload token already used")
            envelope["used"] = True
            locked.upload_envelope = envelope
            locked.save(update_fields=["upload_envelope"])
        self.upload_envelope = locked.upload_envelope

    def _yield_to_duplicate(self, duplicate: File, *, storage: StorageBackend) -> None:
        """Concede a dedup race: clean our bytes, revive the winner, fail this row.

        Never discards a key the winner shares — on an overwriting backend
        that would delete the surviving row's bytes. A trashed winner is
        restored (elevated: it may belong to another owner) so the caller's
        conflict points at a live row rather than a purge-doomed one.
        """

        if duplicate.storage_path != self.storage_path:
            storage.discard(self.storage_path, context="finalize.duplicate")
        if duplicate.is_trashed:
            with system_context(reason="storage.finalize.restore_duplicate"):
                duplicate.restore()
        self._fail(reason="duplicate")
        raise exceptions.UploadConflict(f"identical bytes already exist: {duplicate.sqid}")

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Soft-delete into the Trash smart folder; backend bytes stay.

        :meth:`purge` (or the ``storage_prune`` command after the trash TTL)
        does the real delete. The soft path persists through ``save()``, so
        the library's ``delete`` gate would never fire — check it explicitly
        to keep the zed ``delete`` permission live.
        """

        del args, kwargs
        if self.is_trashed:
            return (0, {})
        if not self.has_access("delete"):
            raise PermissionDenied(f"Denied: cannot delete {self._meta.label} {self.public_id}")
        self.is_trashed = True
        self.trashed_at = timezone.now()
        self.trashed_by_id = actor_user_id(current_actor())
        self.save(update_fields=["is_trashed", "trashed_at", "trashed_by", "updated_at"])
        return (1, {self._meta.label: 1})

    def restore(self) -> None:
        """Reverse a previous soft-delete."""

        if not self.is_trashed:
            return
        self.is_trashed = False
        self.trashed_at = None
        self.trashed_by = None
        self.save(update_fields=["is_trashed", "trashed_at", "trashed_by", "updated_at"])

    def purge(self) -> None:
        """Really delete: remove the row, then the backend object.

        Backend failures never block the row deletion. A failed backend
        delete is accepted as an orphaned object — rows are the source of
        truth and keys are content-addressed, so an orphan can only waste
        space, never serve stale content under a live row.
        """

        storage = self.storage
        key = self.storage_path
        super().delete()
        storage.discard(key, context="purge")

    def _fail(self, *, reason: str) -> None:
        """Transition this row to FAILED, recording why for audit surfaces."""

        envelope = dict(self.upload_envelope or {})
        envelope["failure_reason"] = reason
        self.upload_envelope = envelope
        self.upload_state = cast(UploadState, UploadState.FAILED)
        with system_context(reason=f"storage.upload.failed.{reason}"):
            self.save(update_fields=["upload_envelope", "upload_state", "updated_at"])

    def _emit_finalized(self) -> None:
        """Fire ``file_finalized`` for downstream addons once the row commits."""

        actor = current_actor()
        transaction.on_commit(
            lambda: file_finalized.send(sender=type(self), instance=self, actor=actor),
        )


class FileAttachment(SqidMixin, AuditMixin, AngeeModel):
    """Polymorphic edge attaching one :class:`File` to any model row.

    Consumers attach explicitly (create a row against the concrete model) or
    declare a ``GenericRelation("storage.FileAttachment")`` on the target for
    an ergonomic reverse accessor. Access control rides entirely on the file
    parent — see ``permissions.zed``.
    """

    runtime = True

    sqid_prefix = "fat_"
    file = models.ForeignKey(
        "storage.File",
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey("content_type", "object_id")
    label = models.CharField(max_length=200, blank=True)

    class Meta:
        """Django model options for file attachments."""

        abstract = True
        ordering = ("-created_at", "sqid")
        rebac_resource_type = "storage/file_attachment"
        rebac_id_attr = "sqid"
        indexes = (models.Index(fields=("content_type", "object_id")),)

    def __str__(self) -> str:
        """Return the attachment label or a file-qualified fallback."""

        return self.label or f"attachment:{self.file_id}"


class StorageRole(AngeeModel):
    """Table-less REBAC type anchor for the ``storage/role`` namespace.

    The const-backed ``admin`` relation on ``storage/role`` (``permissions.zed``)
    needs a model carrying its ``rebac_resource_type`` to satisfy the
    ``rebac.E009`` system check — the same anchor operator's connection uses. The
    row is never created or read; it exists only to register the type so a
    platform admin resolves as an effective storage-admin through the const.
    """

    runtime = True

    class Meta:
        """Django model options for the storage role anchor."""

        abstract = True
        managed = False
        rebac_resource_type = "storage/role"


def _mime_row(file_model: type[Any], mime_type: str) -> Any | None:
    """Return the taxonomy row for one MIME string, if catalogued.

    Shared by ``FileManager.draft`` (the begin-time hint) and ``File.finalize``
    (the detected value), so it lives beside the model both reach through.
    """

    if not mime_type:
        return None
    mime_model = file_model._meta.get_field("mime_type").related_model
    return mime_model._default_manager.filter(mime_type=mime_type.strip().lower()).first()


def _frozen(value: Any) -> Any:
    """Return ``value`` recursively converted to a hashable cache key."""

    if isinstance(value, dict):
        return tuple(sorted((key, _frozen(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(_frozen(item) for item in value)
    return value


def _normalized_hash(value: str) -> str:
    """Return the lowercase SHA-256 hex digest, rejecting malformed input."""

    digest = str(value or "").strip().lower()
    if not _SHA256_HEX.fullmatch(digest):
        raise exceptions.UploadError("content_hash must be a SHA-256 hex digest")
    return digest
