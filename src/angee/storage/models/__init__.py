"""Storage model declarations and required historical public imports."""

from angee.iam.models.roles import role_anchor
from angee.storage.choices import (
    FileVisibility,
    UploadState,
)
from angee.storage.managers import (
    DriveManager,
    FileAttachmentManager,
    FileManager,
    FolderManager,
    StorageMasterManager,
)
from angee.storage.models.attachment import (
    FileAttachment,
)
from angee.storage.models.backend import (
    Backend,
    Drive,
)
from angee.storage.models.file import (
    File,
)
from angee.storage.models.folder import (
    Folder,
)
from angee.storage.models.mimetype import (
    MimeType,
)
from angee.storage.querysets import (
    DriveQuerySet,
    FileAttachmentQuerySet,
    FileQuerySet,
    FolderQuerySet,
    StorageMasterQuerySet,
)

StorageRole = role_anchor("storage/role")


__all__ = [
    "Backend",
    "Drive",
    "DriveManager",
    "DriveQuerySet",
    "File",
    "FileAttachment",
    "FileAttachmentManager",
    "FileAttachmentQuerySet",
    "FileManager",
    "FileQuerySet",
    "FileVisibility",
    "Folder",
    "FolderManager",
    "FolderQuerySet",
    "MimeType",
    "StorageMasterManager",
    "StorageMasterQuerySet",
    "StorageRole",
    "UploadState",
]
