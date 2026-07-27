"""Facebook takeout ZIP client for the archive workflow bridge."""

from __future__ import annotations

import zipfile
from pathlib import PurePosixPath
from typing import Any, BinaryIO, cast

from angee.messaging_integrate_facebook.archive import ACTIVITY_DIRECTORY, MARKER_RELATIVE_PATHS
from angee.messaging_integrate_facebook.connect import confirmed_facebook_channel
from angee.messaging_integrate_facebook.importer import import_archive
from angee.workflows_integrate.archives import (
    ArchiveError,
    BoundedReader,
    archive_entries,
    stage_subtree,
)
from angee.workflows_integrate.steps import ArchiveExecutionReporter, ArchiveExtractor

_RECOGNITION_READ_LIMIT = 16 * 1024 * 1024


class FacebookTakeoutExtractor(ArchiveExtractor):
    """Recognize and import a Facebook Download Your Information ZIP."""

    key = "facebook_takeout"
    label = "Facebook data export"
    target_resource = "messaging.Channel"

    def recognizes(self, file: Any) -> bool:
        """Return whether bounded ZIP metadata contains a Facebook marker path."""

        try:
            with file.open_stream() as stream:
                bounded = BoundedReader(stream, limit=_RECOGNITION_READ_LIMIT)
                with zipfile.ZipFile(cast(BinaryIO, bounded)) as archive:
                    return _has_marker(archive)
        except (
            ArchiveError,
            NotImplementedError,
            OSError,
            ValueError,
            zipfile.BadZipFile,
            zipfile.LargeZipFile,
        ):
            return False

    def execute(
        self,
        file: Any,
        target_pk: str,
        reporter: ArchiveExecutionReporter,
    ) -> dict[str, Any]:
        """Stage the ZIP and delegate all landing to the one import facade."""

        channel = confirmed_facebook_channel(target_pk)
        try:
            with file.open_stream() as stream, zipfile.ZipFile(stream) as archive:
                if not _has_marker(archive):
                    raise ArchiveError("This archive contains no Facebook data export.")
                with stage_subtree(archive, PurePosixPath(".")) as root:
                    result = import_archive(
                        root,
                        channel,
                        resume=True,
                        on_batch=lambda _total: reporter.heartbeat(),
                    )
        except (zipfile.BadZipFile, zipfile.LargeZipFile) as error:
            raise ArchiveError("This file is not a readable ZIP export archive.") from error
        reporter.heartbeat()
        return {"channel": str(channel.sqid), **result}


def _has_marker(archive: zipfile.ZipFile) -> bool:
    """Return whether safe member names prove a Facebook activity tree."""

    for name in archive_entries(archive):
        path = PurePosixPath(name)
        if ACTIVITY_DIRECTORY not in path.parts:
            continue
        suffix = "/".join(path.parts[path.parts.index(ACTIVITY_DIRECTORY) + 1 :])
        if suffix in MARKER_RELATIVE_PATHS or suffix.startswith("messages/"):
            return True
    return False
