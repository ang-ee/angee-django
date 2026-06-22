"""GraphQL schema contributions for Angee storage."""

from __future__ import annotations

from typing import Any, cast
from urllib.parse import urlencode

import strawberry
import strawberry_django
from django.apps import apps
from django.urls import reverse
from rebac import ObjectRef, current_actor, system_context
from rebac.backends import backend as rebac_backend
from strawberry import UNSET, auto
from strawberry.permission import BasePermission
from strawberry.scalars import JSON
from strawberry_django.pagination import OffsetPaginated

from angee.base.models import public_id_for
from angee.graphql.crud import crud
from angee.graphql.data import data_query
from angee.graphql.ids import PublicID, instance_for_id
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
from angee.iam.identity import user_display_label, user_public_id
from angee.storage import exceptions
from angee.storage.models import UploadState

Backend = apps.get_model("storage", "Backend")
Drive = apps.get_model("storage", "Drive")
Folder = apps.get_model("storage", "Folder")
MimeType = apps.get_model("storage", "MimeType")
File = apps.get_model("storage", "File")

_STORAGE_ADMIN_ROLE = ObjectRef("storage/role", "storage_admin")
"""Role whose effective members may manage backends and drives."""


@strawberry_django.type(MimeType)
class MimeTypeType(AngeeNode):
    """GraphQL projection of one MIME taxonomy row."""

    mime_type: auto
    category: auto
    label: auto
    icon_key: auto


@strawberry_django.type(Backend)
class BackendType(AngeeNode):
    """Admin projection of a storage backend, including its config."""

    slug: auto
    label: auto
    backend_class: auto
    backend_config: JSON
    is_default: auto
    is_archived: auto
    created_at: auto
    updated_at: auto


@strawberry_django.type(Drive)
class DriveType(AngeeNode):
    """GraphQL projection of a drive."""

    slug: auto
    name: auto
    description: auto
    prefix: auto
    is_archived: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["backend_id"])
    def backend(self) -> strawberry.ID:
        """Return the parent backend's public id without exposing the row."""

        return strawberry.ID(public_id_for(Backend, cast(Any, self).backend_id))


@strawberry_django.type(Folder)
class FolderType(AngeeNode):
    """GraphQL projection of a folder or smart folder."""

    name: auto
    description: auto
    is_virtual: auto
    smart_kind: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["drive_id"])
    def drive(self) -> strawberry.ID | None:
        """Return the drive's public id; smart folders have none."""

        drive_id = cast(Any, self).drive_id
        return strawberry.ID(public_id_for(Drive, drive_id)) if drive_id else None

    @strawberry_django.field(only=["parent_id"])
    def parent(self) -> strawberry.ID | None:
        """Return the parent folder's public id, if any."""

        parent_id = cast(Any, self).parent_id
        return strawberry.ID(public_id_for(Folder, parent_id)) if parent_id else None


@strawberry_django.type(File)
class FileType(AngeeNode):
    """GraphQL projection of a file row."""

    filename: auto
    title: auto
    content_hash: auto
    size_bytes: auto
    metadata: JSON
    upload_state: auto
    is_trashed: auto
    trashed_at: auto
    created_at: auto
    updated_at: auto
    mime_type: MimeTypeType | None

    @strawberry_django.field(only=["drive_id"])
    def drive(self) -> strawberry.ID:
        """Return the drive's public id without exposing the drive object."""

        return strawberry.ID(public_id_for(Drive, cast(Any, self).drive_id))

    @strawberry_django.field(only=["folder_id"])
    def folder(self) -> strawberry.ID | None:
        """Return the folder's public id, if the file is in one."""

        folder_id = cast(Any, self).folder_id
        return strawberry.ID(public_id_for(Folder, folder_id)) if folder_id else None

    @strawberry_django.field(only=["created_by_id"])
    def created_by(self) -> strawberry.ID | None:
        """Return the uploader's public id without exposing the user object."""

        return cast("strawberry.ID | None", user_public_id(cast(Any, self).created_by_id))

    @strawberry_django.field(only=["created_by_id"])
    def created_by_label(self) -> str | None:
        """Return the uploader's display label — no user object exposed."""

        return user_display_label(cast(Any, self).created_by_id)

    @strawberry_django.field
    def url(self) -> str:
        """Return the token proxy download URL for READY rows, empty otherwise.

        Minted here in actor scope — only a reader of the row resolves this
        field — so the URL is a short-lived capability the download view honours
        without a second access check (see :meth:`File.download_url`).
        """

        row = cast(Any, self)
        if row.upload_state != UploadState.READY:
            return ""
        return str(row.download_url())


@strawberry_django.filter_type(File, lookups=True)
class FileFilter:
    """Field lookups accepted when filtering the files list."""

    filename: auto
    title: auto
    upload_state: auto
    is_trashed: auto
    updated_at: auto
    drive: auto
    folder: auto


@strawberry_django.order_type(File)
class FileOrder:
    """Orderings accepted by the files list."""

    filename: auto
    size_bytes: auto
    created_at: auto
    updated_at: auto


@strawberry_django.filter_type(Folder, lookups=True)
class FolderFilter:
    """Field lookups accepted when filtering the folders list."""

    name: auto
    is_virtual: auto
    smart_kind: auto
    drive: auto
    parent: auto


@strawberry_django.order_type(Folder)
class FolderOrder:
    """Orderings accepted by the folders list."""

    name: auto
    is_virtual: auto
    smart_kind: auto
    created_at: auto
    updated_at: auto


@strawberry_django.filter_type(Drive, lookups=True)
class DriveFilter:
    """Field lookups accepted when filtering the drives list."""

    slug: auto
    name: auto
    is_archived: auto


@strawberry_django.order_type(Drive)
class DriveOrder:
    """Orderings accepted by the drives list."""

    slug: auto
    name: auto
    created_at: auto
    updated_at: auto


@strawberry_django.filter_type(Backend, lookups=True)
class BackendFilter:
    """Field lookups accepted when filtering the backends list."""

    slug: auto
    label: auto
    backend_class: auto
    is_default: auto
    is_archived: auto


@strawberry_django.order_type(Backend)
class BackendOrder:
    """Orderings accepted by the backends list."""

    slug: auto
    label: auto
    created_at: auto
    updated_at: auto


@strawberry.input
class FileUploadBeginInput:
    """Fields accepted when reserving an upload."""

    filename: str
    mime_type: str = ""
    size_bytes: int = 0
    drive: PublicID | None = None
    drive_slug: str = ""
    folder: PublicID | None = None
    content_hash: str = ""


@strawberry.type
class FileUploadBeginPayload:
    """How the client should send bytes for one reserved file row.

    ``method`` is ``"proxy"`` (PUT the raw body to ``upload_url``),
    ``"deduped"`` (identical bytes already exist; nothing to send), or empty
    when ``error`` is set. A ``"presigned"`` arm arrives with the first
    backend that can presign uploads natively.
    """

    method: str = ""
    file: FileType | None = None
    upload_url: str = ""
    upload_token: str = ""
    error: str | None = None
    error_code: str | None = None


@strawberry.input
class FileUploadFinalizeInput:
    """Fields accepted when finalizing an upload."""

    file: PublicID
    content_hash: str
    size_bytes: int


@strawberry.type
class FileUploadFinalizePayload:
    """Verified file row, or the error that rejected the upload."""

    file: FileType | None = None
    error: str | None = None
    error_code: str | None = None


@strawberry.input
class FilePatch:
    """Fields accepted when updating a file."""

    id: PublicID
    filename: str | None = UNSET
    title: str | None = UNSET
    folder: PublicID | None = UNSET
    metadata: JSON | None = UNSET


@strawberry.input
class FolderInput:
    """Fields accepted when creating a folder."""

    drive: PublicID
    name: str
    parent: PublicID | None = None
    description: str = ""


@strawberry.input
class FolderPatch:
    """Fields accepted when updating a folder."""

    id: PublicID
    name: str | None = UNSET
    description: str | None = UNSET
    parent: PublicID | None = UNSET


@strawberry.input
class DriveInput:
    """Fields accepted when creating a drive."""

    backend: PublicID
    slug: str
    name: str
    description: str = ""
    prefix: str = ""


@strawberry.input
class DrivePatch:
    """Fields accepted when updating a drive."""

    id: PublicID
    name: str | None = UNSET
    description: str | None = UNSET
    prefix: str | None = UNSET
    is_archived: bool | None = UNSET


@strawberry.input
class BackendInput:
    """Fields accepted when registering a storage backend."""

    slug: str
    label: str
    backend_class: str
    backend_config: JSON | None = None
    is_default: bool = False


@strawberry.input
class BackendPatch:
    """Fields accepted when updating a storage backend."""

    id: PublicID
    label: str | None = UNSET
    backend_class: str | None = UNSET
    backend_config: JSON | None = UNSET
    is_default: bool | None = UNSET
    is_archived: bool | None = UNSET


class StorageAdminPermission(BasePermission):
    """Allow actors who reach the ``storage_admin`` role.

    Platform admins (``angee/role:admin``) are implicit members through the
    role's ``member`` union in ``permissions.zed``.
    """

    message = "Storage admin permission required."
    error_extensions = {"code": "PERMISSION_DENIED"}

    def has_permission(self, source: Any, info: strawberry.Info, **kwargs: Any) -> bool:
        """Return whether the current actor is an effective storage admin."""

        del source, info, kwargs
        actor = current_actor()
        if actor is None:
            return False
        result = rebac_backend().check_access(
            subject=actor,
            action="effective_member",
            resource=_STORAGE_ADMIN_ROLE,
        )
        return bool(result.allowed)


_STORAGE_ADMIN_CLASSES: list[type[BasePermission]] = [StorageAdminPermission]


@strawberry.type
class StorageQuery:
    """Storage queries shared by the public and console schemas."""

    mime_types: OffsetPaginated[MimeTypeType] = strawberry_django.offset_paginated()


DriveDataQuery, _DRIVE_DATA_TYPES = data_query(
    DriveType,
    type_name="DriveDataQuery",
    filters=DriveFilter,
    order=DriveOrder,
    list_name="drives",
    detail_name="drive",
    aggregate_name="drive_aggregate",
    group_name="drive_groups",
    aggregate_fields=["id"],
    group_by_fields=["is_archived", "created_at"],
    enable_filter_echo=True,
    aggregate_kwargs={"name_prefix": "DriveAggregate", "pagination_style": "offset"},
)
FolderDataQuery, _FOLDER_DATA_TYPES = data_query(
    FolderType,
    type_name="FolderDataQuery",
    filters=FolderFilter,
    order=FolderOrder,
    list_name="folders",
    detail_name="folder",
    aggregate_name="folder_aggregate",
    group_name="folder_groups",
    aggregate_fields=["id"],
    group_by_fields=[
        "drive",
        "drive__name",
        "is_virtual",
        "smart_kind",
    ],
    enable_filter_echo=True,
    aggregate_kwargs={"name_prefix": "FolderAggregate", "pagination_style": "offset"},
)
FileDataQuery, _FILE_DATA_TYPES = data_query(
    FileType,
    type_name="FileDataQuery",
    filters=FileFilter,
    order=FileOrder,
    list_name="files",
    detail_name="file",
    aggregate_name="file_aggregate",
    group_name="file_groups",
    aggregate_fields=["id", "size_bytes"],
    group_by_fields=[
        "drive",
        "drive__name",
        "upload_state",
        "is_trashed",
        "updated_at",
    ],
    enable_filter_echo=True,
    aggregate_kwargs={"name_prefix": "FileAggregate", "pagination_style": "offset"},
)
BackendDataQuery, _BACKEND_DATA_TYPES = data_query(
    BackendType,
    type_name="BackendDataQuery",
    filters=BackendFilter,
    order=BackendOrder,
    list_name="backends",
    detail_name="backend",
    aggregate_name="backend_aggregate",
    group_name="backend_groups",
    aggregate_fields=["id"],
    group_by_fields=["backend_class", "is_default", "is_archived"],
    enable_filter_echo=True,
    permission_classes=_STORAGE_ADMIN_CLASSES,
    aggregate_kwargs={"name_prefix": "BackendAggregate", "pagination_style": "offset"},
)


@strawberry.type
class StorageMutation:
    """Upload protocol and folder mutations shared by both schemas."""

    @strawberry.mutation
    def file_upload_begin(self, input: FileUploadBeginInput) -> FileUploadBeginPayload:
        """Reserve a draft file and tell the client where to send bytes."""

        try:
            row = File.objects.draft(
                filename=input.filename,
                mime_type=input.mime_type,
                size_bytes=input.size_bytes,
                drive_id=str(input.drive) if input.drive else "",
                drive_slug=input.drive_slug,
                folder_id=str(input.folder) if input.folder else "",
                content_hash=input.content_hash,
            )
        except exceptions.UploadError as error:
            return FileUploadBeginPayload(error=str(error), error_code=error.code)
        if row.upload_state == UploadState.READY:
            return FileUploadBeginPayload(method="deduped", file=row)
        token = row.issue_upload_token()
        upload_url = f"{reverse('storage_upload')}?{urlencode({'token': token})}"
        return FileUploadBeginPayload(method="proxy", file=row, upload_url=upload_url, upload_token=token)

    @strawberry.mutation
    def file_upload_finalize(self, input: FileUploadFinalizeInput) -> FileUploadFinalizePayload:
        """Verify uploaded bytes and return the READY row."""

        row = instance_for_id(File, input.file, queryset=File.objects.all())
        if row is None:
            return FileUploadFinalizePayload(error="file not found", error_code="not_found")
        try:
            row.finalize(expected_hash=input.content_hash, expected_size=input.size_bytes)
        except exceptions.UploadError as error:
            return FileUploadFinalizePayload(error=str(error), error_code=error.code)
        return FileUploadFinalizePayload(file=row)

    @strawberry.mutation
    def create_folder(self, data: FolderInput) -> FolderType:
        """Create a real folder; the gate and insert live on the manager."""

        try:
            folder = Folder.objects.create_in_drive(
                drive_id=str(data.drive),
                name=data.name,
                parent_id=str(data.parent) if data.parent else "",
                description=data.description,
            )
        except exceptions.UploadError as error:
            raise ValueError(str(error)) from error
        return cast(FolderType, folder)

    @strawberry.mutation
    def restore_file(self, id: PublicID) -> FileType | None:
        """Pull one file out of the Trash smart folder."""

        row = instance_for_id(File, id, queryset=File.objects.all())
        if row is None:
            raise ValueError("file not found")
        row.restore()
        return cast(FileType, row)


@strawberry.type
class StorageConsoleMutation:
    """Admin-only storage mutations."""

    @strawberry.mutation(permission_classes=_STORAGE_ADMIN_CLASSES)
    def purge_file(self, id: PublicID) -> bool:
        """Permanently delete one file row and its backend object."""

        with system_context(reason="storage.graphql.purge_file"):
            row = instance_for_id(File, id, queryset=File._default_manager.all())
            if row is None:
                raise ValueError("file not found")
            row.purge()
        return True


_FILE_MUTATION = crud(FileType, update=FilePatch, delete=True)
"""File update plus soft-delete (``delete`` trashes; ``purgeFile`` is the real delete)."""

_FOLDER_MUTATION = crud(FolderType, update=FolderPatch, delete=True)
"""Folder rename/move/delete; creation is the gated ``createFolder`` mutation."""

_DRIVE_MUTATION = crud(
    DriveType,
    create=DriveInput,
    update=DrivePatch,
    delete=True,
    permission_classes=_STORAGE_ADMIN_CLASSES,
    write_context="storage.graphql.drive",
)
"""Admin drive CRUD: storage-admin gated, written elevated (const-admin create)."""

_BACKEND_MUTATION = crud(
    BackendType,
    create=BackendInput,
    update=BackendPatch,
    delete=True,
    permission_classes=_STORAGE_ADMIN_CLASSES,
    write_context="storage.graphql.backend",
)
"""Admin backend CRUD: same elevated storage-admin shape as drives."""


_SHARED_TYPES = [
    MimeTypeType,
    DriveType,
    FolderType,
    FileType,
    FileUploadBeginPayload,
    FileUploadFinalizePayload,
    *_DRIVE_DATA_TYPES,
    *_FOLDER_DATA_TYPES,
    *_FILE_DATA_TYPES,
]

schemas = {
    "public": {
        "query": [StorageQuery, DriveDataQuery, FolderDataQuery, FileDataQuery],
        "mutation": [StorageMutation, _FILE_MUTATION, _FOLDER_MUTATION],
        "types": [*_SHARED_TYPES],
    },
    "console": {
        "query": [
            StorageQuery,
            DriveDataQuery,
            FolderDataQuery,
            FileDataQuery,
            BackendDataQuery,
        ],
        "mutation": [
            StorageMutation,
            StorageConsoleMutation,
            _FILE_MUTATION,
            _FOLDER_MUTATION,
            _DRIVE_MUTATION,
            _BACKEND_MUTATION,
        ],
        "subscription": [changes(File, field="fileChanged")],
        "types": [*_SHARED_TYPES, BackendType, *_BACKEND_DATA_TYPES],
    },
}
