"""The real attribution storage keeps row, tuple and audit database owners aligned."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_actual_changeset_storage_uses_selected_database_and_restores_scope(outcome):
    root = Path(__file__).resolve().parents[1]
    script = f"expected_outcome = {outcome!r}\n" + """
from django.conf import settings
class DefaultRouter:
    def db_for_read(self, model, **hints):
        return 'default'
    db_for_write = db_for_read
settings.configure(
    INSTALLED_APPS=['django.contrib.contenttypes', 'django.contrib.auth', 'rebac',
                    'simple_history', 'angee.base', 'angee.provenance', 'angee.provenance.testing'],
    SECRET_KEY='actual-storage-alias-test', AUTH_USER_MODEL='auth.User',
    ANGEE_CHANGESET_MODEL='provenance.Changeset', ANGEE_MERGE_GUARDS=[],
    REBAC_LOCAL_BACKEND_STORAGE='registry', DATABASE_ROUTERS=[DefaultRouter()],
    DATABASES={alias: {'ENGINE':'django.db.backends.sqlite3', 'NAME':':memory:'}
               for alias in ('default', 'selected')},
)
import django
django.setup()
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connections, models, transaction
from rebac import SubjectRef, actor_context, database_context, system_context, to_object_ref
from rebac.models import PermissionAuditEvent, SchemaDefinition, SchemaRelation, active_relationship_model
from rebac.models.schema_write import schema_changes
from angee.base.models.persistence import PersistenceMixin
from angee.provenance import context, models as sources
from angee.provenance.testing.models import Changeset
assert Changeset.close is sources.Changeset.close
assert Changeset.save is sources.Changeset.save
for alias in ('default', 'selected'):
    call_command('migrate', database=alias, run_syncdb=True, verbosity=0)
    get_user_model().objects.db_manager(alias).create(username='actor')
    with schema_changes(using=alias):
        SchemaDefinition.objects.using(alias).create(resource_type='auth/user')
        if alias == 'selected':
            owner = SchemaDefinition.objects.using(alias).create(resource_type='provenance/changeset')
            SchemaRelation.objects.using(alias).create(
                definition_id=owner.pk, name='actor', allowed_subjects=[{'type':'auth/user'}])
actor = SubjectRef.of('auth/user', '1')
with database_context('default'), actor_context(actor):
    with context.changeset(fresh=True, using='default'):
        outer = context.current_changeset()
        with transaction.atomic(using='selected'):
            try:
                with context.changeset(fresh=True, using='selected'):
                    row = context.current_changeset(required=True, record=True)
                    assert isinstance(row, Changeset)
                    assert row._state.db == 'selected'
                    assert row.actor_ref == str(actor)
                    # A matching-PK default row exposes misrouted bookkeeping.
                    sentinel = Changeset(pk=row.pk, actor_ref='', change_reason='default sentinel')
                    with system_context(reason='tests.default.sentinel'):
                        sentinel.save(force_insert=True, using='default')
                    if expected_outcome == 'failure':
                        raise ValueError('handled operation failure')
            except ValueError:
                pass
            assert row.outcome is None
        assert context.current_changeset() is outer
    assert context.current_changeset() is None
    stored = Changeset._base_manager.using('selected').get(pk=row.pk)
    assert stored.state == Changeset.State.CLOSED
    assert stored.outcome == expected_outcome
    sentinel.refresh_from_db(using='default')
    assert sentinel.state == Changeset.State.OPEN and sentinel.outcome is None
    with database_context('selected'), system_context(reason='tests.storage.export'):
        assert stored.document()
    assert Changeset._base_manager.using('selected').get(pk=row.pk).hash == stored.hash
    sentinel.refresh_from_db(using='default')
    assert sentinel.hash == ''
    stored.scope, stored.key = 'storage', 'created'
    stored.record_created(stored)
    assert stored.created_content_type._state.db == 'selected'
    assert Changeset._base_manager.using('selected').get(pk=row.pk).created_object_id == str(row.pk)
    sentinel.refresh_from_db(using='default')
    assert sentinel.created_object_id is None
relationships = active_relationship_model().objects
assert relationships.using('selected').filter(
    resource_type='provenance/changeset', resource_id=to_object_ref(stored).resource_id, relation='actor',
    subject_type='auth/user', subject_id='1').count() == 1
assert not relationships.using('default').exists()
assert PermissionAuditEvent.objects.using('selected').filter(kind=PermissionAuditEvent.KIND_RELATIONSHIP_GRANT).exists()
assert not PermissionAuditEvent.objects.using('default').filter(
    kind=PermissionAuditEvent.KIND_RELATIONSHIP_GRANT).exists()
assert not PermissionAuditEvent.objects.using('default').filter(reason='changeset.record').exists()
class LockProbe(PersistenceMixin):
    value = models.CharField(max_length=30)
    class Meta:
        app_label = 'provenance'
for alias in ('default', 'selected'):
    with connections[alias].schema_editor() as editor:
        editor.create_model(LockProbe)
    LockProbe.objects.using(alias).create(id=1, value=alias)
probe = LockProbe(id=1)
probe._state.db = 'selected'
with transaction.atomic(using='selected'):
    locked = probe.lock_subject()
    assert locked._state.db == 'selected' and locked.value == 'selected'

from django.core.exceptions import ImproperlyConfigured
from angee.base.models.base import AngeeDataModel
from angee.provenance.contrib.angee.history import HistoryMixin, ModelHistory
from angee.provenance.statements import StatementsMixin
try:
    ModelHistory(use_base_model_db=False)
except ImproperlyConfigured:
    pass
else:
    raise AssertionError('Angee replay requires co-located source/history/Changeset storage')
class ReplayRow(StatementsMixin, HistoryMixin, AngeeDataModel):
    title = models.CharField(max_length=30)
    counter = models.IntegerField(default=0)
    history = ModelHistory(inherit=True, excluded_fields=['counter'])
    class Meta:
        app_label = 'provenance'
        rebac_resource_type = 'provenance/replayrow'
for alias in ('default', 'selected'):
    with connections[alias].schema_editor() as editor:
        editor.create_model(ReplayRow)
        editor.create_model(ReplayRow.history.model)
    with schema_changes(using=alias):
        definition = SchemaDefinition.objects.using(alias).create(resource_type='provenance/replayrow')
        from rebac.models import SchemaPermission
        for action in ('create', 'read', 'write'):
            SchemaPermission.objects.using(alias).create(
                definition_id=definition.pk, name=action, expression='authenticated')
    with actor_context(actor), context.changeset(fresh=True, using=alias):
        original = ReplayRow(id=1, title='before' if alias == 'selected' else 'default untouched',
                             counter=7 if alias == 'selected' else 99)
        original.save(using=alias)
with actor_context(actor), database_context('default'):
    with context.changeset(fresh=True, using='selected'):
        selected_row = ReplayRow._base_manager.using('selected').get(pk=1)
        selected_row.title = 'after'
        selected_row.save(using='selected', update_fields=['title'])
        edited = context.current_changeset(required=True)
    assert edited._state.db == 'selected'
    assert ReplayRow.history.using('selected').filter(changeset_id=edited.pk).exists()
    version = ReplayRow.history.using('selected').filter(changeset_id=edited.pk).select_related('changeset').get()
    assert version.changeset._state.db == 'selected'
    restored = version.get_instance(using='selected')
    assert restored._state.db == 'selected' and restored.counter == 7
    reverted = edited.revert()
    assert reverted._state.db == 'selected'
    selected_row = ReplayRow._base_manager.using('selected').get(pk=1)
    default_row = ReplayRow._base_manager.using('default').get(pk=1)
    assert (selected_row.title, selected_row.counter) == ('before', 7)
    assert (default_row.title, default_row.counter) == ('default untouched', 99)

"""
    environment = {key: value for key, value in os.environ.items() if key != 'DJANGO_SETTINGS_MODULE'}
    result = subprocess.run(
        [sys.executable, '-c', script], cwd=root, env=environment,
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
