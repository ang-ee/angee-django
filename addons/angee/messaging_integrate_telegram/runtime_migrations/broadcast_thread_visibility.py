"""Fix Telegram broadcast threads born private before the visibility hint.

The pre-hint ingest landed a broadcast channel's threads under the chat-thread
default — ``public_thread`` modality with ``private`` visibility — because
``ParsedThread`` could not name a visibility. The adapter now marks broadcast
threads public at creation; this one-time data fix converges the rows created
before that. Downstream ``makemigrations`` cannot represent a data fix, hence
the addon-owned runtime migration.
"""

from __future__ import annotations

from django.db import migrations
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    """Return whether messaging carries the thread axes and channel backend to fix.

    A data-only fix has no new-state schema marker: it is applicable whenever the
    target shapes exist, and its body is idempotent (the predicate excludes
    already-public rows), so materializing it on a fresh project is a no-op.
    """

    thread = project_state.models.get(("messaging", "thread"))
    channel = project_state.models.get(("messaging", "channel"))
    if thread is None or channel is None:
        return False
    return (
        "modality" in thread.fields
        and "visibility" in thread.fields
        and "backend_class" in channel.fields
    )


def mark_broadcast_threads_public(apps, schema_editor) -> None:
    """Set public visibility on telegram broadcast threads born under the private default.

    Scoped to threads of telegram-backed channels so no other producer's
    private public-shaped thread is touched. Historical models through
    ``_base_manager`` — a migration is a system operation.
    """

    thread_model = apps.get_model("messaging", "Thread")
    channel_model = apps.get_model("messaging", "Channel")
    database = schema_editor.connection.alias
    telegram_ids = list(
        channel_model._base_manager.using(database)
        .filter(backend_class="telegram")
        .values_list("pk", flat=True)
    )
    if not telegram_ids:
        return
    thread_model._base_manager.using(database).filter(
        channel_id__in=telegram_ids,
        modality="public_thread",
        visibility="private",
    ).update(visibility="public")


class Migration(migrations.Migration):
    """Data fix: telegram broadcast threads become public."""

    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.RunPython(mark_broadcast_threads_public, migrations.RunPython.noop),
    ]
