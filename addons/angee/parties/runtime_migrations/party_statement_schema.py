"""Django makemigrations output for the declared statement cutover schema."""

import angee.base.fields
import django.core.validators
import django.db.models.deletion
import django.db.models.manager
import simple_history.models
from django.conf import settings
from django.db import migrations, models


def applies(state):
    if ("parties", "party") not in state.models:
        return False
    if any(("provenance", name) not in state.models for name in ("changeset", "rule", "seed")):
        # Provenance's changeset and principal schemas are pending in this build.
        return False
    names = {"historicalparty", "historicalperson", "historicalorganization", "partystatement", "historicalpartystatement"}
    present = {name for name in names if ("parties", name) in state.models}
    if present == names:
        return False
    if present:
        raise ValueError("Party statement/history schema is partial; reconcile its native migration state.")
    return True


class Migration(migrations.Migration):

    dependencies = [
        ('integrate', '__latest__'),
        ('storage', '__latest__'),
        migrations.swappable_dependency(settings.ANGEE_CHANGESET_MODEL),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='HistoricalOrganization',
            fields=[
                ('id', models.BigIntegerField(auto_created=True, blank=True, db_index=True, verbose_name='ID')),
                ('created_at', models.DateTimeField(blank=True, db_index=True, editable=False)),
                ('updated_at', models.DateTimeField(blank=True, db_index=True, editable=False)),
                ('display_name', models.TextField()),
                ('notes', models.TextField(blank=True, default='')),
                ('handle_count', models.PositiveIntegerField(db_index=True, default=0)),
                ('raw_vcard', models.TextField(blank=True, default='')),
                ('extensions', models.JSONField(blank=True, default=dict)),
                ('source_uid', models.CharField(blank=True, default='', max_length=512)),
                ('source_etag', models.CharField(blank=True, default='', max_length=512)),
                ('first_met_note', models.TextField(blank=True, default='')),
                ('legal_name', models.TextField(blank=True, default='')),
                ('domain', models.CharField(blank=True, db_index=True, default='', max_length=255)),
                ('history_id', models.AutoField(primary_key=True, serialize=False)),
                ('history_date', models.DateTimeField(db_index=True)),
                ('history_change_reason', models.TextField(null=True)),
                ('history_type', models.CharField(choices=[('+', 'Created'), ('~', 'Changed'), ('-', 'Deleted')], max_length=1)),
                ('avatar', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='storage.file')),
                ('changeset', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.ANGEE_CHANGESET_MODEL)),
                ('created_by', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('folder', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='parties.folder')),
                ('history_user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('introduced_by', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='parties.party')),
                ('merged_into', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='parties.party')),
                ('party_ptr', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='parties.party')),
                ('updated_by', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'historical organization',
                'verbose_name_plural': 'historical organizations',
                'ordering': ('-history_date', '-history_id'),
                'get_latest_by': ('history_date', 'history_id'),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name='HistoricalParty',
            fields=[
                ('id', models.BigIntegerField(auto_created=True, blank=True, db_index=True, verbose_name='ID')),
                ('created_at', models.DateTimeField(blank=True, db_index=True, editable=False)),
                ('updated_at', models.DateTimeField(blank=True, db_index=True, editable=False)),
                ('display_name', models.TextField()),
                ('notes', models.TextField(blank=True, default='')),
                ('handle_count', models.PositiveIntegerField(db_index=True, default=0)),
                ('raw_vcard', models.TextField(blank=True, default='')),
                ('extensions', models.JSONField(blank=True, default=dict)),
                ('source_uid', models.CharField(blank=True, default='', max_length=512)),
                ('source_etag', models.CharField(blank=True, default='', max_length=512)),
                ('first_met_note', models.TextField(blank=True, default='')),
                ('history_id', models.AutoField(primary_key=True, serialize=False)),
                ('history_date', models.DateTimeField(db_index=True)),
                ('history_change_reason', models.TextField(null=True)),
                ('history_type', models.CharField(choices=[('+', 'Created'), ('~', 'Changed'), ('-', 'Deleted')], max_length=1)),
                ('avatar', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='storage.file')),
                ('changeset', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.ANGEE_CHANGESET_MODEL)),
                ('created_by', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('folder', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='parties.folder')),
                ('history_user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('introduced_by', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='parties.party')),
                ('merged_into', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='parties.party')),
                ('updated_by', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'historical party',
                'verbose_name_plural': 'historical parties',
                'ordering': ('-history_date', '-history_id'),
                'get_latest_by': ('history_date', 'history_id'),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name='HistoricalPerson',
            fields=[
                ('id', models.BigIntegerField(auto_created=True, blank=True, db_index=True, verbose_name='ID')),
                ('created_at', models.DateTimeField(blank=True, db_index=True, editable=False)),
                ('updated_at', models.DateTimeField(blank=True, db_index=True, editable=False)),
                ('display_name', models.TextField()),
                ('notes', models.TextField(blank=True, default='')),
                ('handle_count', models.PositiveIntegerField(db_index=True, default=0)),
                ('raw_vcard', models.TextField(blank=True, default='')),
                ('extensions', models.JSONField(blank=True, default=dict)),
                ('source_uid', models.CharField(blank=True, default='', max_length=512)),
                ('source_etag', models.CharField(blank=True, default='', max_length=512)),
                ('first_met_note', models.TextField(blank=True, default='')),
                ('name_prefix', models.TextField(blank=True, default='')),
                ('given_name', models.TextField(blank=True, default='')),
                ('additional_name', models.TextField(blank=True, default='')),
                ('family_name', models.TextField(blank=True, default='')),
                ('name_suffix', models.TextField(blank=True, default='')),
                ('nickname', models.TextField(blank=True, default='')),
                ('birthday', models.DateField(blank=True, null=True)),
                ('anniversary', models.DateField(blank=True, null=True)),
                ('history_id', models.AutoField(primary_key=True, serialize=False)),
                ('history_date', models.DateTimeField(db_index=True)),
                ('history_change_reason', models.TextField(null=True)),
                ('history_type', models.CharField(choices=[('+', 'Created'), ('~', 'Changed'), ('-', 'Deleted')], max_length=1)),
                ('avatar', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='storage.file')),
                ('changeset', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.ANGEE_CHANGESET_MODEL)),
                ('created_by', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('folder', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='parties.folder')),
                ('history_user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('introduced_by', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='parties.party')),
                ('merged_into', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='parties.party')),
                ('party_ptr', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='parties.party')),
                ('updated_by', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('user', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'historical person',
                'verbose_name_plural': 'historical persons',
                'ordering': ('-history_date', '-history_id'),
                'get_latest_by': ('history_date', 'history_id'),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name='PartyStatement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True, db_index=True)),
                ('asserted_by', models.TextField()),
                ('source_kind', angee.base.fields.StateField(choices=[('person', 'Person'), ('system_of_record', 'System of record'), ('directory', 'Directory'), ('network', 'Network'), ('import', 'Import'), ('seed', 'Seed'), ('document', 'Document'), ('extraction', 'Extraction'), ('rule', 'Rule')], db_index=True, max_length=16)),
                ('snak', angee.base.fields.StateField(choices=[('value', 'Value'), ('some_value', 'Some Value'), ('no_value', 'No Value')], db_index=True, default='value', max_length=10)),
                ('confidence', models.FloatField(default=1.0, validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(1)])),
                ('rank', angee.base.fields.StateField(choices=[('preferred', 'Preferred'), ('normal', 'Normal')], db_index=True, default='normal', max_length=9)),
                ('status', angee.base.fields.StateField(choices=[('asserted', 'Asserted'), ('withdrawn', 'Withdrawn'), ('quarantined', 'Quarantined'), ('deprecated', 'Deprecated')], db_index=True, default='asserted', max_length=11)),
                ('applied', models.BooleanField(default=False)),
                ('evidence', models.JSONField(blank=True, default=list)),
                ('basis_hash', models.CharField(blank=True, max_length=64)),
                ('aspect', models.CharField(choices=[('handle', 'handle')], max_length=64)),
                ('changeset', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.ANGEE_CHANGESET_MODEL)),
                ('handle', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='parties.handle')),
                ('link', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='integrate.recordlink')),
                ('subject', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='parties.party')),
                ('superseded', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='parties.partystatement')),
            ],
            options={
                'db_table': 'parties_party_statement',
                'base_manager_name': '_angee_base',
                'default_manager_name': 'objects',
            },
            managers=[
                ('objects', django.db.models.manager.Manager()),
                ('_angee_base', django.db.models.manager.Manager()),
            ],
        ),
        migrations.CreateModel(
            name='HistoricalPartyStatement',
            fields=[
                ('id', models.BigIntegerField(auto_created=True, blank=True, db_index=True, verbose_name='ID')),
                ('created_at', models.DateTimeField(blank=True, db_index=True, editable=False)),
                ('updated_at', models.DateTimeField(blank=True, db_index=True, editable=False)),
                ('asserted_by', models.TextField()),
                ('source_kind', angee.base.fields.StateField(choices=[('person', 'Person'), ('system_of_record', 'System of record'), ('directory', 'Directory'), ('network', 'Network'), ('import', 'Import'), ('seed', 'Seed'), ('document', 'Document'), ('extraction', 'Extraction'), ('rule', 'Rule')], db_index=True, max_length=16)),
                ('snak', angee.base.fields.StateField(choices=[('value', 'Value'), ('some_value', 'Some Value'), ('no_value', 'No Value')], db_index=True, default='value', max_length=10)),
                ('confidence', models.FloatField(default=1.0, validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(1)])),
                ('rank', angee.base.fields.StateField(choices=[('preferred', 'Preferred'), ('normal', 'Normal')], db_index=True, default='normal', max_length=9)),
                ('status', angee.base.fields.StateField(choices=[('asserted', 'Asserted'), ('withdrawn', 'Withdrawn'), ('quarantined', 'Quarantined'), ('deprecated', 'Deprecated')], db_index=True, default='asserted', max_length=11)),
                ('applied', models.BooleanField(default=False)),
                ('evidence', models.JSONField(blank=True, default=list)),
                ('basis_hash', models.CharField(blank=True, max_length=64)),
                ('aspect', models.CharField(choices=[('handle', 'handle')], max_length=64)),
                ('history_id', models.AutoField(primary_key=True, serialize=False)),
                ('history_date', models.DateTimeField(db_index=True)),
                ('history_change_reason', models.TextField(null=True)),
                ('history_type', models.CharField(choices=[('+', 'Created'), ('~', 'Changed'), ('-', 'Deleted')], max_length=1)),
                ('changeset', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to=settings.ANGEE_CHANGESET_MODEL)),
                ('handle', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='parties.handle')),
                ('history_user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('link', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='integrate.recordlink')),
                ('subject', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='parties.party')),
                ('superseded', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='parties.partystatement')),
            ],
            options={
                'verbose_name': 'historical party statement',
                'verbose_name_plural': 'historical party statements',
                'ordering': ('-history_date', '-history_id'),
                'get_latest_by': ('history_date', 'history_id'),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.AddConstraint(
            model_name='partystatement',
            constraint=models.CheckConstraint(condition=models.Q(('confidence__gte', 0), ('confidence__lte', 1), models.Q(('rank', 'normal'), models.Q(('rank', 'preferred'), ('source_kind', 'person')), _connector='OR'), ('snak__in', ['value', 'some_value', 'no_value']), ('source_kind__in', ['person', 'system_of_record', 'directory', 'network', 'import', 'seed', 'document', 'extraction', 'rule']), ('status__in', ['asserted', 'withdrawn', 'quarantined', 'deprecated'])), name='parties_partystatement_stance'),
        ),
        migrations.AddConstraint(
            model_name='partystatement',
            constraint=models.UniqueConstraint(condition=models.Q(('aspect', 'handle'), ('superseded__isnull', True)), fields=('asserted_by', 'subject', 'aspect', 'handle'), name='parties_partystatement_key_handle'),
        ),
        migrations.AddConstraint(
            model_name='partystatement',
            constraint=models.UniqueConstraint(condition=models.Q(('aspect', 'handle'), ('superseded__isnull', True), ('applied', True)), fields=('subject', 'aspect', 'handle'), name='parties_partystatement_applied_handle'),
        ),
        migrations.AddConstraint(
            model_name='partystatement',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('aspect', 'handle'), _negated=True), ('handle__isnull', False), _connector='OR'), name='parties_partystatement_member_handle'),
        ),
        migrations.AddConstraint(
            model_name='partystatement',
            constraint=models.CheckConstraint(condition=models.Q(('aspect__in', ('handle',))), name='parties_partystatement_aspect'),
        ),
    ]
