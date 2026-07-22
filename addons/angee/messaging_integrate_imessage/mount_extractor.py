"""iMessage iPhone-backup extraction from a mounted storage drive.

A local-folder mount indexes an unencrypted iPhone backup as ``storage.File`` rows
under a dedicated ``storage.Drive``. In reference mode the drive's bytes are the
folder on disk, so the backup resolves to a real directory and the directory
importer (:func:`~.importer.import_backup`) runs unchanged. Unlike WhatsApp there
is one store (SMS + iMessage together), so one extractor maps the backup onto its
confirmed channel.

Store and blob resolution stay owned by :mod:`.store`; this module only locates the
backup root inside the drive.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.apps import apps
from rebac import system_context

from angee.integrate_iphone.backup import BackupError, IosBackup
from angee.messaging_integrate_imessage.backend import confirmed_imessage_channel
from angee.messaging_integrate_imessage.importer import import_backup
from angee.messaging_integrate_imessage.store import has_sms_store
from angee.workflows_integrate.steps import ArchiveExecutionReporter, ArchiveExtractor

_MANIFEST_NAME = "Manifest.db"


class ImessageMountBackupExtractor(ArchiveExtractor):
    """Recognize an iMessage iPhone backup inside a mounted drive and import it.

    The subject is a ``storage.Drive`` produced by a local-folder mount;
    recognition succeeds when the backup's ``sms.db`` store resolves on disk, and
    import reuses the directory reader against the drive's backup root.
    """

    key = "imessage_iphone_mount"
    label = "iMessage iPhone backup (mounted)"
    subject_resource = "storage.Drive"
    target_resource = "messaging.Channel"

    def recognizes(self, subject: Any) -> bool:
        """Return whether the Messages store resolves in the drive's backup."""

        root = _backup_root(subject)
        if root is None:
            return False
        try:
            iphone = IosBackup(root)
        except BackupError:
            return False
        try:
            return has_sms_store(iphone)
        except BackupError:
            return False
        finally:
            iphone.close()

    def execute(
        self,
        subject: Any,
        target_pk: str,
        reporter: ArchiveExecutionReporter,
    ) -> dict[str, Any]:
        """Import the drive's backup Messages store into the confirmed channel."""

        channel = confirmed_imessage_channel(target_pk)
        root = _backup_root(subject)
        if root is None:
            raise BackupError("This drive does not expose an iPhone backup on disk.")
        total = import_backup(channel, root, resume=True, on_batch=lambda _total: reporter.heartbeat())
        return {"channel": str(channel.sqid), "imported": total}


# FOLLOW-UP: lift the drive→backup-root resolver below into a shared owner (the
# natural home is `angee.messaging`, which both messaging backup addons depend on,
# or `angee.integrate_iphone` once it may know `storage`) and compose it here and in
# `angee.messaging_integrate_whatsapp.mount_extractor` — these three helpers are
# byte-for-byte platform-neutral. Kept mirrored for now to leave WhatsApp untouched.
def _backup_root(drive: Any) -> Path | None:
    """Return the on-disk iPhone-backup root inside ``drive``, or ``None``.

    The backup root is the directory holding ``Manifest.db``. Only reference
    mounts expose real bytes on disk (``File.storage.path``); a managed/copy or
    non-filesystem drive returns ``None`` — sqlite cannot open its blobs in place.
    """

    manifest = _manifest_file(drive)
    if manifest is None:
        return None
    local = _file_local_path(manifest)
    if local is None or not local.exists():
        return None
    return local.parent


def _manifest_file(drive: Any) -> Any:
    """Return the drive's ``Manifest.db`` File row (deterministic on multiples)."""

    file_model = apps.get_model("storage", "File")
    with system_context(reason="messaging_integrate_imessage.mount.manifest"):
        return (
            file_model._base_manager.filter(drive=drive, filename=_MANIFEST_NAME, is_trashed=False)
            .order_by("storage_path")
            .first()
        )


def _file_local_path(file_row: Any) -> Path | None:
    """Return one File row's real on-disk path when its backend serves one."""

    path_fn = getattr(file_row.storage, "path", None)
    if path_fn is None:
        return None
    try:
        return Path(path_fn(file_row.storage_path))
    except (NotImplementedError, ValueError):
        return None
