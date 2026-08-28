"""Converge public-thread messages onto the COMMENT kind the ingest owner derives.

Before the kind decision moved to the ingest owner, each backend declared the
functional kind, so the same act landed differently by arrival path — a
broadcast-channel post ingested by a chat bridge was ``chat`` while a feed post
was ``comment``. The ingest owner now derives the kind from the thread's
structural shape (public-thread content is a ``COMMENT``); this one-time data
fix converges the rows written under the per-backend rule. Downstream
``makemigrations`` cannot represent a data fix, hence the addon-owned runtime
migration.
"""

from __future__ import annotations

from django.db import migrations
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    """Return whether messaging carries the message kind and thread modality to fix.

    A data-only fix has no new-state schema marker: it is applicable whenever the
    target shapes exist, and its body is idempotent (the predicate excludes
    already-converged rows), so materializing it on a fresh project is a no-op.
    """

    message = project_state.models.get(("messaging", "message"))
    thread = project_state.models.get(("messaging", "thread"))
    if message is None or thread is None:
        return False
    return "message_type" in message.fields and "modality" in thread.fields


def mark_public_thread_messages_comment(apps, schema_editor) -> None:
    """Relabel chat-kind messages in public threads as comments.

    Historical models through ``_base_manager`` — a migration is a system
    operation.
    """

    message_model = apps.get_model("messaging", "Message")
    database = schema_editor.connection.alias
    # order_by() clears the model default ordering: it names the live model's
    # ``sqid`` alias, which the historical model cannot resolve.
    message_model._base_manager.using(database).order_by().filter(
        thread__modality="public_thread",
        message_type="chat",
    ).update(message_type="comment")


class Migration(migrations.Migration):
    """Data fix: public-thread messages carry the COMMENT kind."""

    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.RunPython(mark_public_thread_messages_comment, migrations.RunPython.noop),
    ]
