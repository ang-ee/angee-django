"""Explicit capture-metadata migration retains verified legacy source state."""

import os
import subprocess
import sys
from pathlib import Path


def test_old_colocated_history_migrates_capture_metadata_and_preserves_replay(tmp_path):
    # Two interpreters share the same real database. The first uses the old
    # simple-history field bases and serializer; the second uses the adapter.
    script = r"""
import hashlib
import json
import sys
from pathlib import Path
from django.conf import settings
mode, root = sys.argv[1], Path(sys.argv[2])
settings.configure(
    INSTALLED_APPS=['django.contrib.contenttypes', 'django.contrib.auth', 'rebac',
                    'simple_history', 'angee.base', 'angee.iam', 'angee.provenance', 'angee.provenance.testing'],
    SECRET_KEY='retained-history-upgrade', AUTH_USER_MODEL='auth.User',
    ANGEE_CHANGESET_MODEL='provenance.Changeset', ANGEE_MERGE_GUARDS=[],
    PROVENANCE_POLICY='angee.provenance.contrib.angee.policy.AngeePolicy',
    REBAC_LOCAL_BACKEND_STORAGE='registry',
    DATABASES={alias: {'ENGINE':'django.db.backends.sqlite3', 'NAME':str(root / (alias + '.sqlite3'))}
               for alias in ('default', 'selected')},
)
import django
django.setup()
from django.core.management import call_command
from django.db import connections, models
from django.db.migrations.state import ModelState
from django.db.migrations.writer import MigrationWriter
from django.utils import timezone
from simple_history.models import HistoricalRecords
from simple_history.utils import get_history_manager_for_model
from rebac import SubjectRef, actor_context, database_context, system_context
from rebac.models import SchemaDefinition, SchemaPermission
from rebac.models.schema_write import schema_changes
from angee.iam.models.base import ProtectedDataModel
from angee.provenance.context import changeset, current_changeset
from angee.provenance.contrib.angee.history import HistoryMixin, ModelHistory
from angee.provenance.engine.base import ChangesetAttribution as LegacyAttribution
from angee.provenance.history import _ChangesetHistory
from angee.base.serialization import canonical_json, json_safe
from angee.provenance.testing.models import Changeset

# This is the pre-adoption historical field declaration, not a copy of capture
# or replay. The new phase uses the canonical native capture/replay adapter.
row_bases = (ProtectedDataModel,) if mode == 'old' else (HistoryMixin, ProtectedDataModel)
class Row(*row_bases):
    title = models.CharField(max_length=30)
    history = (HistoricalRecords(bases=(LegacyAttribution,), use_base_model_db=True, excluded_fields=["sqid"])
               if mode == 'old' else ModelHistory())
    class Meta:
        app_label = 'provenance'
        rebac_resource_type = 'provenance/upgraderow'

history_model = get_history_manager_for_model(Row).model
field_state = [[name, MigrationWriter.serialize(field)[0]]
               for name, field in ModelState.from_model(history_model).fields.items()]
assert 'source_database' not in {field.name for field in history_model._meta.concrete_fields}
actor = SubjectRef.of('auth/user', '1')
if mode == 'old':
    for alias in ('default', 'selected'):
        call_command('migrate', database=alias, run_syncdb=True, verbosity=0)
        from django.contrib.auth import get_user_model
        get_user_model().objects.db_manager(alias).create(username='legacy actor')
    with schema_changes(using='selected'):
        definition = SchemaDefinition.objects.using('selected').create(resource_type='provenance/upgraderow')
        for action in ('create', 'read', 'write'):
            SchemaPermission.objects.using('selected').create(
                definition_id=definition.pk, name=action, expression='authenticated')
    # Retained old rows are materialized through Django's historical schema;
    # capture is disabled so the current engine cannot manufacture the fixture.
    settings.SIMPLE_HISTORY_ENABLED = False
    with system_context(reason='tests.legacy.fixture'), database_context('selected'):
        original = Row(id=1, title='before')
        original.save(using='selected')
        first = Changeset(change_reason='legacy creation', state='closed', outcome='success',
                          touched=['provenance.row'])
        first.save(using='selected')
        edited = Changeset(change_reason='legacy edit', state='closed', outcome='success',
                           touched=['provenance.row'])
        edited.save(using='selected')
        values = {field.attname: getattr(original, field.attname) for field in history_model.tracked_fields}
        history_model.objects.using('selected').create(
            **values, changeset_id=first.pk, history_type='+', history_date=timezone.now())
        Row._base_manager.using('selected').filter(pk=1).update(title='after')
        values['title'] = 'after'
        history_model.objects.using('selected').create(
            **values, changeset_id=edited.pk, history_type='~', history_date=timezone.now())
    # Frozen a59dc01a _document_chunks field projection and encoding. This fixture
    # proves bytes, not merely equality of two exports by the replacement engine.
    fields = {field.name: field.value_from_object(edited)
              for field in edited._meta.concrete_fields if field.name != 'hash'}
    rows = list(history_model.objects.using('selected').filter(changeset_id=edited.pk).order_by('history_id'))
    values = [{field.name: field.value_from_object(row) for field in row._meta.concrete_fields} for row in rows]
    legacy = ('{"changeset":' + canonical_json(json_safe(fields)) + ',"history":{"provenance.row":['
              + ','.join(canonical_json(json_safe(value)) for value in values) + ']}}')
    digest = hashlib.sha256(legacy.encode()).hexdigest()
    Changeset._base_manager.using('selected').filter(pk=edited.pk).update(hash=digest)
    (root / 'legacy.json').write_text(legacy)
    (root / 'receipt.json').write_text(json.dumps({'pk':str(edited.pk), 'digest':digest,
        'fields':field_state,
        'columns':[column.name for column in connections['selected'].introspection.get_table_description(
            connections['selected'].cursor(), history_model._meta.db_table)]}))
else:
    retained = json.loads((root / 'receipt.json').read_text())
    # Native schema editing preserves the old capture column, then records the
    # verified fact that Row had no colliding source changeset field.
    import copy
    capture = history_model._meta.get_field('history_changeset')
    legacy_capture = copy.copy(capture)
    legacy_capture.db_column = 'changeset_id'
    legacy_capture.set_attributes_from_name('changeset')
    with connections['selected'].schema_editor() as editor:
        editor.alter_field(history_model, legacy_capture, capture)
        editor.add_field(history_model, history_model._meta.get_field('history_source_complete'))
    history_model.objects.using('selected').update(history_source_complete=True)
    columns = [column.name for column in connections['selected'].introspection.get_table_description(
        connections['selected'].cursor(), history_model._meta.db_table)]
    assert set(columns) == {field.column for field in history_model._meta.local_concrete_fields}
    assert set(columns) == ((set(retained['columns']) - {'changeset_id'})
                            | {'history_changeset_id', 'history_source_complete'})
    edited = Changeset._base_manager.using('selected').get(pk=retained['pk'])
    with system_context(reason='tests.legacy.export'), database_context('selected'):
        legacy = json.loads((root / 'legacy.json').read_text())
        adopted = json.loads(edited.document())
        for value in legacy['history']['provenance.row']:
            value['history_changeset'] = value.pop('changeset')
            value['history_source_complete'] = True
        assert adopted == legacy
    edited.refresh_from_db(using='selected')
    assert hashlib.sha256((root / 'legacy.json').read_bytes()).hexdigest() == retained['digest']
    with actor_context(actor), database_context('default'):
        inverse = edited.revert()
    assert inverse._state.db == 'selected'
    row = Row._base_manager.using('selected').get(pk=1)
    assert row.title == 'before'
    assert get_history_manager_for_model(Row).using('selected').filter(history_changeset_id=inverse.pk).exists()
"""
    environment = {key: value for key, value in os.environ.items() if key != "DJANGO_SETTINGS_MODULE"}
    for mode in ("old", "new"):
        result = subprocess.run(
            [sys.executable, "-c", script, mode, str(tmp_path)],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
