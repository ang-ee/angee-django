"""Rename the retired ``discrepant`` link status to ``failed`` on live links and their history.

A vocabulary rename only: every row keeps its state, evidence and timestamps.
The rename runs as SQL on both tables because the historical ``StateField``
admits neither the old value after nor the new value before the field change.
Reverse renames ``failed`` back to ``discrepant`` on both tables and loses nothing.

The guard waits while the history table is still pending in the same build and
refuses a history table that lacks the link's vocabulary.
"""

from django.db import migrations

import angee.base.fields


def applies(state):
    link = state.models.get(("integrate", "recordlink"))
    history = state.models.get(("integrate", "historicalrecordlink"))
    if link is None:
        if history is not None:
            raise ValueError("RecordLink failure vocabulary has a history table without its link.")
        return False
    if history is None:
        # record_link_history_state is pending in this build; the composer re-evaluates next round.
        return False
    old = ["discrepant" in dict(owner.fields["status"].choices) for owner in (link, history)]
    if any(old) and not all(old):
        raise ValueError("RecordLink failure vocabulary is partially migrated.")
    return all(old)


def _rename(apps, schema_editor, old, new):
    for name in ("RecordLink", "HistoricalRecordLink"):
        model = apps.get_model("integrate", name)
        table = schema_editor.quote_name(model._meta.db_table)
        column = schema_editor.quote_name(model._meta.get_field("status").column)
        schema_editor.execute(f"UPDATE {table} SET {column} = %s WHERE {column} = %s", [new, old])


def forwards(apps, schema_editor):
    _rename(apps, schema_editor, "discrepant", "failed")


def backwards(apps, schema_editor):
    _rename(apps, schema_editor, "failed", "discrepant")


class Migration(migrations.Migration):

    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.RunPython(forwards, backwards),
        migrations.AlterField(
            model_name='historicalrecordlink',
            name='status',
            field=angee.base.fields.StateField(choices=[('current', 'Current'), ('observed', 'Observed'), ('unavailable', 'Unavailable'), ('failed', 'Failed'), ('withdrawn', 'Withdrawn'), ('tombstone', 'Tombstone')], db_index=True, default='observed', max_length=11),
        ),
        migrations.AlterField(
            model_name='recordlink',
            name='status',
            field=angee.base.fields.StateField(choices=[('current', 'Current'), ('observed', 'Observed'), ('unavailable', 'Unavailable'), ('failed', 'Failed'), ('withdrawn', 'Withdrawn'), ('tombstone', 'Tombstone')], db_index=True, default='observed', max_length=11),
        ),
    ]
