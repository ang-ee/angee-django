"""Facebook takeout import from an extracted local-folder storage drive."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from django.apps import apps
from rebac import system_context

from angee.messaging_integrate_facebook.archive import ACTIVITY_DIRECTORY, MARKER_FILENAMES
from angee.messaging_integrate_facebook.connect import confirmed_facebook_channel
from angee.messaging_integrate_facebook.importer import import_archive
from angee.workflows_integrate.archives import ArchiveError
from angee.workflows_integrate.steps import ArchiveExecutionReporter, ArchiveExtractor


class FacebookMountTakeoutExtractor(ArchiveExtractor):
    """Recognize an extracted Facebook export drive and import its real path."""

    key = "facebook_takeout_mount"
    label = "Facebook data export (mounted)"
    subject_resource = "storage.Drive"
    target_resource = "messaging.Channel"

    def recognizes(self, subject: Any) -> bool:
        """Return whether a mounted drive exposes a Facebook marker on disk."""

        return _export_root(subject) is not None

    def execute(
        self,
        subject: Any,
        target_pk: str,
        reporter: ArchiveExecutionReporter,
    ) -> dict[str, Any]:
        """Delegate the mounted export to the same import facade."""

        channel = confirmed_facebook_channel(target_pk)
        root = _export_root(subject)
        if root is None:
            raise ArchiveError("This drive does not expose a Facebook export on disk.")
        result = import_archive(
            root,
            channel,
            resume=True,
            on_batch=lambda _total: reporter.heartbeat(),
        )
        reporter.heartbeat()
        return {"channel": str(channel.sqid), **result}


def _export_root(drive: Any) -> Path | None:
    """Return the real local-folder mount root containing the export trees."""

    file_model = apps.get_model("storage", "File")
    with system_context(reason="messaging_integrate_facebook.mount.marker"):
        candidates = file_model._base_manager.filter(
            drive=drive,
            filename__in=MARKER_FILENAMES,
            is_trashed=False,
        ).order_by("storage_path")
        for file_row in candidates:
            relative = PurePosixPath(str(file_row.storage_path))
            if ACTIVITY_DIRECTORY not in relative.parts:
                continue
            local = file_row.local_path()
            if local is None or not local.is_file():
                continue
            root = local
            for _part in relative.parts:
                root = root.parent
            try:
                if root.joinpath(*relative.parts).resolve() == local.resolve():
                    return root
            except OSError:
                continue
    return None
