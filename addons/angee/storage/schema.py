"""GraphQL schema contributions for Angee storage."""

from __future__ import annotations

from typing import Any, cast
from urllib.parse import urlencode

import strawberry
import strawberry_django
from django.apps import apps
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.urls import reverse
from graphql import GraphQLError
from rebac import ObjectRef, PermissionDenied, generic_target, system_context
from rebac.resources import model_resource_type
from strawberry import auto
from strawberry.permission import BasePermission
from strawberry.scalars import JSON
from strawberry_django_aggregates import BigInt
from strawberry_django.utils.requests import get_request

from angee.base.models.identity import instance_from_public_id
from angee.graphql.actions import ActionResult, action_guard, resolve_action_target
from angee.graphql.capabilities import permissions_field
from angee.graphql.data.hasura import AngeeHasuraWriteBackend, hasura_model_resource, public_pk_decoder
from angee.graphql.deletion import DeletePreview, attach_delete_preview_metadata, delete_by_public_id
from angee.graphql.ids import (
    PublicID,
    instance_for_id,
    require_instance_for_id,
)
from angee.graphql.node import NODE_DISPLAY_NAME_DESCRIPTION, AngeeNode
from angee.graphql.relations import RecordReferenceNode, actor_scoped_public_id, with_record_reference_access
from angee.graphql.subscriptions import changes
from angee.iam.audit import AuthoredRefMixin, TrashedRefMixin
from angee.iam.permissions import RolePermission
from angee.storage import exceptions
from angee.storage.models import File as FileModel
from angee.storage.models import FileVisibility, UploadState
from angee.tags.schema import TaggedNode, tags_input_extensions

Backend = apps.get_model("storage", "Backend")
Drive = apps.get_model("storage", "Drive")
Folder = apps.get_model("storage", "Folder")
MimeType = apps.get_model("storage", "MimeType")
File = apps.get_model("storage", "File")
FileAttachment = apps.get_model("storage", "FileAttachment")

_STORAGE_ADMIN_ROLE = ObjectRef("storage/role", "storage_admin")
"""Role whose effective members may manage backends and drives."""

strawberry.enum(cast(Any, FileVisibility))


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

    backend: strawberry.ID | None = actor_scoped_public_id("backend")
    owns_items: auto


@strawberry_django.type(Folder)
class FolderType(AngeeNode):
    """GraphQL projection of a folder or smart folder."""

    name: auto
    description: auto
    is_virtual: auto
    smart_kind: auto
    created_at: auto
    updated_at: auto

    drive: strawberry.ID | None = actor_scoped_public_id("drive")
    parent: strawberry.ID | None = actor_scoped_public_id("parent")


@strawberry_django.type(File)
class FileType(AuthoredRefMixin, TrashedRefMixin, AngeeNode):
    """GraphQL projection of a file row."""

    permissions = permissions_field(("read", "write", "share", "delete"))
    """The verbs record chrome gates on (``holdsPermission``), e.g. a contributed
    ``storage.File#actions`` child declaring ``permission: "read"``."""

    display_name: str = strawberry_django.field(
        resolver=AngeeNode.display_name,
        only=["title", "filename"],
        description=NODE_DISPLAY_NAME_DESCRIPTION,
    )
    filename: auto
    title: auto
    content_hash: auto
    size_bytes: BigInt
    metadata: JSON
    upload_state: auto
    visibility: auto
    is_trashed: auto
    created_at: auto
    updated_at: auto
    mime_type: MimeTypeType | None

    drive: strawberry.ID | None = actor_scoped_public_id("drive")
    folder: strawberry.ID | None = actor_scoped_public_id("folder")

    @strawberry_django.field
    def url(self, info: strawberry.Info) -> str | None:
        """Return a READY row's download URL, empty when unready, null in previews.

        The bearer token names the current actor. Download lookup re-checks
        that actor's read permission, so revocation stops subsequent requests
        even before the token expires (see :meth:`File.download_url`).
        """

        if getattr(get_request(info), "view_as", None) is not None:
            return None
        row = cast(Any, self)
        if row.upload_state != UploadState.READY:
            return ""
        return str(row.download_url())


@strawberry_django.type(FileAttachment)
class FileAttachmentType(RecordReferenceNode):
    """An actor-readable file edge, with its target identity redacted when unreadable."""

    label: auto
    file: FileType
    display_name: str = strawberry_django.field(
        resolver=AngeeNode.display_name,
        only=["label", "file_id"],
        description=NODE_DISPLAY_NAME_DESCRIPTION,
    )

    model_label: str | None = strawberry_django.field(
        resolver=RecordReferenceNode.reference_model,
        only=["content_type_id", "object_id"],
    )
    record_id: PublicID | None = strawberry_django.field(
        resolver=RecordReferenceNode.reference_id,
        only=["content_type_id", "object_id"],
    )


@strawberry.type
class RecordFilesType:
    """The upload capability and visible edges of one readable record."""

    can_upload: bool
    attachments: list[FileAttachmentType]


@strawberry.type
class StorageQuery:
    """Record-scoped attachment reads shared by public and console schemas."""

    @strawberry.field(name="file_attachments")
    def file_attachments(self, file: PublicID) -> list[FileAttachmentType]:
        """Read one file's edges through the shared target-access projection."""

        source = instance_for_id(File, file)
        if source is None:
            return []
        return list(with_record_reference_access(source.attachments.select_related("file")))

    @strawberry.field(name="record_files")
    def record_files(self, model_label: str, record_id: PublicID) -> RecordFilesType:
        """List file-readable edges without granting access through the target.

        Which record types carry files is the edge schema's fact, projected to
        clients as resource metadata; a record-scoped upload also needs the
        record-file arm and write on the record.
        """

        try:
            model = apps.get_model(model_label.strip())
        except LookupError, ValueError:
            return RecordFilesType(can_upload=False, attachments=[])
        if model_resource_type(model) is None:
            return RecordFilesType(can_upload=False, attachments=[])
        record = instance_from_public_id(model, str(record_id))
        if record is None:
            return RecordFilesType(can_upload=False, attachments=[])
        manager = FileAttachment._default_manager
        return RecordFilesType(
            can_upload=manager.has_record_arm(generic_target(record)) and record.has_access("write"),
            attachments=list(with_record_reference_access(manager.for_record(record))),
        )


@strawberry.input
class FileUploadRecordInput:
    """The model and public identity of the upload's attachment target."""

    model_label: str = strawberry.field(name="model_label")
    record_id: PublicID = strawberry.field(name="record_id")


@strawberry.input
class FileUploadBeginInput:
    """Fields accepted when reserving an upload."""

    filename: str
    mime_type: str = strawberry.field(name="mime_type", default="")
    size_bytes: BigInt = strawberry.field(name="size_bytes", default=BigInt(0))
    drive: PublicID | None = None
    drive_slug: str = strawberry.field(name="drive_slug", default="")
    folder: PublicID | None = None
    content_hash: str = strawberry.field(name="content_hash", default="")
    visibility: FileVisibility = cast(FileVisibility, FileVisibility.INHERITED)
    record: FileUploadRecordInput | None = None


@strawberry.type
class FileUploadBeginPayload:
    """How the client should send bytes for one reserved file row.

    ``method`` is ``"proxy"`` (PUT the raw body to ``upload_url``),
    ``"deduped"`` (identical bytes already exist; nothing to send), or empty
    when ``error`` is set. A ``"presigned"`` arm arrives with the first
    backend that can presign uploads natively.
    """

    method: str = ""
    file: FileModel | None = strawberry.field(graphql_type=FileType | None, default=None)
    upload_url: str = strawberry.field(name="upload_url", default="")
    upload_token: str = strawberry.field(name="upload_token", default="")
    error: str | None = None
    error_code: str | None = strawberry.field(name="error_code", default=None)


@strawberry.input
class FileUploadFinalizeInput:
    """Fields accepted when finalizing an upload."""

    file: PublicID
    content_hash: str = strawberry.field(name="content_hash")
    size_bytes: BigInt = strawberry.field(name="size_bytes")


@strawberry.type
class FileUploadFinalizePayload:
    """Verified file row, or the error that rejected the upload."""

    file: FileModel | None = strawberry.field(graphql_type=FileType | None, default=None)
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


class FolderWriteBackend(AngeeHasuraWriteBackend):
    """Write semantics for folders: create belongs to the manager factory."""

    def _create_row(self, info: strawberry.Info, data: dict[str, Any]) -> Any:
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


def _browsable_drives(info: strawberry.Info) -> Any:
    """Return the listed drives: every drive but the image drive."""

    del info
    return Drive.objects.browsable()


def _browsable_files(info: strawberry.Info) -> Any:
    """Return the listed files: every file but the image drive's, which its records show."""

    del info
    return File.objects.browsable()


_MIME_TYPE_RESOURCE = hasura_model_resource(
    MimeTypeType,
    model=MimeType,
    name="mime_types",
    filterable=["id", "mime_type", "category", "label", "icon_key"],
    sortable=["mime_type", "category", "label"],
    aggregatable=["id"],
    groupable=["category"],
    insert=False,
    update=False,
    delete=False,
)
_DRIVE_RESOURCE = hasura_model_resource(
    DriveType,
    model=Drive,
    name="drives",
    get_queryset=_browsable_drives,
    filterable=["id", "slug", "name", "is_archived", "backend", "owns_items"],
    sortable=["slug", "name", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["is_archived", "created_at"],
    insertable=["backend", "slug", "name", "description", "prefix", "owns_items"],
    updatable=["name", "description", "prefix", "is_archived", "owns_items"],
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
    get_queryset=_browsable_files,
    filterable=[
        "id",
        "filename",
        "title",
        "upload_state",
        "visibility",
        "is_trashed",
        "updated_at",
        "drive",
        "folder",
        "mime_type",
    ],
    sortable=["filename", "title", "size_bytes", "created_at", "updated_at"],
    aggregatable=["id", "size_bytes"],
    groupable=[
        "folder",
        "folder__name",
        "drive",
        "drive__name",
        "upload_state",
        "is_trashed",
        "updated_at",
        "mime_type",
        "mime_type__mime_type",
    ],
    insert=False,
    updatable=["filename", "title", "folder", "metadata"],
    # A file reads as its title or filename; that computed label is not filterable,
    # so lists and file pickers search the two columns behind it. The filename
    # leads: every file has one, while a title is optional.
    record_representation="display_name",
    record_search_fields=("filename", "title"),
    field_id_decode={
        "drive": public_pk_decoder(Drive),
        "folder": public_pk_decoder(Folder),
        "mime_type": public_pk_decoder(MimeType),
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
            record = None
            if input.record is not None:
                try:
                    model = apps.get_model(input.record.model_label.strip())
                except (LookupError, ValueError) as error:
                    raise exceptions.UploadRecordDenied() from error
                if model_resource_type(model) is None:
                    raise exceptions.UploadRecordDenied()
                try:
                    record = resolve_action_target(
                        model,
                        input.record.record_id,
                        reason="storage.upload.record_target",
                    )
                except GraphQLError as error:
                    raise exceptions.UploadRecordDenied() from error
            row = File.objects.draft(
                filename=input.filename,
                mime_type=input.mime_type,
                size_bytes=input.size_bytes,
                drive_id=str(input.drive) if input.drive else "",
                drive_slug=input.drive_slug,
                folder_id=str(input.folder) if input.folder else "",
                content_hash=input.content_hash,
                visibility=input.visibility,
                record=record,
            )
        except exceptions.UploadError as error:
            return FileUploadBeginPayload(error=str(error), error_code=error.code)
        if row.upload_state == UploadState.READY:
            return FileUploadBeginPayload(method="deduped", file=row)
        token = row.issue_upload_token()
        upload_url = f"{reverse('storage_upload')}?{urlencode({'token': token})}"
        return FileUploadBeginPayload(method="proxy", file=row, upload_url=upload_url, upload_token=token)

    @strawberry.mutation(name="set_file_visibility")
    @action_guard("Could not change file visibility.")
    def set_file_visibility(self, id: PublicID, visibility: FileVisibility) -> ActionResult:
        """Dispatch to the visibility owner with one missing-or-denied response."""

        try:
            row = resolve_action_target(File, id, reason="storage.file.visibility_target")
            row.set_visibility(visibility)
        except (GraphQLError, ObjectDoesNotExist, PermissionDenied) as error:
            raise ValidationError("File is unavailable or access is denied.") from error
        return ActionResult(ok=True, message="File visibility updated.")

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

    @strawberry.mutation(name="delete_file")
    def delete_file(self, id: PublicID, confirm: bool = False) -> DeletePreview:
        """Preview or confirm moving one file to Trash."""

        return delete_by_public_id(
            File,
            str(id),
            confirm=confirm,
            queryset=File.objects.for_write(),
        )

    @strawberry.mutation(name="delete_folder")
    def delete_folder(self, id: PublicID, confirm: bool = False) -> DeletePreview:
        """Preview or confirm deleting one folder."""

        return delete_by_public_id(
            Folder,
            str(id),
            confirm=confirm,
            queryset=Folder.objects.for_write(),
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
            row = require_instance_for_id(
                File,
                id,
                queryset=File._default_manager.all(),
                not_found="file not found",
            )
            row.purge()
        return True


@strawberry_django.type(File, name="FileType", extend=True)
class FileTags(TaggedNode):
    """Console-only tags on a file; the public file node stays unchanged."""


_SHARED_TYPES = [
    MimeTypeType,
    DriveType,
    FolderType,
    FileType,
    FileAttachmentType,
    RecordFilesType,
    FileUploadBeginPayload,
    FileUploadFinalizePayload,
    *_MIME_TYPE_RESOURCE.types,
    *_DRIVE_RESOURCE.types,
    *_FOLDER_RESOURCE.types,
    *_FILE_RESOURCE.types,
]

schemas = {
    "public": {
        "query": [
            StorageQuery,
            _MIME_TYPE_RESOURCE.query,
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
            _MIME_TYPE_RESOURCE.query,
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
        "subscription": [
            changes(File, field="fileChanged"),
            changes(Folder, field="folderChanged"),
        ],
        "types": [*_SHARED_TYPES, BackendType, *_BACKEND_RESOURCE.types],
        "type_extensions": [FileTags],
        "input_extensions": tags_input_extensions(_FILE_RESOURCE),
    },
}
