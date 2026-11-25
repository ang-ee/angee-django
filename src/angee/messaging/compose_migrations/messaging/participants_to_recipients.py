"""Migration template: messaging's ``Participant`` becomes ``Recipient``, with its rows.

Copy this module into the project's ``runtime/messaging/migrations/`` under the
project's next migration name, replace the placeholder dependency with the
project's own messaging leaf (never ``__latest__``), and keep its bytes; then run
``makemigrations messaging`` until it reports no changes, and migrate. A migration
``makemigrations`` writes without it deletes the ``Participant`` table and creates
``Recipient``: the drop guard refuses that on a database whose table holds rows,
but written where the table was empty and applied elsewhere, it drops them.

In this order, in one transaction:

1. The model is renamed, and with it its table (``messaging_participant`` becomes
   ``messaging_recipient``): no row is copied, dropped or created.
2. A message without a sender takes the handle of its ``from`` row, of the first
   one when it has two.
3. The ``from`` rows naming a handle other than their message's sender, outside
   the sender's merge group, are counted and the count is logged. Nothing is
   stored: each message's source keeps its ``From`` header.
4. The ``from`` rows are deleted: a message's sender is ``Message.sender`` alone.
5. A row without a message (a thread membership, which nothing in the framework
   writes or reads) refuses the migration with their count and the statement
   that deletes them. Nothing is applied.
6. ``message`` becomes required, ``role`` keeps ``to``, ``cc`` and ``bcc`` (its
   column narrows to three characters), the relations take their new reverse
   names, the two old unique constraints give way to
   ``uq_recipient_message_handle_role``, and ``joined_at`` and ``left_at`` are
   dropped. The public id prefix (``rcp_``, was ``ptp_``) is the model's
   ``sqid_prefix``: the field is not migration state, so no operation names it.

The reverse restores the columns, constraints, choices and names. It writes no
``from`` row back and keeps the senders step 2 set: the release before this one
reads a sender from the message alone.

Every row is read and written through historical models. They keep
``Meta.ordering`` on ``sqid``, which migration state does not carry, so every
query here clears or names its order.
"""

from __future__ import annotations

import logging

from django.core.management.base import CommandError
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps
from django.db.models.functions import Coalesce

from angee.base.fields.state import StateField

FROM = "from"
PREVIOUS_TABLE = "messaging_participant"
"""The table step 1 renames; a refusal rolls the rename back, so its remedy names this table."""

logger = logging.getLogger(__name__)


def _rows(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor, model: str) -> models.QuerySet:
    return apps.get_model("messaging", model)._base_manager.using(schema_editor.connection.alias).order_by()


def adopt_senders(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Step 2: a message without a sender takes the handle of its first ``from`` row."""

    from_rows = _rows(apps, schema_editor, "Recipient").filter(role=FROM)
    first = from_rows.filter(message_id=models.OuterRef("pk")).order_by("pk").values("handle_id")[:1]
    messages = _rows(apps, schema_editor, "Message")
    messages.filter(sender__isnull=True, pk__in=from_rows.values("message_id")).update(
        sender_id=models.Subquery(first),
    )
    # Flush the update's deferred FK checks before step 6 alters the tables.
    schema_editor.connection.check_constraints(table_names=[messages.model._meta.db_table])


def count_other_senders(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Step 3: count and log the ``from`` rows naming a handle outside their sender's merge group."""

    other = _rows(apps, schema_editor, "Recipient").filter(role=FROM).alias(
        _named=Coalesce("handle__merged_into_id", "handle_id"),
        _sender=Coalesce("message__sender__merged_into_id", "message__sender_id"),
    ).exclude(_named=models.F("_sender")).count()
    logger.warning(
        "%s 'from' row(s) named a handle other than their message's sender and are deleted; each message keeps "
        "its sender, and its source keeps the From header.",
        other,
    )


def delete_from_rows(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Step 4: delete the ``from`` rows; the sender is ``Message.sender`` alone."""

    _rows(apps, schema_editor, "Recipient").filter(role=FROM).delete()


def refuse_rows_without_a_message(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Step 5: refuse while a row has no message, with their count and the statement that deletes them."""

    orphans = _rows(apps, schema_editor, "Recipient").filter(message__isnull=True).count()
    if orphans:
        raise CommandError(
            f"Nothing was applied: {orphans} messaging.Participant row(s) have no message and cannot become "
            f"recipients; delete them with `DELETE FROM {PREVIOUS_TABLE} WHERE message_id IS NULL;` in "
            f"`manage.py dbshell --database {schema_editor.connection.alias}`, then migrate again."
        )


class Migration(migrations.Migration):
    # Placeholder: replace with this project's explicit messaging leaf, the migration
    # whose state still has ``Participant``.
    dependencies = [
        ("messaging", "0000_messaging_leaf"),
    ]
    operations = [
        migrations.RenameModel("Participant", "Recipient"),
        migrations.RunPython(adopt_senders, migrations.RunPython.noop),
        migrations.RunPython(count_other_senders, migrations.RunPython.noop),
        migrations.RunPython(delete_from_rows, migrations.RunPython.noop),
        migrations.RunPython(refuse_rows_without_a_message, migrations.RunPython.noop),
        migrations.AlterField(
            "recipient",
            "message",
            models.ForeignKey(on_delete=models.CASCADE, related_name="recipients", to="messaging.message"),
        ),
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
        migrations.RemoveField("recipient", "joined_at"),
        migrations.RemoveField("recipient", "left_at"),
    ]
