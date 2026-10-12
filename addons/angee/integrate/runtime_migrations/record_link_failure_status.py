"""Rename the retired ``discrepant`` link status to ``failed`` on live links and their history.

A vocabulary rename only: every row keeps its state, evidence and timestamps.
Reverse renames ``failed`` back to ``discrepant`` on both tables and loses nothing.
"""

from django.db import migrations

import angee.base.fields


def applies(state):
    owners = [state.models.get(("integrate", name)) for name in ("recordlink", "historicalrecordlink")]
    if all(owner is None for owner in owners):
        return False
    if any(owner is None for owner in owners):
        raise ValueError("RecordLink failure vocabulary has a partial historical schema.")
    old = ["discrepant" in dict(owner.fields["status"].choices) for owner in owners]
    if any(old) and not all(old):
        raise ValueError("RecordLink failure vocabulary is partially migrated.")
    return all(old)


def forwards(apps, schema_editor):
    for name in ("RecordLink", "HistoricalRecordLink"):
        apps.get_model("integrate", name)._base_manager.using(schema_editor.connection.alias).filter(
            status="discrepant",
        ).update(status="failed")


def backwards(apps, schema_editor):
    for name in ("RecordLink", "HistoricalRecordLink"):
        apps.get_model("integrate", name)._base_manager.using(schema_editor.connection.alias).filter(
            status="failed",
        ).update(status="discrepant")


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
