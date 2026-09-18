"""Backfill legacy ``attachment.bin`` display names from each file's MIME type.

Before the ingest fallback derived a name from the payload's MIME, an unnamed
attachment landed under the hardcoded literal ``attachment.bin`` regardless of
its real type. Storage already sniffs and stores the true MIME, so this one-time
data fix converges the display ``filename`` onto the same ``attachment{ext}``
rule the ingest fallback now applies. Downstream ``makemigrations`` cannot
represent a data fix, hence the addon-owned runtime migration.
"""

from __future__ import annotations

import mimetypes

from django.db import migrations
from django.db.migrations.state import ProjectState

_EXTENSION_FIXUPS = {".jpe": ".jpg", ".jpeg": ".jpg"}


def _fallback_attachment_name(mime: str) -> str:
    """Return ``attachment{ext}`` for a MIME type, defaulting to ``attachment.bin``.

    A frozen copy of ``angee.storage.uploads.fallback_attachment_name`` as it
    stood when this fix shipped: the loader copies this module verbatim into a
    project's migration history, and released history must not import live code
    that may later move or change its rule. The live helper owns the rule going
    forward; this copy owns only what the one-time backfill wrote.
    """

    primary = (mime or "").split(";", 1)[0].strip().lower()
    extension = mimetypes.guess_extension(primary) if primary else None
    if extension is None:
        extension = ".bin"
    return f"attachment{_EXTENSION_FIXUPS.get(extension, extension)}"


def applies(project_state: ProjectState) -> bool:
    """Return whether storage carries the File name/MIME shape this fix reads.

    A data-only fix has no new-state schema marker: it is applicable whenever the
    target shapes exist, and its body is idempotent (each pass renames only files
    still literally named ``attachment.bin``), so materialising it on a fresh
    project is a no-op.
    """

    file = project_state.models.get(("storage", "file"))
    if file is None:
        return False
    return "filename" in file.fields and "mime_type" in file.fields


def rename_attachment_bin(apps, schema_editor) -> None:
    """Rename every ``attachment.bin`` File to ``attachment{ext}`` from its MIME.

    Historical models through ``_base_manager`` — a migration is a system
    operation. Only the display ``filename`` column moves; ``storage_path`` (the
    content-addressed key downloads resolve through) and the stored blob stay
    untouched. Batched by distinct MIME so one UPDATE renames every file of a
    kind, and idempotent: a renamed row no longer matches, and a row whose MIME
    maps back to ``.bin`` is skipped so it never re-writes.
    """

    file_model = apps.get_model("storage", "File")
    mime_model = apps.get_model("storage", "MimeType")
    database = schema_editor.connection.alias
    # order_by() clears the model default ordering: it names the live model's
    # ``sqid`` alias, which the historical model cannot resolve.
    stale = file_model._base_manager.using(database).filter(filename="attachment.bin").order_by()
    mime_ids = list(stale.values_list("mime_type_id", flat=True).distinct())
    for mime_id in mime_ids:
        mime = ""
        if mime_id is not None:
            mime = (
                mime_model._base_manager.using(database).filter(pk=mime_id).values_list("mime_type", flat=True).first()
                or ""
            )
        new_name = _fallback_attachment_name(mime)
        if new_name == "attachment.bin":
            continue
        stale.filter(mime_type_id=mime_id).update(filename=new_name)


class Migration(migrations.Migration):
    """Data fix: legacy ``attachment.bin`` files carry a MIME-derived name."""

    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.RunPython(rename_attachment_bin, migrations.RunPython.noop),
    ]
