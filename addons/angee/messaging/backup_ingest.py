"""Shared batching + resume-watermark loop for device-backup message import.

A chat-backup import is the same three moves for every platform: read the store's
messages in thread order, map each to the neutral :class:`ParsedMessage`, and
flush through ``Message.objects.ingest`` in bounded batches. Because ingest is
idempotent on ``(channel, external_id)`` the import is resumable — but a naive
re-run re-reads the whole history (and re-fetches its media) every time. Per-thread
resume *watermarks* let the store skip each thread's already-imported prefix in its
own SQL, so an import interrupted by a task time limit advances on re-run instead
of restarting.

The store (the platform-specific reader) owns the wire schema, media resolution,
and the date encoding; this owner is neutral of all three. It reads landed
``Message`` rows grouped by ``thread.external_id`` — the
``chat:<channel-pk>:<thread-external-id>`` key, stripped back to the store's raw
thread key — and hands the store each thread's newest already-imported instant as
a plain ``datetime``; the store converts those to its native date filter. That
``chat:<channel-pk>:`` key is derived inline here (``_THREAD_KEY_PREFIX``),
mirroring how ``Message.objects.ingest`` composes the same key inline when it
lands a chat thread (``angee.messaging.managers``); there is no shared manager
method for it yet, so both sites spell the format out. Centralizing the
derivation on a Thread/Message manager method — the single owner both the ingest
and this resume path would then call — is a follow-up.

The WhatsApp addon predates this owner and still carries its own copy in
:class:`angee.messaging_integrate_whatsapp.backup.BackupImporter`; migrating it
onto this module (its ``_resume_watermarks`` normalizes the recovered key with
``bare_jid`` and pre-converts to CoreData seconds — both moves this owner leaves to
the store) is a mechanical follow-up.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any

from django.apps import apps
from django.db.models import Max
from rebac import system_context

from angee.messaging.backends import ParsedMessage

_THREAD_KEY_PREFIX = "chat:{channel_pk}:"
"""The manager's chat-thread external-id namespace; stripped to recover the store key."""

DEFAULT_MAX_BATCH_BYTES = 64_000_000
"""Flush a batch once its buffered media reaches this many bytes.

The message-count default is fine for text, but a run of large videos would
otherwise hold gigabytes of media bytes resident before the first ingest.
"""


def thread_watermarks(channel: Any, *, reason: str) -> dict[str, datetime]:
    """Return each chat thread's newest already-imported instant, keyed by store key.

    The map is empty for a first import. Keyed by the store's raw thread key —
    the thread's ``chat:<channel>:<key>`` external id with the manager-owned
    prefix stripped — so the store can skip the imported prefix per thread. A
    global ``since`` would instead drop older messages in threads a prior
    interrupted run never reached; per-thread watermarks converge on the last
    imported row and advance from there. The store owns turning each ``datetime``
    into its native date filter (the reason this owner stays date-encoding
    neutral).
    """

    message_model = apps.get_model("messaging", "Message")
    prefix = _THREAD_KEY_PREFIX.format(channel_pk=channel.pk)
    watermarks: dict[str, datetime] = {}
    with system_context(reason=reason):
        rows = (
            message_model._base_manager.filter(thread__channel=channel, sent_at__isnull=False)
            .values("thread__external_id")
            .annotate(latest=Max("sent_at"))
        )
        for row in rows:
            external_id = str(row["thread__external_id"] or "")
            if external_id.startswith(prefix) and row["latest"] is not None:
                key = external_id[len(prefix) :]
                if key:
                    watermarks[key] = row["latest"]
    return watermarks


def batch_ingest(
    channel: Any,
    messages: Iterable[Any],
    parsed_message: Callable[[Any], ParsedMessage],
    *,
    reason: str,
    batch_size: int = 500,
    max_batch_bytes: int = DEFAULT_MAX_BATCH_BYTES,
    dry_run: bool = False,
    on_batch: Callable[[int], None] | None = None,
) -> int:
    """Drive a store's messages through the shared ingest path in batches; return the total.

    ``messages`` yields the store's platform DTOs (each exposing ``media`` for
    byte accounting); ``parsed_message`` maps one DTO onto the neutral messaging
    seam. A batch flushes at ``batch_size`` messages **or** ``max_batch_bytes`` of
    buffered media, whichever comes first. Every chat backup lands under the
    ``CHAT`` kind with the email quotation graph off, so those are fixed here
    rather than re-decided per addon.
    """

    message_model = apps.get_model("messaging", "Message")
    batch_size = max(1, int(batch_size))
    max_batch_bytes = max(1, int(max_batch_bytes))
    total = 0
    batch: list[ParsedMessage] = []
    batch_bytes = 0

    def flush() -> None:
        nonlocal total, batch_bytes
        if not batch:
            return
        if not dry_run:
            with system_context(reason=reason):
                message_model.objects.ingest(
                    batch,
                    channel=channel,
                    message_kind=message_model.MessageKind.CHAT,
                    quote_edges=False,
                )
        total += len(batch)
        if on_batch is not None:
            on_batch(total)
        batch.clear()
        batch_bytes = 0

    for message in messages:
        batch.append(parsed_message(message))
        batch_bytes += sum(len(item.content) for item in getattr(message, "media", ()) if item.content)
        if len(batch) >= batch_size or batch_bytes >= max_batch_bytes:
            flush()
    flush()
    return total
