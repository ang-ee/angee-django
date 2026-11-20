"""Source model for external storage mounts."""

from __future__ import annotations

import contextlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, ClassVar, Self, cast

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import DataError, models, transaction
from django.utils.text import slugify
from rebac import system_context

from angee.base.fields.implementation import ImplClassField
from angee.base.fields.state import StateField
from angee.base.implementations import resolve_impl_class
from angee.integrate.errors import IntegrationError
from angee.integrate.models import (
    Bridge,
    IntegrationCreateMode,
    IntegrationLifecycle,
    IntegrationManager,
    IntegrationQuerySet,
)
from angee.integrate.sync import current_progress_bridge
from angee.storage import exceptions
from angee.storage_integrate.mounts import MountBackend, MountBrowseResult, MountEntry, MountLocation

if TYPE_CHECKING:
    from django.contrib.auth.base_user import AbstractBaseUser

    from angee.integrate.models import Credential
    from angee.storage.models import Backend

_PROGRESS_BATCH_SIZE = 200


@dataclass(frozen=True)
class _MountFileState:
    """File-owned freshness facts loaded once for one mount sync."""

    pk: Any
    content_hash: str
    size_bytes: int
    is_trashed: bool
    mtime_ns: int | None
    path: str
    """Where the row was last placed: its source path, which a path-keyed source's key already is."""


class MountMode(models.TextChoices):
    """How a mount handles bytes discovered at its external source."""

    COPY = "copy", "Copy files"
    REFERENCE = "reference", "Leave files in place"


class MountQuerySet[MountT: Mount](IntegrationQuerySet[MountT]):
    """Mount selections by their persisted source identity."""

    def for_roots(self, roots: Iterable[str]) -> Self:
        """Return the mounts of these source roots; one root is one mount across modes and source kinds."""

        return self.filter(config__root__in=tuple(roots))


_MountManagerBase = IntegrationManager.from_queryset(MountQuerySet)


class MountManager[MountT: Mount](_MountManagerBase[MountT]):
    """Provision mounts together with the drive their source synchronizes into, admitting their inputs once."""

    @staticmethod
    def backend_impl(backend_class: str) -> type[MountBackend]:
        """Resolve a configured mount backend, refusing an unknown key against ``backend_class``."""

        try:
            return cast(type[MountBackend], resolve_impl_class(MountBackend, backend_class))
        except ImproperlyConfigured as error:
            raise ValidationError({"backend_class": str(error)}) from error

    def build(self, **fields: Any) -> MountT:
        """Return an unsaved Mount (``IntegrationManager.build``), refusing an unknown ``backend_class``.

        Its backend's defaults name its vendor. It also answers its source's
        canonical root before anything is created.
        """

        self.backend_impl(str(fields.get("backend_class") or ""))
        return super().build(**fields)

    def browse_source(
        self,
        user: AbstractBaseUser,
        backend_class: str,
        *,
        credential: Credential | None = None,
        token: str = "",
    ) -> MountBrowseResult:
        """List one bounded level of a configured mount backend's source tree, before a Mount exists.

        The account is admitted as :meth:`provision` admits it. A location some
        mount already roots at is offered blocked as already mounted, unless the
        backend already blocked it for its own reason.
        """

        impl = self.backend_impl(backend_class)
        self._check_connection(user, backend_class, impl=impl, credential=credential)
        listing = impl.browse(credential=credential, token=token)
        tokens = (location.token for location in (listing.location, *listing.entries))
        with system_context(reason="storage_integrate.browse_mount_source"):
            mounted = set(self.for_roots(tokens).values_list("config__root", flat=True))

        def admitted(location: MountLocation) -> MountLocation:
            if location.token not in mounted or location.blocked_reason:
                return location
            return replace(location, is_mountable=False, blocked_reason="Already mounted")

        return replace(listing, location=admitted(listing.location), entries=tuple(map(admitted, listing.entries)))

    def provision(
        self,
        user: AbstractBaseUser,
        *,
        backend_class: str,
        name: str,
        root: str,
        mode: MountMode,
        credential: Credential | None = None,
        copy_backend: Backend | None = None,
    ) -> MountT:
        """Provision one external-source Mount and its drive, then queue its first sync.

        Every input is admitted here, once. ``root`` is the backend's source
        token (what ``browse`` offers); the backend's ``source_root`` validates it
        on the unsaved row and returns its canonical root, which the
        already-mounted check and the Mount use, so a refusal is keyed to
        ``root`` before anything is created. A backend that requires a
        credential reads with ``credential``, one of ``user``'s accounts the
        Mount reads with (``Integration.connectable_credentials``), attached
        through the integration's own attach verb; the Mount points at the
        backend's vendor. A reference mount gets its own storage backend serving
        the source in place; a copy mount stores under a key prefix of
        ``copy_backend``, a writable backend ``user`` may read, or else of the
        configured default drive's backend.
        """

        display_name = str(name).strip()
        if not display_name:
            raise ValidationError({"name": "A mount name is required."})
        impl = self.backend_impl(backend_class)
        backend_model = apps.get_model("storage", "Backend")
        drive_model = apps.get_model("storage", "Drive")
        self._check_connection(user, backend_class, impl=impl, credential=credential)
        self._check_copy_backend(user, mode=mode, copy_backend=copy_backend, backend_model=backend_model)
        source = self.build(backend_class=backend_class, config={"root": str(root)}, credential=credential, mode=mode)
        storage_backend_spec: tuple[str, dict[str, Any]] | None = None
        try:
            with contextlib.closing(source.backend) as backend:
                mount_config = {"root": backend.source_root()}
                if mode == MountMode.REFERENCE:
                    storage_backend_spec = backend.storage_backend_spec()
        except IntegrationError as error:
            raise ValidationError({"root": error.public_message}) from error
        except ValidationError as error:
            # Django ValidationError has error_dict only for field-keyed errors.
            if hasattr(error, "error_dict"):
                raise
            raise ValidationError({"root": error.messages}) from error

        with system_context(reason="storage_integrate.mount.provision"), transaction.atomic():
            if self.for_roots((mount_config["root"],)).exists():
                raise ValidationError({"root": impl.already_mounted_message()})
            slug = self._available_slug(
                display_name,
                slug_default=impl.slug_default(),
                backend_model=backend_model,
                drive_model=drive_model,
            )
            drive_slug = f"mount-{slug}"
            if storage_backend_spec is not None:
                storage_backend_key, storage_backend_config = storage_backend_spec
                storage_backend_id = backend_model.objects.create(
                    slug=drive_slug,
                    label=f"{display_name} (external)",
                    backend_class=storage_backend_key,
                    backend_config=storage_backend_config,
                    created_by_id=user.pk,
                ).pk
                prefix = ""
            else:
                if copy_backend is not None:
                    storage_backend_id = copy_backend.pk
                else:
                    default = drive_model.objects.configured_default()
                    if default is None:
                        raise ImproperlyConfigured("The configured default storage drive is missing.")
                    storage_backend_id = default.backend_id
                prefix = f"mounts/{slug}"
            drive = drive_model.objects.create(
                backend_id=storage_backend_id,
                slug=drive_slug,
                name=display_name,
                prefix=prefix,
                owner_id=user.pk,
                created_by_id=user.pk,
            )
            mount = self.create(
                vendor_id=source.vendor_id,
                owner_id=user.pk,
                display_name=display_name,
                backend_class=backend_class,
                drive_id=drive.pk,
                mode=mode,
                lifecycle=IntegrationLifecycle.DISCONNECTED,
                config=mount_config,
                created_by_id=user.pk,
            )
            if credential is None:
                mount.connect()
            else:
                mount.attach_connection(credential)
        mount.request_sync()
        return mount

    def _check_connection(
        self,
        user: AbstractBaseUser,
        backend_class: str,
        *,
        impl: type[MountBackend],
        credential: Credential | None,
    ) -> None:
        """Require a credential exactly when the backend reads with one, and only one of ``user``'s it may use."""

        if not impl.requires_credential:
            if credential is not None:
                raise ValidationError({"credential": f"A {impl.label} mount reads without an account."})
            return
        if credential is None:
            raise ValidationError({"credential": f"Choose the account to read this {impl.label} with."})
        with system_context(reason="storage_integrate.mount.connectable_credential"):
            readable = self.build(backend_class=backend_class).connectable_credentials(user)
            usable = readable.filter(pk=credential.pk).exists()
        if not usable:
            raise ValidationError({"credential": f"This account cannot read a {impl.label} for you."})

    @staticmethod
    def _check_copy_backend(
        user: AbstractBaseUser, *, mode: MountMode, copy_backend: Backend | None, backend_model: Any
    ) -> None:
        """Admit a chosen copy target only for a copy mount: readable by ``user``, unarchived and writable."""

        if copy_backend is None:
            return
        if mode != MountMode.COPY:
            raise ValidationError({"copy_backend": "Only a copy mount stores its files in a chosen backend."})
        if not backend_model.objects.with_actor(user).filter(pk=copy_backend.pk).exists():
            raise ValidationError({"copy_backend": "You cannot use this storage backend."})
        if copy_backend.is_archived:
            raise ValidationError({"copy_backend": "This storage backend is archived."})
        if not copy_backend.writable:
            raise ValidationError({"copy_backend": "Copied files need a writable storage backend."})

    @staticmethod
    def _available_slug(name: str, *, slug_default: str, backend_model: Any, drive_model: Any) -> str:
        """Return a slug whose mount-prefixed backend and drive slugs are unused."""

        base = slugify(name) or slug_default
        suffix = 1
        candidate = base
        while (
            backend_model.objects.filter(slug=f"mount-{candidate}").exists()
            or drive_model.objects.filter(slug=f"mount-{candidate}").exists()
        ):
            suffix += 1
            candidate = f"{base}-{suffix}"
        return candidate


class Mount(Bridge, inherits="integrate.Integration"):
    """An external storage source synchronized into a dedicated drive.

    Per-file freshness lives on the synchronized File rows, so each source run
    reads those current facts.
    """

    objects = MountManager()

    integration_create_mode: ClassVar[IntegrationCreateMode | None] = IntegrationCreateMode.CONNECT
    live_impl_field = "backend_class"

    backend_class = ImplClassField(
        MountBackend,
        default="local_folder",
        create_only=True,
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

    class Meta:
        """Django model options for the Mount integration child."""

        abstract = True
        ordering = ("-updated_at",)
        rebac_resource_type = "storage_integrate/mount"

    @property
    def backend(self) -> MountBackend:
        """Return the selected mount backend bound to this row."""

        return cast(MountBackend, self.live_impl)

    def credential_blocker(self, credential: Any) -> str | None:
        """Refuse an account this mount's backend does not read with (:meth:`reads_with`).

        Integrate admits by it on every attachment, so neither the console's
        credential patch nor a re-attachment can point a Mount at a token of
        another vendor or one that lacks the backend's scopes. One account reads
        any number of mounts.
        """

        if self.reads_with(credential):
            return None
        return f"This account cannot read a {type(self).objects.backend_impl(self.backend_class).label}."

    def reads_with(self, credential: Any) -> bool:
        """Whether the backend reads with ``credential``: only a backend that requires one, by integrate's rule."""

        requires = type(self).objects.backend_impl(self.backend_class).requires_credential
        return requires and super().reads_with(credential)

    def _next_sync_at(self, *, now: Any) -> Any:
        """Keep manual mounts unscheduled unless polling is explicitly enabled."""

        if not self.config.get("poll_enabled"):
            return None
        return super()._next_sync_at(now=now)

    def sync(self) -> int:
        """Reconcile the external source into this mount's storage drive, then close the backend's readers."""

        drive = self.drive
        if drive is None:
            raise ValidationError({"drive": "The mount requires a storage drive."})
        with contextlib.closing(self.backend) as backend:
            return self._reconcile(backend, drive)

    def _reconcile(self, backend: MountBackend, drive: Any) -> int:
        """Mirror the source tree and files into ``drive`` and return the changed-file count."""

        backend.check_source()
        file_model = apps.get_model("storage", "File")
        folder_model = apps.get_model("storage", "Folder")
        previous = self._freshness_map(file_model)
        previous_present = {key for key, state in previous.items() if not state.is_trashed}
        seen_keys: set[str] = set()
        folder_cache: dict[tuple[str, ...], Any | None] = {(): None}
        mime_cache: dict[str, Any | None] = {}
        counts = {
            "changed": 0,
            "unchanged": 0,
            "duplicates": 0,
            "errors": 0,
            "skipped": 0,
            "trashed": 0,
            "vanished": 0,
            "scanned": 0,
        }

        self._mirror_directories(backend, drive=drive, folder_model=folder_model, cache=folder_cache, counts=counts)

        for entry in backend.iter_entries():
            counts["scanned"] += 1
            seen_keys.add(entry.storage_key)
            prior = previous.get(entry.storage_key)
            if self._entry_unchanged(entry, prior):
                counts["unchanged"] += 1
                self._report_batch(counts)
                continue

            digest = ""
            try:
                digest = backend.entry_hash(entry)
                folder = self._entry_folder(
                    entry,
                    drive=drive,
                    folder_model=folder_model,
                    cache=folder_cache,
                )
                if self.mode == MountMode.REFERENCE:
                    file_model.objects.index_external(
                        drive=drive,
                        storage_path=entry.storage_key,
                        filename=PurePosixPath(entry.path).name,
                        content_hash=digest,
                        size_bytes=entry.size_bytes,
                        mime_type=backend.entry_mime(entry),
                        folder=folder,
                        metadata={"mount": self._mount_metadata(entry)},
                        mime_cache=mime_cache,
                        existing_pk=prior.pk if prior is not None else None,
                        owner_id=self.owner_id,
                    )
                elif prior is not None and not prior.is_trashed and prior.content_hash == digest:
                    # The copied bytes are current; the source only moved, renamed or touched.
                    self._place_copy(file_model, prior.pk, entry=entry, folder=folder)
                else:
                    with contextlib.closing(backend.open_entry(entry)) as reader:
                        file_model.objects.ingest_stream(
                            reader,
                            filename=PurePosixPath(entry.path).name,
                            content_hash=digest,
                            size_bytes=entry.size_bytes,
                            metadata={"mount": self._mount_metadata(entry)},
                            owner_id=self.owner_id,
                            drive_id=str(drive.public_id),
                            folder_id=str(folder.public_id) if folder is not None else "",
                        )
                counts["changed"] += 1
            except exceptions.ExternalDuplicate:
                counts["duplicates"] += 1
                self._report_batch(counts)
                continue
            # Each manager write owns its atomic block; with no transaction
            # around sync, a contained DataError is already rolled back.
            except DataError, exceptions.UploadError, OSError, ValidationError:
                counts["errors"] += 1
                self._report_batch(counts)
                continue

            self._report_batch(counts)

        seen_keys.update(backend.observed_keys)
        counts["scanned"] += backend.scan_errors
        counts["errors"] += backend.scan_errors
        counts["skipped"] = backend.skipped_entries
        counts["vanished"] = len(previous_present.difference(seen_keys))
        if self.mode == MountMode.REFERENCE:
            counts["trashed"] = file_model.objects.trash_missing_external(
                drive,
                seen_keys,
            )
            folder_model.objects.prune_missing(
                drive,
                [parts for parts in folder_cache if parts],
            )
        self._report_progress(counts, complete=True)
        return counts["changed"]

    def _mount_metadata(self, entry: MountEntry) -> dict[str, Any]:
        """Return the mount facts a row records for ``entry``: its mtime, source path and storage key.

        A reference row's storage path is its key, and a copy row always records
        its source path. A source keyed by stable ids can move or rename a file
        without touching its size or mtime, so a row of such a source also
        records what its storage path does not: the path, or the key. A
        path-keyed source records nothing more than it always did.
        """

        keyed_by_path = entry.storage_key == entry.path
        if self.mode == MountMode.REFERENCE:
            placement = {} if keyed_by_path else {"source_path": entry.path}
            return {"mtime_ns": entry.mtime_ns, **placement}
        return {
            "source_path": entry.path,
            "mtime_ns": entry.mtime_ns,
            **({} if keyed_by_path else {"storage_key": entry.storage_key}),
        }

    def _place_copy(self, file_model: Any, pk: Any, *, entry: MountEntry, folder: Any | None) -> None:
        """Move and rename a copied row to its source's new place and record the source's facts.

        The bytes are unchanged, so nothing is copied; the row keeps its identity
        under its storage key. Placement follows ``File.validate_placement``.
        """

        filename = PurePosixPath(entry.path).name
        with system_context(reason="storage_integrate.mount.place_copy"):
            row = file_model.objects.get(pk=pk)
            moved = [
                field
                for field, value in (("folder_id", folder.pk if folder is not None else None), ("filename", filename))
                if getattr(row, field) != value
            ]
            if moved:
                if folder is not None:
                    file_model.validate_placement(folder, row.drive_id)
                row.folder_id = folder.pk if folder is not None else None
                row.filename = filename
                row.save(update_fields=[field.removesuffix("_id") for field in moved] + ["updated_at"])
            row.merge_metadata({"mount": self._mount_metadata(entry)})

    def _freshness_map(self, file_model: Any) -> dict[str, _MountFileState]:
        """Load this drive's File-owned mount freshness in one streamed query, by storage key.

        A reference row's key is its storage path; a copy row's is the key it
        recorded, or its source path, which a path-keyed source's key is.
        """

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
            for pk, storage_path, content_hash, size_bytes, is_trashed, metadata in rows.iterator(chunk_size=2000):
                mount_metadata = metadata.get("mount") if isinstance(metadata, Mapping) else None
                mount_values = mount_metadata if isinstance(mount_metadata, Mapping) else {}
                recorded_path = str(mount_values.get("source_path") or "")
                if self.mode == MountMode.REFERENCE:
                    storage_key = str(storage_path)
                    source_path = recorded_path or storage_key
                else:
                    storage_key = str(mount_values.get("storage_key") or recorded_path)
                    source_path = recorded_path
                if not storage_key:
                    continue
                raw_mtime_ns = mount_values.get("mtime_ns")
                try:
                    mtime_ns = int(raw_mtime_ns) if raw_mtime_ns is not None else None
                except TypeError, ValueError:
                    mtime_ns = None
                freshness[storage_key] = _MountFileState(
                    pk=pk,
                    content_hash=str(content_hash),
                    size_bytes=int(size_bytes),
                    is_trashed=bool(is_trashed),
                    mtime_ns=mtime_ns,
                    path=source_path,
                )
        return freshness

    def _entry_unchanged(self, entry: MountEntry, previous: _MountFileState | None) -> bool:
        """Return whether File-owned source facts prove ``entry`` is unchanged, placement included."""

        return (
            previous is not None
            and not previous.is_trashed
            and previous.size_bytes == entry.size_bytes
            and previous.mtime_ns == entry.mtime_ns
            and previous.path == entry.path
            and bool(previous.content_hash)
        )

    def _mirror_directories(
        self,
        backend: MountBackend,
        *,
        drive: Any,
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
                    drive,
                    PurePosixPath(directory).parts,
                    cache=cache,
                )
            # Each ensure_path write owns its atomic block; with no transaction
            # around sync, a contained DataError is already rolled back.
            except DataError, exceptions.UploadError, OSError, ValidationError:
                counts["errors"] += 1
                continue

    def _entry_folder(
        self, entry: MountEntry, *, drive: Any, folder_model: Any, cache: dict[tuple[str, ...], Any | None]
    ) -> Any | None:
        """Return the cached storage folder mirroring ``entry``'s parent path."""

        parts = tuple(PurePosixPath(entry.path).parts[:-1])
        if parts not in cache:
            cache[parts] = folder_model.objects.ensure_path(drive, parts, cache=cache)
        return cache[parts]

    def _report_batch(self, counts: dict[str, int]) -> None:
        """Publish progress after each complete batch."""

        if counts["scanned"] % _PROGRESS_BATCH_SIZE == 0:
            self._report_progress(counts)

    def _report_progress(self, counts: dict[str, int], *, complete: bool = False) -> None:
        """Publish mount counters through the generic bridge change stream."""

        bridge = current_progress_bridge()
        if bridge is None:
            return
        bridge.report_progress(
            "syncing",
            message="Mount sync completed" if complete else "Mount sync in progress",
            details={"backend": self.backend_class, "mode": str(self.mode), **counts},
        )
