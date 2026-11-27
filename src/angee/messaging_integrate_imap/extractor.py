"""Paged, historical mbox ingestion through the reviewed Storage archive workflow."""

from __future__ import annotations

import hashlib
import mailbox
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager, nullcontext
from dataclasses import replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, ClassVar

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from rebac import system_context

from angee.messaging.backends import ParsedMessage
from angee.messaging_integrate_imap.parser import parse_message_or_fallback
from angee.storage.models import UploadState
from angee.storage.uploads import PROXY_CHUNK_SIZE
from angee.workflows.steps import NextPage
from angee.workflows_integrate.archive_steps import ArchiveExecutionReporter, ArchiveExtractor

PAGE_MESSAGES = 25
PAGE_BYTES = 64 * 1024 * 1024
MESSAGE_BYTES = 64 * 1024 * 1024
PAGE_SECONDS = 30
PROBE_BYTES = 64 * 1024


def _recognizes(head: bytes) -> bool:
    """Require an mbox envelope followed by recognizable RFC message headers."""

    if not head.startswith(b"From ") or b"\n" not in head:
        return False
    headers = head.split(b"\n", 1)[1].replace(b"\r\n", b"\n").split(b"\n\n", 1)[0]
    names = {line.split(b":", 1)[0].lower() for line in headers.splitlines() if b":" in line}
    return bool(names & {b"from", b"date", b"message-id", b"subject", b"to"})


class MboxExtractor(ArchiveExtractor):
    """Import a plain mbox File into a paused IMAP channel, without a server connection."""

    key = "imap_mbox"
    label = "Email archive (mbox)"
    target_resource = "messaging.Channel"
    target_filters: ClassVar[dict[str, Any]] = {"backend_class": "imap", "lifecycle": "paused"}
    target_instructions = "Pause an IMAP channel you can edit, then retry this step to choose the destination."

    def recognizes(self, subject: Any) -> bool:
        if subject.upload_state != UploadState.READY:
            return False
        with subject.open_stream() as stream:
            return _recognizes(stream.read(PROBE_BYTES))

    def execute(self, subject: Any, target_pk: str, reporter: ArchiveExecutionReporter) -> Any:
        channel = reporter.context.load(apps.get_model("messaging.Channel"), target_pk, permission="write")
        return channel.import_imap_mbox(subject, reporter=reporter)


def _signature(path: Path) -> list[int]:
    stat = path.stat()
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


@contextmanager
def _source_path(
    file: Any, state: dict[str, Any], reporter: ArchiveExecutionReporter,
) -> Iterator[tuple[Path, list[int]]]:
    """Use local bytes in place; spool remote bytes to one attempt-owned temporary file.

    Verify the Storage identity before the first write. A retained local stat
    signature avoids hashing a large, unchanged reference mount on every page.
    """

    if file.upload_state != UploadState.READY:
        raise ValidationError("The archive file is not ready.")
    if state and (state["file"] != str(file.sqid) or state["digest"] != file.content_hash):
        raise ValidationError("The archive file changed. Start a new import for the updated file.")
    local = file.local_path()
    with closing(NamedTemporaryFile(prefix="angee-mbox-")) if local is None else nullcontext() as temporary:
        path = Path(temporary.name) if temporary is not None else local
        before = _signature(path)
        if local is None or state.get("signature") != before:
            digest = hashlib.sha256()
            size = 0
            with file.open_stream() as stream:
                while chunk := stream.read(PROXY_CHUNK_SIZE):
                    reporter.heartbeat()
                    size += len(chunk)
                    if size > file.size_bytes:
                        raise ValidationError("The archive bytes no longer match the Storage file. Re-index it first.")
                    digest.update(chunk)
                    if temporary is not None:
                        temporary.write(chunk)
            if size != file.size_bytes or digest.hexdigest() != file.content_hash:
                raise ValidationError("The archive bytes no longer match the Storage file. Re-index it first.")
            if temporary is not None:
                temporary.flush()
                before = _signature(path)
            elif _signature(path) != before:
                raise ValidationError("The archive changed while it was being read. Retry with an unchanged file.")
        yield path, before


def import_mbox_page(
    file: Any, *, channel: Any, own_addresses: frozenset[str], reporter: ArchiveExecutionReporter,
) -> dict[str, Any] | NextPage:
    """Land a bounded page, then reconcile late reply references until a pass is stable.

    Replays of identical observations can heal parent/thread links. Different
    observations already in the channel are preserved. Alternating traversal
    handles both chronological and reverse exports without assuming date order.
    """

    state = reporter.context.state or {}
    offset = state.get("offset", 0)
    round_number = state.get("pass", 0)
    changed = state.get("changed", False)
    parsed: list[ParsedMessage] = []
    total_bytes = 0
    deadline = time.monotonic() + PAGE_SECONDS
    reporter.heartbeat()
    with _source_path(file, state, reporter) as (path, signature), closing(mailbox.mbox(path, create=False)) as archive:
        with path.open("rb") as stream:
            if not _recognizes(stream.read(PROBE_BYTES)):
                raise ValidationError("The file is not a plain mbox email archive.")
        keys = list(archive.iterkeys())
        if round_number % 2:
            keys.reverse()
        for key in keys[offset:]:
            reporter.heartbeat()
            with archive.get_file(key, from_=False) as message_file:
                raw = message_file.read(MESSAGE_BYTES + 1)
            if len(raw) > MESSAGE_BYTES:
                raise ValidationError(f"Archive entry {int(key) + 1} exceeds the 64 MiB message limit.")
            if parsed and total_bytes + len(raw) > PAGE_BYTES:
                break
            message = parse_message_or_fallback(raw, own_addresses=own_addresses)
            parsed.append(replace(message, metadata={
                **message.metadata,
                "archive": {"format": "mbox", "file": str(file.sqid), "digest": file.content_hash, "entry": key},
            }))
            total_bytes += len(raw)
            if len(parsed) >= PAGE_MESSAGES or time.monotonic() >= deadline:
                break
        if _signature(path) != signature:
            raise ValidationError("The archive changed while it was being read. Retry with an unchanged file.")
        reporter.heartbeat()
        with transaction.atomic():
            locked = type(channel)._base_manager.select_for_update().get(pk=channel.pk)
            locked._require_paused_imap(reporter.context.actor)
            if locked.config != channel.config or locked.credential_id != channel.credential_id:
                raise ValidationError("The channel configuration changed. Retry the import.")
            with system_context(reason="messaging_integrate_imap.mbox_history"):
                messages = apps.get_model("messaging.Message").objects
                before = {
                    pk: (thread, parent)
                    for pk, thread, parent in messages.with_external_ids([item.external_id for item in parsed])
                    .filter(channel=locked).values_list("pk", "thread_id", "parent_id")
                }
                landed = messages.ingest(parsed, channel=locked, historical=True, preserve_existing=True)
                changed = changed or any(before.get(row.pk) != (row.thread_id, row.parent_id) for row in landed)
        offset += len(parsed)
        checkpoint = {
            "file": str(file.sqid), "digest": file.content_hash,
            "signature": signature if file.local_path() is not None else None,
            "offset": offset, "pass": round_number, "changed": changed,
        }
        if offset < len(keys):
            return reporter.context.next_page(checkpoint)
        if changed:
            return reporter.context.next_page({**checkpoint, "offset": 0, "pass": round_number + 1, "changed": False})
        return {"file": str(file.sqid), "entries": len(keys), "passes": round_number + 1}
