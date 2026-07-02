"""GraphQL schema contributions for Angee storage."""

from __future__ import annotations

from typing import Any, cast
from urllib.parse import urlencode

import strawberry
import strawberry_django
from django.apps import apps
from django.urls import reverse
from rebac import ObjectRef, system_context
from strawberry import auto
from strawberry.permission import BasePermission
from strawberry.scalars import JSON
from strawberry_django.pagination import OffsetPaginated

from angee.graphql.data import AngeeHasuraWriteBackend, hasura_model_resource, public_pk_decoder
from angee.graphql.deletion import DeletePreview, attach_delete_preview_metadata, delete_by_public_id
from angee.graphql.ids import (
    PublicID,
    instance_for_id,
    require_public_id,
    to_public_id,
)
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
from angee.graphql.writes import write_queryset
from angee.iam.audit import AuthoredRefMixin
from angee.iam.permissions import RolePermission
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

        return require_public_id(Backend, cast(Any, self).backend_id)


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

        return to_public_id(Drive, cast(Any, self).drive_id)

    @strawberry_django.field(only=["parent_id"])
    def parent(self) -> strawberry.ID | None:
        """Return the parent folder's public id, if any."""

        return to_public_id(Folder, cast(Any, self).parent_id)


@strawberry_django.type(File)
class FileType(AuthoredRefMixin, AngeeNode):
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

        return require_public_id(Drive, cast(Any, self).drive_id)

    @strawberry_django.field(only=["folder_id"])
    def folder(self) -> strawberry.ID | None:
        """Return the folder's public id, if the file is in one."""

        return to_public_id(Folder, cast(Any, self).folder_id)

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


@strawberry.input
class FileUploadBeginInput:
    """Fields accepted when reserving an upload."""

    filename: str
    mime_type: str = strawberry.field(name="mime_type", default="")
    size_bytes: int = strawberry.field(name="size_bytes", default=0)
    drive: PublicID | None = None
    drive_slug: str = strawberry.field(name="drive_slug", default="")
    folder: PublicID | None = None
    content_hash: str = strawberry.field(name="content_hash", default="")


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
    upload_url: str = strawberry.field(name="upload_url", default="")
    upload_token: str = strawberry.field(name="upload_token", default="")
    error: str | None = None
    error_code: str | None = strawberry.field(name="error_code", default=None)


@strawberry.input
class FileUploadFinalizeInput:
    """Fields accepted when finalizing an upload."""

    file: PublicID
    content_hash: str = strawberry.field(name="content_hash")
    size_bytes: int = strawberry.field(name="size_bytes")


@strawberry.type
class FileUploadFinalizePayload:
    """Verified file row, or the error that rejected the upload."""

    file: FileType | None = None
    error: str | None = None
    error_code: str | None = strawberry.field(name="error_code", default=None)


class StorageAdminPermission(RolePermission):
    """Allow actors who reach the ``storage_admin`` role.

    Platform admins (``angee/role:admin``) are implicit members through the
    role's ``member`` union in ``permissions.zed``.
    """

    role_ref = _STORAGE_ADMIN_ROLE
    message = "Storage admin permission required."


_STORAGE_ADMIN_CLASSES: list[type[BasePermission]] = [StorageAdminPermission]


@strawberry.type
class StorageQuery:
    """Storage queries shared by the public and console schemas."""

    mime_types: OffsetPaginated[MimeTypeType] = strawberry_django.offset_paginated()


class FolderWriteBackend(AngeeHasuraWriteBackend):
    """Write semantics for folders: create belongs to the manager factory."""

    def create(self, info: strawberry.Info, data: dict[str, Any]) -> Any:
        """Create a real folder through ``Folder.objects.create_in_drive``."""

        del info
        try:
            return Folder.objects.create_in_drive(
                drive_id=str(data["drive"]),
                name=str(data["name"]),
                parent_id=str(data.get("parent") or ""),
                description=str(data.get("description") or ""),
            )
        except exceptions.UploadError as error:
            raise ValueError(str(error)) from error


_DRIVE_RESOURCE = hasura_model_resource(
    DriveType,
    model=Drive,
    name="drives",
    filterable=["id", "slug", "name", "is_archived", "backend"],
    sortable=["slug", "name", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["is_archived", "created_at"],
    insertable=["backend", "slug", "name", "description", "prefix"],
    updatable=["name", "description", "prefix", "is_archived"],
    field_id_decode={"backend": public_pk_decoder(Backend)},
    write_backend=AngeeHasuraWriteBackend(Drive, public_id_fields=("backend",)),
)
_FOLDER_RESOURCE = hasura_model_resource(
    FolderType,
    model=Folder,
    name="folders",
    filterable=["id", "name", "is_virtual", "smart_kind", "drive", "parent"],
    sortable=["name", "is_virtual", "smart_kind", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["drive", "drive__name", "is_virtual", "smart_kind"],
    insertable=["drive", "name", "parent", "description"],
    updatable=["name", "description", "parent"],
    field_id_decode={
        "drive": public_pk_decoder(Drive),
        "parent": public_pk_decoder(Folder),
    },
    write_backend=FolderWriteBackend(Folder, public_id_fields=("parent",)),
)
_FILE_RESOURCE = hasura_model_resource(
    FileType,
    model=File,
    name="files",
    filterable=[
        "id",
        "filename",
        "title",
        "upload_state",
        "is_trashed",
        "updated_at",
        "drive",
        "folder",
    ],
    sortable=["filename", "size_bytes", "created_at", "updated_at"],
    aggregatable=["id", "size_bytes"],
    groupable=["drive", "drive__name", "upload_state", "is_trashed", "updated_at"],
    insert=False,
    updatable=["filename", "title", "folder", "metadata"],
    field_id_decode={
        "drive": public_pk_decoder(Drive),
        "folder": public_pk_decoder(Folder),
    },
    write_backend=AngeeHasuraWriteBackend(File, public_id_fields=("folder",)),
)
_BACKEND_RESOURCE = hasura_model_resource(
    BackendType,
    model=Backend,
    name="backends",
    filterable=["id", "slug", "label", "backend_class", "is_archived"],
    sortable=["slug", "label", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["backend_class", "is_archived"],
    insertable=["slug", "label", "backend_class", "backend_config"],
    updatable=["label", "backend_class", "backend_config", "is_archived"],
)


@strawberry.type
class StorageMutation:
    """Upload protocol and folder mutations shared by both schemas."""

    @strawberry.mutation(name="file_upload_begin")
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

    @strawberry.mutation(name="file_upload_finalize")
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

    @strawberry.mutation(name="restore_file")
    def restore_file(self, id: PublicID) -> FileType | None:
        """Pull one file out of the Trash smart folder."""

        row = instance_for_id(File, id, queryset=File.objects.all())
        if row is None:
            raise ValueError("file not found")
        row.restore()
        return cast(FileType, row)

    @strawberry.mutation(name="delete_file")
    def delete_file(self, id: PublicID, confirm: bool = False) -> DeletePreview:
        """Preview or confirm moving one file to Trash."""

        return delete_by_public_id(
            File,
            str(id),
            confirm=confirm,
            queryset=write_queryset(File),
        )

    @strawberry.mutation(name="delete_folder")
    def delete_folder(self, id: PublicID, confirm: bool = False) -> DeletePreview:
        """Preview or confirm deleting one folder."""

        return delete_by_public_id(
            Folder,
            str(id),
            confirm=confirm,
            queryset=write_queryset(Folder),
        )


attach_delete_preview_metadata(
    StorageMutation,
    model=File,
    node=FileType,
    field="delete_file",
)
attach_delete_preview_metadata(
    StorageMutation,
    model=Folder,
    node=FolderType,
    field="delete_folder",
)


@strawberry.type
class StorageConsoleMutation:
    """Admin-only storage mutations."""

    @strawberry.mutation(
        name="purge_file",
        permission_classes=_STORAGE_ADMIN_CLASSES,
    )
    def purge_file(self, id: PublicID) -> bool:
        """Permanently delete one file row and its backend object."""

        with system_context(reason="storage.graphql.purge_file"):
            row = instance_for_id(File, id, queryset=File._default_manager.all())
            if row is None:
                raise ValueError("file not found")
            row.purge()
        return True


_SHARED_TYPES = [
    MimeTypeType,
    DriveType,
    FolderType,
    FileType,
    FileUploadBeginPayload,
    FileUploadFinalizePayload,
    *_DRIVE_RESOURCE.types,
    *_FOLDER_RESOURCE.types,
    *_FILE_RESOURCE.types,
]

schemas = {
    "public": {
        "query": [
            StorageQuery,
            _DRIVE_RESOURCE.query,
            _FOLDER_RESOURCE.query,
            _FILE_RESOURCE.query,
        ],
        "mutation": [
            StorageMutation,
            _FILE_RESOURCE.mutation,
            _FOLDER_RESOURCE.mutation,
        ],
        "types": [*_SHARED_TYPES],
    },
    "console": {
        "query": [
            StorageQuery,
            _DRIVE_RESOURCE.query,
            _FOLDER_RESOURCE.query,
            _FILE_RESOURCE.query,
            _BACKEND_RESOURCE.query,
        ],
        "mutation": [
            StorageMutation,
            StorageConsoleMutation,
            _FILE_RESOURCE.mutation,
            _FOLDER_RESOURCE.mutation,
            _DRIVE_RESOURCE.mutation,
            _BACKEND_RESOURCE.mutation,
        ],
        "subscription": [changes(File, field="fileChanged")],
        "types": [*_SHARED_TYPES, BackendType, *_BACKEND_RESOURCE.types],
    },
}
