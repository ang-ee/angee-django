"""One Facebook takeout facade shared by CLI, ZIP, and mounted-drive clients."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rebac import system_context

from angee.messaging.backup_ingest import (
    ContentKeyCounter,
    batch_ingest,
    thread_watermarks,
)
from angee.messaging_integrate_facebook.archive import FacebookArchive
from angee.messaging_integrate_facebook.parser import (
    FacebookPostMatcher,
    parsed_comment,
    parsed_connection,
    parsed_message,
    parsed_post,
)
from angee.parties.connections import ingest_connections
from angee.posts.ingest import land_posts

logger = logging.getLogger(__name__)

_IMPORT_REASON = "messaging_integrate_facebook.takeout_import"
_WATERMARK_REASON = "messaging_integrate_facebook.takeout_import.watermarks"
# Public records land in bounded chunks so a photo-heavy archive never holds
# every album's bytes resident — the message path's discipline, applied here.
_PUBLIC_CHUNK = 200


def import_archive(
    root: Path | str,
    channel: Any,
    *,
    resume: bool = False,
    dry_run: bool = False,
    limit: int | None = None,
    on_batch: Callable[[int], None] | None = None,
) -> dict[str, int]:
    """Import one extracted Facebook export and return JSON-safe item counts.

    Messages stream through the byte-bounded batch owner with optional
    per-thread resume watermarks. Own posts and comments land through the posts
    public overlay in bounded chunks (media bytes are read per chunk, never all
    at once), and friend records land through parties' batch connection owner.
    ``limit`` bounds takeout messages (the potentially unbounded collection);
    the smaller public and connection sections still converge in full.
    ``on_batch`` receives a running landed total after every message flush and
    public chunk — extractors thread the workflow heartbeat through it.
    """

    archive = FacebookArchive(root)
    own_name = archive.own_name()
    if not own_name:
        own_name = str(channel.display_name or "Facebook User")
        logger.warning(
            "Facebook export below %s carries no profile_information.json; "
            "falling back to %r for direction attribution — own messages will "
            "import as inbound until a run with the profile section present.",
            root,
            own_name,
        )
    counter = ContentKeyCounter()
    watermarks = (
        thread_watermarks(channel, reason=_WATERMARK_REASON)
        if resume and not dry_run
        else {}
    )
    messages = archive.messages(
        counter=counter,
        watermarks=watermarks,
        limit=limit,
    )
    message_count = batch_ingest(
        channel,
        messages,
        lambda record: parsed_message(record, own_name=own_name),
        reason=_IMPORT_REASON,
        dry_run=dry_run,
        on_batch=on_batch,
    )

    post_records = archive.posts(counter=counter)
    matcher = FacebookPostMatcher(post_records)
    comments = [
        parsed_comment(
            record,
            own_name=own_name,
            counter=counter,
            matcher=matcher,
        )
        for record in archive.comments()
    ]
    connections = [parsed_connection(record) for record in archive.connections()]
    landed = message_count
    if not dry_run:
        with system_context(reason=_IMPORT_REASON):
            for start in range(0, len(post_records), _PUBLIC_CHUNK):
                chunk = [
                    parsed_post(record, archive=archive, own_name=own_name)
                    for record in post_records[start : start + _PUBLIC_CHUNK]
                ]
                land_posts(channel, chunk, owner_id=channel.owner_id)
                landed += len(chunk)
                if on_batch is not None:
                    on_batch(landed)
            for start in range(0, len(comments), _PUBLIC_CHUNK):
                land_posts(
                    channel,
                    comments[start : start + _PUBLIC_CHUNK],
                    owner_id=channel.owner_id,
                )
                landed += len(comments[start : start + _PUBLIC_CHUNK])
                if on_batch is not None:
                    on_batch(landed)
        ingest_connections(connections, created_by_id=channel.owner_id)
    return {
        "messages": message_count,
        "posts": len(post_records),
        "comments": len(comments),
        "connections": len(connections),
    }
