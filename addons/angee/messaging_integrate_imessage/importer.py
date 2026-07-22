"""Import one iPhone backup's SMS + iMessage history into a messaging channel.

Thin composition: :class:`~.store.ImessageStore` produces the neutral messages,
:func:`~.parser.parsed_message` maps them, and the shared
:mod:`angee.messaging.backup_ingest` owner batches them through
``Message.objects.ingest`` with per-thread resume watermarks. This module owns
only the store lifetime (both SQLite connections close on success or failure) and
wiring the resume watermarks — computed generically by thread, then converted to
the store's native date filter by the store — into the read.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from angee.integrate_iphone.backup import IosBackup
from angee.messaging.backup_ingest import batch_ingest, thread_watermarks
from angee.messaging_integrate_imessage.connect import get_or_create_imessage_channel
from angee.messaging_integrate_imessage.lines import line_channel_name
from angee.messaging_integrate_imessage.parser import parsed_message
from angee.messaging_integrate_imessage.store import ImessageStore

_IMPORT_REASON = "messaging_integrate_imessage.backup_import"
_WATERMARK_REASON = "messaging_integrate_imessage.backup_import.watermarks"


def import_backup(
    channel: Any,
    backup_dir: Path | str,
    *,
    since: datetime | None = None,
    limit: int | None = None,
    batch_size: int = 500,
    dry_run: bool = False,
    resume: bool = False,
    on_batch: Callable[[int], None] | None = None,
) -> int:
    """Open and import one backup's Messages store; return the message total.

    The shared importer facade for the management command and workflow
    extractors — it owns the store lifetime so every client closes both SQLite
    connections. ``resume`` skips each chat's already-imported prefix so an import
    interrupted by the task time limit advances on re-run instead of restarting.
    """

    store = ImessageStore(IosBackup(backup_dir))
    try:
        watermarks = (
            thread_watermarks(channel, reason=_WATERMARK_REASON) if resume and not dry_run else {}
        )
        messages = store.messages(since=since, limit=limit, watermarks=watermarks)
        return batch_ingest(
            channel,
            messages,
            parsed_message,
            reason=_IMPORT_REASON,
            batch_size=batch_size,
            dry_run=dry_run,
            on_batch=on_batch,
        )
    finally:
        store.close()


def import_backup_per_line(
    owner: Any,
    backup_dir: Path | str,
    *,
    since: datetime | None = None,
    limit: int | None = None,
    batch_size: int = 500,
    dry_run: bool = False,
    resume: bool = False,
    on_batch: Callable[[int], None] | None = None,
) -> dict[str, int]:
    """Import one backup split into one channel per local line; return per-line counts.

    Each distinct ``destination_caller_id`` line (:meth:`ImessageStore.line_variants`)
    gets an idempotent iMessage channel owned by ``owner`` — real lines named
    ``Messages <line>``, everything unresolvable pooled into ``Messages (other)`` —
    and the existing single-channel :func:`batch_ingest` path runs once per line over
    that line's raw caller-id variants. Lines are visited in a deterministic order
    (sorted, catch-all last). Returns ``{channel_sqid_or_name: imported_count}``;
    the caller sums the values for a run total.
    """

    store = ImessageStore(IosBackup(backup_dir))
    try:
        variants = store.line_variants()
        results: dict[str, int] = {}
        for line_key, raw_variants in sorted(
            variants.items(), key=lambda item: (item[0] is None, item[0] or "")
        ):
            channel = get_or_create_imessage_channel(owner, name=line_channel_name(line_key))
            watermarks = (
                thread_watermarks(channel, reason=_WATERMARK_REASON) if resume and not dry_run else {}
            )
            messages = store.messages(
                since=since,
                limit=limit,
                watermarks=watermarks,
                caller_ids=tuple(raw_variants),
            )
            results[str(channel.sqid or channel.display_name)] = batch_ingest(
                channel,
                messages,
                parsed_message,
                reason=_IMPORT_REASON,
                batch_size=batch_size,
                dry_run=dry_run,
                on_batch=on_batch,
            )
        return results
    finally:
        store.close()
