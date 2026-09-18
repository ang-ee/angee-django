"""Backfill display names for attachments ingested before the parts owned their name.

Before the ingest owner derived a name from the message's structural facts, a byte
part that arrived without a filename landed with an empty ``Part.name`` and its
content-addressed ``storage.File`` took the opaque ``attachment{ext}`` fallback — one
File backing parts from many messages. The ingest owner now derives a stable per-part
name (a chat message's id, an email part's Content-ID, else the shared fallback); this
one-time data fix converges the rows written before it. Downstream ``makemigrations``
cannot represent a data fix, hence the addon-owned runtime migration.

The naming rule is frozen here on purpose: the loader copies this module verbatim into
a project's migration history, and released history must not import live code that may
later move or change its rule. ``angee.messaging.managers.derived_part_name`` (and the
storage ``attachment{ext}`` fallback it composes) own the rule going forward; this copy
owns only what the one-time backfill wrote.
"""

from __future__ import annotations

import mimetypes
import re

from django.db import migrations
from django.db.migrations.state import ProjectState

_EXTENSION_FIXUPS = {".jpe": ".jpg", ".jpeg": ".jpg"}
_CID_SLUG_DISALLOWED_RE = re.compile(r"[^A-Za-z0-9._-]")
_CID_SLUG_MAX = 80
_CHAT_KIND = "chat"


def _attachment_extension(mime: str) -> str:
    """Frozen copy of ``angee.storage.uploads.attachment_extension``."""

    primary = (mime or "").split(";", 1)[0].strip().lower()
    extension = mimetypes.guess_extension(primary) if primary else None
    if extension is None:
        return ".bin"
    return _EXTENSION_FIXUPS.get(extension, extension)


def _fallback_attachment_name(mime: str) -> str:
    """Frozen copy of ``angee.storage.uploads.fallback_attachment_name``."""

    return f"attachment{_attachment_extension(mime)}"


def _cid_slug(cid: str) -> str:
    """Frozen copy of ``angee.messaging.managers._cid_slug``."""

    core = (cid or "").strip().strip("<>")
    return _CID_SLUG_DISALLOWED_RE.sub("", core)[:_CID_SLUG_MAX]


def _derived_part_name(*, mime: str, cid: str, external_id: str, is_chat: bool, index: int = 0) -> str:
    """Frozen copy of ``angee.messaging.managers.derived_part_name`` as it shipped."""

    extension = _attachment_extension(mime)
    if is_chat:
        chat_id = (external_id or "").rsplit("/", 1)[-1]
        if chat_id:
            suffix = f"-{index}" if index else ""
            return f"{chat_id}{suffix}{extension}"
    else:
        slug = _cid_slug(cid)
        if slug:
            return f"inline-{slug}{extension}"
    return _fallback_attachment_name(mime)


def applies(project_state: ProjectState) -> bool:
    """Return whether messaging + storage carry the Part/Message/File shape this fix reads.

    A data-only fix has no new-state schema marker: it is applicable whenever the
    target shapes exist, and its body is idempotent (pass 1 renames only nameless
    parts, pass 2 only files still under the ``attachment{ext}`` fallback), so
    materialising it on a fresh project is a no-op.
    """

    part = project_state.models.get(("messaging", "part"))
    message = project_state.models.get(("messaging", "message"))
    file = project_state.models.get(("storage", "file"))
    if part is None or message is None or file is None:
        return False
    part_fields = part.fields
    message_fields = message.fields
    return (
        "name" in part_fields
        and "cid" in part_fields
        and "type" in part_fields
        and "file" in part_fields
        and "message" in part_fields
        and "external_id" in message_fields
        and "message_type" in message_fields
        and "filename" in file.fields
    )


def name_unnamed_attachment_parts(apps, schema_editor) -> None:
    """Name every nameless byte part, then converge each fallback-named File onto its earliest part.

    Historical models through ``_base_manager`` — a migration is a system operation.
    Only the display ``Part.name`` and ``File.filename`` columns move; ``storage_path``
    (the content-addressed key downloads resolve through) and the stored blobs stay
    untouched. ``order_by()`` in pass 2 clears the model default ordering (the live
    ``sqid`` alias a historical model cannot resolve); pass 1 orders by concrete
    columns only.
    """

    part_model = apps.get_model("messaging", "Part")
    file_model = apps.get_model("storage", "File")
    database = schema_editor.connection.alias

    # Pass 1: every byte part that arrived nameless takes its derived name. Ordered by
    # (message, position, pk) so the per-message running index the ingest owner assigns
    # to a message's second-and-later nameless parts is reproduced exactly.
    nameless = (
        part_model._base_manager.using(database)
        .filter(name="", file__isnull=False)
        .order_by("message_id", "position", "pk")
        .values("pk", "message_id", "type", "cid", "message__external_id", "message__message_type")
    )
    current_message: object = object()
    index = 0
    for row in nameless.iterator(chunk_size=2000):
        if row["message_id"] != current_message:
            current_message = row["message_id"]
            index = 0
        name = _derived_part_name(
            mime=row["type"] or "",
            cid=row["cid"] or "",
            external_id=row["message__external_id"] or "",
            is_chat=row["message__message_type"] == _CHAT_KIND,
            index=index,
        )
        index += 1
        part_model._base_manager.using(database).filter(pk=row["pk"]).update(name=name)

    # Pass 2: a File still under the opaque ``attachment{ext}`` fallback takes the name
    # of its earliest referencing part (its parts now all carry names from pass 1).
    # Content-addressed dedup means one File backs many parts; the earliest part by
    # (sent_at, created_at, pk) owns the shared display name. Idempotent: a renamed File
    # no longer matches, and one whose earliest name is itself ``attachment{ext}``
    # rewrites to the identical value and is skipped.
    stale_files = (
        file_model._base_manager.using(database)
        .filter(filename__startswith="attachment.")
        .order_by()
        .values_list("pk", "filename")
    )
    for file_pk, filename in stale_files.iterator(chunk_size=2000):
        earliest = (
            part_model._base_manager.using(database)
            .filter(file_id=file_pk)
            .order_by("message__sent_at", "message__created_at", "pk")
            .values("name", "type", "cid", "message__external_id", "message__message_type")
            .first()
        )
        if earliest is None:
            continue
        new_name = earliest["name"] or _derived_part_name(
            mime=earliest["type"] or "",
            cid=earliest["cid"] or "",
            external_id=earliest["message__external_id"] or "",
            is_chat=earliest["message__message_type"] == _CHAT_KIND,
            index=0,
        )
        if new_name and new_name != filename:
            file_model._base_manager.using(database).filter(pk=file_pk).update(filename=new_name)


class Migration(migrations.Migration):
    """Data fix: nameless attachment parts and their fallback-named files carry derived names."""

    # Pass 2 reads storage.File through the historical registry, so this must
    # order after storage's leaf as well as messaging's own (the loader adds the
    # latter); ``__latest__`` resolves to the leaf at materialization time.
    dependencies = [("storage", "__latest__")]
    operations = [
        migrations.RunPython(name_unnamed_attachment_parts, migrations.RunPython.noop),
    ]
