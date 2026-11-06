"""Native file owners for storage."""

from __future__ import annotations

import contextlib
import logging
import secrets
from collections.abc import Mapping
from io import Reader
from pathlib import Path
from typing import Any, ClassVar, Self, cast
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import (
    ValidationError,
)
from django.core.files.base import File as DjangoFile
from django.db import DEFAULT_DB_ALIAS, IntegrityError, models, transaction
from django.db.models.deletion import ProtectedError
from django.db.models.fields.reverse_related import ManyToOneRel
from django.db.models.signals import post_save
from django.urls import reverse
from django.utils import timezone
from rebac import (
    ActorLike,
    NoActorResolvedError,
    PermissionDenied,
    current_actor,
    database_context,
    system_context,
    to_object_ref,
    to_subject_ref,
)
from rebac.backends import backend as rebac_backend
from rebac.database import write_alias

from angee.base.fields.state import StateField
from angee.iam.identity import public_subject_ref
from angee.iam.models.base import ProtectedDataModel
from angee.iam.models.ownership import OwnerMixin
from angee.iam.models.trash import ProtectedTrashMixin
from angee.iam.permissions import actor_user_id, instance_actor
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.contrib.angee.managers import AngeeHistoryUnscopedManager
from angee.storage import exceptions
from angee.storage.backends import DOWNLOAD_URL_TTL_SECONDS, StorageBackend
from angee.storage.choices import FileVisibility, UploadState
from angee.storage.managers import (
    FileManager,
    _mime_row,
)
from angee.storage.signals import file_finalized
from angee.storage.uploads import (
    DOWNLOAD_TOKEN_SALT,
    FALLBACK_MIME,
    MIME_SNIFF_BYTES,
    UPLOAD_TOKEN_SALT,
    BodyTooLarge,
    CappedReader,
    _normalized_hash,
    detect_mime,
    require_image_size,
    require_image_type,
    sha256_stream,
)
from angee.tags.models import TaggedModel

logger = logging.getLogger(__name__)


class File(TaggedModel, ProtectedTrashMixin, OwnerMixin, HistoryMixin, ProtectedDataModel, concrete=True):
    """A stored asset, deduplicated per drive by content hash.

    ``owner`` grants access; ``created_by`` retains upload attribution.
    ``delete()`` moves the file to the trash; :meth:`purge` is the real delete.
    """

    record_display_paths: ClassVar[tuple[str, ...]] = ("title", "filename")

    def _grant_dedup_reach(self, owner_id: Any) -> None:
        """Grant a dedup-hit file read reach to the requested ingest owner."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        with database_context(using), transaction.atomic(using=using):
            locked = type(self).system_queryset(lock=("self",)).using(using).get(pk=self.pk)
            if locked.visibility != FileVisibility.INHERITED:
                raise exceptions.UploadConflict("identical bytes already exist")
            if owner_id is None or str(locked.owner_id or "") == str(owner_id):
                return
            user = get_user_model().objects.db_manager(using).get(pk=owner_id)
            locked.grant_record_access("viewer", user)

    @staticmethod
    def merged_metadata(
        current: Any,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        """Return ``current`` recursively merged with ``updates``.

        A nested :class:`~collections.abc.Mapping` under any key is merged
        recursively, so a caller updating one owner's sub-dict keeps every other
        owner's facts; a non-mapping value (or a value replacing a non-mapping)
        overwrites outright. The metadata schema is opaque to storage — an owner
        namespaces its own keys and this merge names none of them.
        """

        merged = dict(current) if isinstance(current, Mapping) else {}
        for key, value in updates.items():
            existing = merged.get(key)
            if isinstance(existing, Mapping) and isinstance(value, Mapping):
                merged[key] = File.merged_metadata(existing, dict(value))
            else:
                merged[key] = value
        return merged

    _upload_nonce: str | None = None

    rebac_grantable = {"viewer": "share"}
    owner_container = "drive"

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
    visibility = StateField(choices_enum=FileVisibility, default=FileVisibility.INHERITED, editable=False)
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

    objects = FileManager()
    unscoped_objects = AngeeHistoryUnscopedManager()

    class Meta:
        """Django model options for files."""

        abstract = True
        base_manager_name = "unscoped_objects"
        ordering = ("-updated_at", "filename", "sqid")
        rebac_resource_type = "storage/file"
        constraints = (
            models.UniqueConstraint(
                fields=("drive", "content_hash"),
                name="uniq_storage_file_drive_content_hash",
            ),
        )
        indexes = (models.Index(fields=("drive", "storage_path")),)

    def merge_metadata(self, metadata: dict[str, Any] | None) -> None:
        """Merge namespaced metadata under the row lock without replacing siblings."""

        if not metadata:
            return
        rows = (
            type(self)
            .objects.db_manager(write_alias(type(self), using=self._state.db, instance=self), hints={"instance": self})
            .all()
        )
        actor = self.actor()
        if actor is not None:
            rows = rows.with_actor(actor)
        with database_context(rows.db), transaction.atomic(using=rows.db):
            locked = rows.lock_if_supported().get(pk=self.pk)
            merged = self.merged_metadata(locked.metadata, metadata)
            if merged != locked.metadata:
                locked.metadata = merged
                locked.save(using=locked._state.db, update_fields=["metadata", "updated_at"])
            self.metadata = merged
            self.updated_at = locked.updated_at

    def __str__(self) -> str:
        """Return the display title or original filename."""

        return self.title or self.filename

    def clean(self) -> None:
        """Reject placement in a smart folder or a folder outside this drive."""

        super().clean()
        if self.folder_id:
            folder_model = type(self).folder.field.related_model
            folder = (
                folder_model._base_manager.db_manager(
                    write_alias(type(self), using=self._state.db, instance=self), hints={"instance": self}
                )
                .filter(
                    pk=self.folder_id,
                )
                .only("drive_id", "is_virtual")
                .first()
            )
            try:
                self.validate_placement(folder, self.drive_id)
            except ValidationError as error:
                raise ValidationError({"folder": error}) from error

    @staticmethod
    def validate_placement(folder: Any, drive_id: Any) -> None:
        """Require one real folder in the selected drive, independent of carrier lookup."""

        message = "Folder must be a real folder in this file's drive."
        if folder is None or folder.is_virtual:
            raise ValidationError(message, code="folder_not_found")
        if folder.drive_id != drive_id:
            raise ValidationError(message, code="wrong_drive")

    def set_visibility(self, value: FileVisibility | str) -> Self:
        """Narrow with file write, or widen with drive share, retaining all grants."""

        using = write_alias(type(self), using=self._state.db, instance=self)

        try:
            visibility = FileVisibility(value)
        except ValueError as error:
            raise ValidationError({"visibility": "Invalid file visibility."}) from error
        actor = instance_actor(self)
        if actor is None:
            raise PermissionDenied("File visibility requires an acting user.")
        with database_context(using), transaction.atomic(using=using):
            locked = type(self).system_queryset(lock=("self",)).using(using).select_related("drive").get(pk=self.pk)
            resource = locked.drive if visibility == FileVisibility.INHERITED else locked
            permission = "share" if visibility == FileVisibility.INHERITED else "write"
            if not resource.with_actor(actor).has_access(permission):
                raise PermissionDenied("You cannot change file visibility.")
            if locked.visibility != visibility:
                locked.visibility = visibility
                locked.sudo(reason="storage.file.set_visibility").save(using=using, update_fields=["visibility"])
            self.visibility = locked.visibility
        return self

    @property
    def storage(self) -> StorageBackend:
        """Return the resolved backend for this row's drive.

        Resolve an uncached drive and its backend structurally in one query.
        The file's caller owns access; the backend instance comes from the
        per-``(row, config)`` cache.
        """

        drive_field = type(self).drive.field
        if drive_field.is_cached(self):
            return self.drive.storage
        with database_context(write_alias(type(self), using=self._state.db, instance=self)):
            drive_field.set_cached_value(
                self,
                drive_field.related_model._base_manager.db_manager(self._state.db, hints={"instance": self})
                .select_related("backend")
                .get(pk=self.drive_id),
            )
            return self.drive.backend.storage

    def local_path(self) -> Path | None:
        """Return this file's real on-disk path when its backend exposes one.

        Reference-mode mounts store rows at real filesystem paths (a
        ``FileSystemStorage`` rooted at the mount folder), which lets a mount
        extractor open the bytes in place; object-store backends have no local
        path and return ``None``. This owns the backend probe so no caller
        reaches into the storage shape from outside.
        """

        path_fn = getattr(self.storage, "path", None)
        if path_fn is None:
            return None
        try:
            return Path(path_fn(self.storage_path))
        except NotImplementedError, ValueError:
            return None

    @property
    def url(self) -> str:
        """Return the backend download URL — presigned when the backend supports it.

        The GraphQL ``url`` field serves the token proxy URL instead (see
        :meth:`download_url`); this is the raw backend address its fallback uses.
        """

        storage = self.storage
        presigned = storage.presigned_get(self.storage_path, expires_in=DOWNLOAD_URL_TTL_SECONDS)
        return presigned or storage.url(self.storage_path)

    def _token_identity(self) -> dict[str, str]:
        """Retain this stored row's database in its signed proxy identity.

        Default payloads retain their historical shape. Tokens issued before
        database identity was carried identify default storage only.
        """

        identity = {"file": str(self.sqid)}
        using = write_alias(type(self), using=self._state.db, instance=self)
        if using != DEFAULT_DB_ALIAS:
            identity["database"] = using
        return identity

    def issue_download_token(self, actor: ActorLike | None = None, *, parent: models.Model | None = None) -> str:
        """Sign a reusable download token for the given or ambient actor.

        An actor is required even in system context; missing actor state raises
        ``NoActorResolvedError``. The claim uses the actor's public identity.
        The token remains a bearer capability; download lookup re-checks that
        actor's ``read`` permission on every request. A projected token also
        re-checks its parent row and live attachment. Expiry rides on the
        signature (``DOWNLOAD_TOKEN_MAX_AGE``).
        """

        subject = current_actor() if actor is None else to_subject_ref(actor)
        if subject is None:
            raise NoActorResolvedError("an actor is required to issue a download token")
        claim = {**self._token_identity(), "actor": str(public_subject_ref(subject))}
        if parent is not None:
            if parent.pk is None:
                raise ValueError("a saved download parent is required")
            claim.update(parent_model=parent._meta.label, parent_id=str(parent.pk))
        return signing.dumps(claim, salt=DOWNLOAD_TOKEN_SALT)

    def download_url(self, request: Any | None = None, *, parent: models.Model | None = None) -> str:
        """Return the token-authenticated proxy download URL for this file.

        The filename rides in the path (so the browser saves under it and the URL
        reads cleanly); the signed ``token`` identifies the row and the ambient
        actor whose read permission is re-checked. ``parent`` binds a projected
        URL to the admitted part or record. Built absolute against
        ``request`` when one is given, otherwise root-relative.
        """

        query = urlencode({"token": self.issue_download_token(parent=parent)})
        path = f"{reverse('storage_download', args=[self.filename])}?{query}"
        return request.build_absolute_uri(path) if request is not None else path

    def open_stream(self) -> DjangoFile[bytes]:
        """Open this file's stored bytes for reading (the download view streams it)."""

        return self.storage.open(self.storage_path, "rb")

    def read_verified(self, *, max_bytes: int, expected_digest: str = "") -> bytes:
        """Read bounded bytes and verify their stored size and SHA-256 identity.

        Callers own actor-scoped access before reading. ``expected_digest`` also
        checks a retained reference against this row's current content identity.
        Backend IO errors propagate; invalid state or bytes raise ValidationError.
        """

        if max_bytes < 0:
            raise ValueError("A file read requires a nonnegative byte limit.")
        if self.upload_state != UploadState.READY:
            raise ValidationError("The file is not ready.")
        if expected_digest and self.content_hash != expected_digest:
            raise ValidationError("The file content identity changed.")
        if self.size_bytes > max_bytes:
            raise ValidationError("The file exceeds the byte limit.")
        try:
            with self.open_stream() as stream:
                digest, size, content = sha256_stream(
                    CappedReader(stream, max_bytes=max_bytes),
                    capture_head=max_bytes,
                )
        except BodyTooLarge as error:
            raise ValidationError("The file exceeds the byte limit.") from error
        if size != self.size_bytes or digest != self.content_hash:
            raise ValidationError("The stored bytes do not match the file identity.")
        return content

    def read_image(self, *, expected_digest: str = "") -> bytes:
        """Read this file's verified bytes to render elsewhere, as an image :func:`require_image` admits.

        A file stored before the image rule existed may be a type it refuses or
        larger than its cap; that raises :class:`exceptions.UploadTypeRefused` or
        :class:`exceptions.UploadTooLarge` instead of passing the bytes on.
        ``expected_digest`` is as in :meth:`read_verified`.
        """

        require_image_size(self.size_bytes)
        # The read is bounded by the row's own verified size, which the cap already admitted.
        content = self.read_verified(max_bytes=self.size_bytes, expected_digest=expected_digest)
        require_image_type(content)
        return content

    def issue_upload_token(self) -> str:
        """Return a one-shot signed token authorizing a proxy byte push.

        The nonce persists in :attr:`upload_envelope` so the token can be
        consumed exactly once; expiry rides on the signature
        (``UPLOAD_TOKEN_MAX_AGE``).
        """

        with database_context(write_alias(type(self), using=self._state.db, instance=self)):
            nonce = secrets.token_urlsafe(24)
            envelope = dict(self.upload_envelope or {})
            envelope.update(nonce=nonce, used=False)
            type(self)._base_manager.db_manager(write_alias(type(self), using=self._state.db, instance=self)).filter(
                pk=self.pk
            ).update(upload_envelope=envelope)
            self.upload_envelope = envelope
            return signing.dumps({**self._token_identity(), "nonce": nonce}, salt=UPLOAD_TOKEN_SALT)

    def receive_bytes(self, body: Reader[bytes]) -> None:
        """Stream a proxied request body into this row's backend key.

        One byte source for the upload flow: the actor must hold file ``write``.
        The one-shot token is
        consumed atomically before the write, the body is capped at
        ``ANGEE_STORAGE_PROXY_UPLOAD_MAX_BYTES``, and an overflow or backend
        error marks the row FAILED and removes the partial object.
        """

        with database_context(write_alias(type(self), using=self._state.db, instance=self)):
            self._authorize_push()
            self._consume_upload_token()
            max_bytes = int(settings.ANGEE_STORAGE_PROXY_UPLOAD_MAX_BYTES)
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
                    self.save(
                        using=write_alias(type(self), using=self._state.db, instance=self),
                        update_fields=["storage_path"],
                    )

    def finalize(self, *, expected_hash: str = "", expected_size: int | None = None) -> Self:
        """Verify the bytes at this row's key and publish it READY.

        Computes the SHA-256, size, and MIME from the actual stored bytes,
        dedups per drive, and flips to READY. ``expected_hash`` /
        ``expected_size`` are asserted against the computed values when a
        source supplies them (the upload path does); a mismatch fails the row
        and raises :class:`exceptions.UploadConflict`. Idempotent on an already
        READY row; a late READY duplicate conflicts and only restores a winner
        the actor can read.
        """

        using = write_alias(type(self), using=self._state.db, instance=self)

        with database_context(using):
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
                ._base_manager.db_manager(using)
                .filter(drive_id=self.drive_id, content_hash=actual_hash, upload_state=UploadState.READY)
                .exclude(pk=self.pk)
                .first()
            )
            if duplicate is not None:
                self._yield_to_duplicate(duplicate, storage=storage)

            self.content_hash = actual_hash
            self.size_bytes = actual_size
            self.mime_type = _mime_row(type(self), detect_mime(head, self.filename), using=using) or _mime_row(
                type(self), FALLBACK_MIME, using=using
            )
            self.upload_state = cast(UploadState, UploadState.READY)
            updated_at = timezone.now()
            try:
                with database_context(using), transaction.atomic(using=using):
                    claimed = (
                        type(self)
                        ._base_manager.db_manager(using)
                        .filter(pk=self.pk, upload_state=UploadState.DRAFT)
                        .update(
                            content_hash=self.content_hash,
                            size_bytes=self.size_bytes,
                            mime_type=self.mime_type,
                            upload_state=UploadState.READY,
                            updated_at=updated_at,
                        )
                    )
            except IntegrityError as error:
                # A concurrent finalize for the same bytes won the dedup constraint.
                winner = (
                    type(self)
                    ._base_manager.db_manager(using)
                    .filter(drive_id=self.drive_id, content_hash=actual_hash)
                    .exclude(pk=self.pk)
                    .first()
                )
                if winner is not None:
                    self._yield_to_duplicate(winner, storage=storage)
                self._fail(reason="duplicate")
                raise exceptions.UploadConflict("identical bytes already exist") from error
            if not claimed:
                self.refresh_from_db(using=using)
                if self.upload_state == UploadState.READY:
                    return self
                if self.upload_state == UploadState.FAILED:
                    raise exceptions.UploadConflict("upload already failed")
                raise exceptions.UploadConflict("upload is no longer awaiting bytes")
            self.updated_at = updated_at
            self._emit_saved(
                update_fields=("content_hash", "size_bytes", "mime_type", "upload_state", "updated_at"),
            )
            self._emit_finalized()
            return self

    def _authorize_push(self) -> None:
        """Require an authenticated file writer, including record-derived writers."""

        with database_context(write_alias(type(self), using=self._state.db, instance=self)):
            actor = current_actor()
            user_id = actor_user_id(actor)
            if actor is None or user_id is None:
                raise exceptions.UploadDenied("an authenticated user is required")
            allowed = rebac_backend().check_access(subject=actor, action="write", resource=to_object_ref(self))
            if not allowed.allowed:
                raise exceptions.UploadDenied("write access to the file is required")

    def _consume_upload_token(self) -> None:
        """Spend the one-shot proxy token atomically, failing closed on reuse.

        The lock is real on PostgreSQL; SQLite ignores ``FOR UPDATE`` and
        relies on its single-writer serialization instead.
        """

        using = write_alias(type(self), using=self._state.db, instance=self)

        nonce = getattr(self, "_upload_nonce", None)
        with (
            database_context(using),
            system_context(reason="storage.upload.consume_token"),
            transaction.atomic(using=using),
        ):
            locked = type(self)._base_manager.db_manager(using).select_for_update().filter(pk=self.pk).first()
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
            locked.save(using=locked._state.db, update_fields=["upload_envelope"])
        self.upload_envelope = locked.upload_envelope

    def _yield_to_duplicate(self, duplicate: File, *, storage: StorageBackend) -> None:
        """Fail this row without identifying or restoring an unreadable duplicate.

        Never discard a key the winner shares: an overwriting backend would
        otherwise lose the surviving row's bytes.
        """

        if duplicate.storage_path != self.storage_path:
            storage.discard(self.storage_path, context="finalize.duplicate")
        actor = instance_actor(self)
        if duplicate.is_trashed and actor is not None and duplicate.with_actor(actor).has_access("read"):
            duplicate.sudo(reason="storage.finalize.restore_duplicate").restore()
        self._fail(reason="duplicate")
        raise exceptions.UploadConflict("identical bytes already exist")

    def delete(self, using: str | None = None, keep_parents: bool = False) -> tuple[int, dict[str, int]]:
        """Move into the Trash smart folder; backend bytes stay.

        :meth:`purge` (or the ``storage_prune`` command after the trash TTL)
        does the real delete. The soft path persists through ``save()``, so
        the library's ``delete`` gate would never fire — check it explicitly
        to keep the zed ``delete`` permission live.
        """

        with database_context(write_alias(type(self), using=self._state.db, instance=self)):
            del keep_parents
            if using is not None and using != self._state.db:
                raise ValueError("File deletion must use the file's database.")
            if self.is_trashed:
                return 0, {}
            if not self.has_access("delete"):
                raise PermissionDenied(f"Denied: cannot delete {self._meta.label} {self.public_id}")
            self.trash()
            return 1, {self._meta.label: 1}

    def purge_if_unretained(self) -> bool:
        """Discard an unchanged acquisition only if no references or direct shares retain it.

        A referencing owner declares retention with a Django FK or one-to-one
        relation to File whose deletion policy is not DO_NOTHING. Check its
        reverse relation on the write database and direct record grants separately.
        """

        with database_context(write_alias(type(self), using=self._state.db, instance=self)):
            self.require_access("delete")
            rows = type(self).objects.db_manager(
                write_alias(type(self), using=self._state.db, instance=self), hints={"instance": self}
            )
            with database_context(rows.db):
                try:
                    with transaction.atomic(using=rows.db):
                        if not rows.filter(pk=self.pk).exists():
                            return False
                        try:
                            locked = self.lock_for_record_access(using=rows.db)
                        except type(self).DoesNotExist:
                            return False
                        if locked.updated_at != self.updated_at or locked.owner_id != self.owner_id:
                            return False
                        for relation in type(locked)._meta.get_fields(include_hidden=True):
                            if not isinstance(relation, ManyToOneRel) or relation.on_delete is models.DO_NOTHING:
                                continue
                            if (
                                relation.related_model._base_manager.db_manager(rows.db)
                                .filter(**{relation.field.name: locked})
                                .exists()
                            ):
                                return False
                        if type(locked).get_rebac_grantable() and locked.direct_record_access():
                            return False
                        locked.purge()
                except ProtectedError:
                    return False
            return True

    def purge(self) -> None:
        """Really delete the row and discard unreferenced backend bytes after commit.

        A shared key stays until its last file row is purged. Backend failures
        never block the row deletion. A failed backend delete is accepted as
        an orphaned object — rows are the source of
        truth and keys are content-addressed, so an orphan can only waste
        space, never serve stale content under a live row.
        """

        using = write_alias(type(self), using=self._state.db, instance=self)
        with database_context(using):
            storage = self.storage
            key = self.storage_path
            drive_id = self.drive_id
            model = type(self)
            super().delete(using=using)

            def discard_unreferenced() -> None:
                if not model.system_queryset().using(using).filter(drive_id=drive_id, storage_path=key).exists():
                    storage.discard(key, context="purge")

            transaction.on_commit(discard_unreferenced, using=using)

    def _fail(self, *, reason: str) -> None:
        """Transition this row to FAILED, recording why for audit surfaces."""

        with database_context(write_alias(type(self), using=self._state.db, instance=self)):
            envelope = dict(self.upload_envelope or {})
            envelope["failure_reason"] = reason
            self.upload_envelope = envelope
            self.upload_state = cast(UploadState, UploadState.FAILED)
            with system_context(reason=f"storage.upload.failed.{reason}"):
                self.save(
                    using=write_alias(type(self), using=self._state.db, instance=self),
                    update_fields=["upload_envelope", "upload_state", "updated_at"],
                )

    def _emit_finalized(self) -> None:
        """Fire ``file_finalized`` for downstream addons once the row commits."""

        actor = current_actor()
        transaction.on_commit(
            lambda: file_finalized.send(sender=type(self), instance=self, actor=actor),
            using=write_alias(type(self), using=self._state.db, instance=self),
        )

    def _emit_saved(self, *, update_fields: tuple[str, ...]) -> None:
        """Emit Django's save signal for the conditional finalize update."""

        post_save.send(
            sender=type(self),
            instance=self,
            created=False,
            update_fields=frozenset(update_fields),
            raw=False,
            using=self._state.db,
        )
