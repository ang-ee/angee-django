"""Django makemigrations output for the Changeset table, declared so build materializes it.

Provenance has no checked-in migration history: on a database that predates the
addon, ``makemigrations`` would only create this table after ``angee build``, and
every declaration that needs ``Changeset`` in the state (the statement principals,
the integrate and parties folds) would never materialize in that build while the
retirements behind them would. Declaring the table makes the first build plan the
whole chain in order: changeset schema, statement principals, then the folds and
their retirements. Reverse drops the table and every changeset it holds.
"""

import angee.base.fields
import django.db.models.deletion
import django.db.models.manager
import django.utils.timezone
from django.db import migrations, models


def applies(state):
    if ("provenance", "changeset") in state.models:
        return False
    if any(("provenance", name) in state.models for name in ("rule", "seed")):
        raise ValueError("Statement principals exist without the Changeset table; reconcile the native migration state.")
    return True


class Migration(migrations.Migration):

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.CreateModel(
            name='Changeset',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('actor', models.TextField(blank=True, editable=False)),
                ('action', angee.base.fields.StateField(choices=[('C', 'Create'), ('U', 'Update'), ('D', 'Delete')], db_index=True, default='U', max_length=1)),
                ('activity', angee.base.fields.StateField(choices=[('manual', 'Manual'), ('pull', 'Pull'), ('push', 'Push'), ('import', 'Import'), ('merge', 'Merge'), ('revert', 'Revert'), ('seed', 'Seed'), ('system', 'System')], db_index=True, default='manual', max_length=6)),
                ('change_reason', models.TextField(blank=True)),
                ('correlation_id', models.CharField(blank=True, db_index=True, max_length=255)),
                ('idempotency_key', models.CharField(blank=True, db_index=True, max_length=255)),
                ('origin_object_id', models.CharField(blank=True, max_length=1024, null=True)),
                ('occurred_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('recorded_at', models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ('state', angee.base.fields.StateField(choices=[('open', 'Open'), ('closed', 'Closed')], db_index=True, default='open', max_length=6)),
                ('outcome', angee.base.fields.StateField(blank=True, choices=[('success', 'Success'), ('failure', 'Failure')], db_index=True, max_length=7, null=True)),
                ('hash', models.CharField(blank=True, editable=False, max_length=64)),
                ('counts', models.JSONField(blank=True, default=dict, editable=False)),
                ('touched', models.JSONField(blank=True, default=list, editable=False)),
                ('origin_content_type', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='contenttypes.contenttype')),
            ],
            options={
                'ordering': ('-recorded_at', '-pk'),
                'abstract': False,
                'swappable': 'ANGEE_CHANGESET_MODEL',
                'base_manager_name': '_angee_base',
                'default_manager_name': 'objects',
                'indexes': [models.Index(fields=['-recorded_at', '-id'], name='provenance__recorde_2d89dd_idx'), models.Index(fields=['origin_content_type', 'origin_object_id'], name='provenance__origin__fc47e9_idx')],
            },
            managers=[
                ('objects', django.db.models.manager.Manager()),
                ('_angee_base', django.db.models.manager.Manager()),
            ],
        ),
    ]
