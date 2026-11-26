"""Migration template: messaging's ``Participant`` becomes ``Recipient``, with its rows.

Copy this module into the project's ``runtime/messaging/migrations/`` under the
project's next migration name, replace the placeholder dependency with the
project's own messaging leaf (never ``__latest__``), and keep its bytes.

In this order, in one transaction:

1. A row without a message, of any role (a thread membership, which nothing in
   the framework writes or reads), refuses the migration with their count and the
   statement that deletes them. Nothing has changed when it refuses, and no later
   step touches such a row.
2. The model is renamed, and with it its table (``messaging_participant`` becomes
   ``messaging_recipient``): no row is copied, dropped or created.
3. A message without a sender takes the handle of its ``from`` row, of the first
   one when it has two.
4. The ``from`` rows naming a handle other than their message's sender, outside
   the sender's merge group, are counted and the count is logged. Nothing is
   stored: each message's source keeps its ``From`` header.
5. The ``from`` rows are deleted: a message's sender is ``Message.sender`` alone.
6. ``role`` keeps ``to``, ``cc`` and ``bcc`` (its column narrows to three
   characters, which rewrites the recipient table), the relations take their new
   reverse names, the two old unique constraints give way to
   ``uq_recipient_message_handle_role``, ``joined_at`` and ``left_at`` are
   dropped with whatever they hold, a drop the migration declares
   (``IntendedRemoveField``) so that ``migrate``'s drop guard applies it, and
   last ``message`` becomes required. Making it required re-creates
   its foreign key, which locks the message table until the migration commits, so
   it comes after the rewrite. The public id prefix (``rcp_``, was ``ptp_``) is
   the model's ``sqid_prefix``: the field is not migration state, so no operation
   names it.

The reverse restores the columns, constraints, choices and names. It writes no
``from`` row back and keeps the senders step 3 set: the release before this one
reads a sender from the message alone.

Every row is read and written through historical models. They keep
``Meta.ordering`` on ``sqid``, which migration state does not carry, so every
query here clears or names its order.
"""

from __future__ import annotations

import logging

from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps
from django_compose.operations import IntendedRemoveField

from angee.base.fields.state import StateField
from angee.base.querysets import MergeableQuerySet

FROM = "from"

logger = logging.getLogger(__name__)


def _rows(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor, model: str) -> models.QuerySet:
    return apps.get_model("messaging", model)._base_manager.using(schema_editor.connection.alias).order_by()


def remedy(table: str) -> str:
    """The statement that deletes the rows step 1 refuses: those of ``table`` without a message."""

    return f"DELETE FROM {table} WHERE message_id IS NULL;"


def refuse_rows_without_a_message(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Step 1: refuse, before anything changes, while a row has no message; say how many and what deletes them."""

    rows = _rows(apps, schema_editor, "Participant").filter(message__isnull=True)
    count = rows.count()
    if count:
        raise RuntimeError(
            f"Recipient upgrade refused: {count} messaging.Participant row(s) have no message and cannot become "
            f"recipients. Delete them with `{remedy(rows.model._meta.db_table)}` in `manage.py dbshell --database "
            f"{schema_editor.connection.alias}`, then retry migrate. Nothing was changed."
        )


def adopt_senders(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Step 3: a message without a sender takes the handle of its first ``from`` row."""

    from_rows = _rows(apps, schema_editor, "Recipient").filter(role=FROM)
    first = from_rows.filter(message_id=models.OuterRef("pk")).order_by("pk").values("handle_id")[:1]
    messages = _rows(apps, schema_editor, "Message")
    messages.filter(sender__isnull=True, pk__in=from_rows.values("message_id")).update(
        sender_id=models.Subquery(first),
    )
    # Flush the update's deferred FK checks before step 6 alters the tables.
    schema_editor.connection.check_constraints(table_names=[messages.model._meta.db_table])


def count_other_senders(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Step 4: count and log the ``from`` rows naming a handle outside their sender's merge group."""

    other = _rows(apps, schema_editor, "Recipient").filter(role=FROM).alias(
        _named=MergeableQuerySet.survivor_id("handle__"),
        _sender=MergeableQuerySet.survivor_id("message__sender__"),
    ).exclude(_named=models.F("_sender")).count()
    logger.warning(
        "%s 'from' row(s) named a handle other than their message's sender and are deleted when this migration "
        "commits; each message keeps its sender, and its source keeps the From header.",
        other,
    )


def delete_from_rows(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Step 5: delete the ``from`` rows; the sender is ``Message.sender`` alone."""

    _rows(apps, schema_editor, "Recipient").filter(role=FROM).delete()


class Migration(migrations.Migration):
    # Placeholder: replace with this project's explicit messaging leaf, the migration
    # whose state still has ``Participant``.
    dependencies = [
        ("messaging", "0000_messaging_leaf"),
    ]
    operations = [
        migrations.RunPython(refuse_rows_without_a_message, migrations.RunPython.noop),
        migrations.RenameModel("Participant", "Recipient"),
        migrations.RunPython(adopt_senders, migrations.RunPython.noop),
        migrations.RunPython(count_other_senders, migrations.RunPython.noop),
        migrations.RunPython(delete_from_rows, migrations.RunPython.noop),
        migrations.AlterField(
            "recipient",
            "role",
            StateField(choices=[("to", "To"), ("cc", "Cc"), ("bcc", "Bcc")], db_index=True, default="to", max_length=3),
        ),
        migrations.AlterField(
            "recipient",
            "thread",
            models.ForeignKey(
                blank=True, null=True, on_delete=models.CASCADE, related_name="recipients", to="messaging.thread",
            ),
        ),
        migrations.AlterField(
            "recipient",
            "handle",
            models.ForeignKey(on_delete=models.CASCADE, related_name="recipient_rows", to="parties.handle"),
        ),
        migrations.RemoveConstraint("recipient", "uq_participant_message_handle_role"),
        migrations.RemoveConstraint("recipient", "uq_participant_thread_handle_role"),
        migrations.AddConstraint(
            "recipient",
            models.UniqueConstraint(fields=("message", "handle", "role"), name="uq_recipient_message_handle_role"),
        ),
        IntendedRemoveField("recipient", "joined_at"),
        IntendedRemoveField("recipient", "left_at"),
        # Last: re-creating the foreign key locks the message table until the commit.
        migrations.AlterField(
            "recipient",
            "message",
            models.ForeignKey(on_delete=models.CASCADE, related_name="recipients", to="messaging.message"),
        ),
    ]
