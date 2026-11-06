"""The real attribution storage keeps row, tuple and audit owners aligned."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_actual_changeset_storage_and_scope(outcome):
    root = Path(__file__).resolve().parents[1]
    script = f"expected_outcome = {outcome!r}\n" + """
from django.conf import settings
settings.configure(
    INSTALLED_APPS=['django.contrib.contenttypes', 'django.contrib.auth', 'rebac',
                    'simple_history', 'angee.base', 'angee.provenance', 'angee.provenance.testing'],
    SECRET_KEY='actual-storage-test', AUTH_USER_MODEL='auth.User',
    ANGEE_CHANGESET_MODEL='provenance.Changeset', ANGEE_MERGE_GUARDS=[],
    REBAC_LOCAL_BACKEND_STORAGE='registry',
    DATABASES={'default': {'ENGINE':'django.db.backends.sqlite3', 'NAME':':memory:'}},
)
import django
django.setup()
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection, models, transaction
from rebac import SubjectRef, actor_context, system_context, to_object_ref
from rebac.models import PermissionAuditEvent, SchemaDefinition, SchemaRelation, active_relationship_model
from rebac.models.schema_write import schema_changes
from angee.base.models.persistence import PersistenceMixin
from angee.provenance import context, models as sources
from angee.provenance.testing.models import Changeset
assert Changeset.close is sources.Changeset.close
assert Changeset.save is sources.Changeset.save
call_command('migrate', run_syncdb=True, verbosity=0)
get_user_model().objects.create(username='actor')
with schema_changes():
    SchemaDefinition.objects.create(resource_type='auth/user')
    owner = SchemaDefinition.objects.create(resource_type='provenance/changeset')
    SchemaRelation.objects.create(
        definition_id=owner.pk, name='actor', allowed_subjects=[{'type':'auth/user'}])
actor = SubjectRef.of('auth/user', '1')
with actor_context(actor):
    with context.changeset(fresh=True):
        outer = context.current_changeset()
        with transaction.atomic():
            try:
                with context.changeset(fresh=True):
                    row = context.current_changeset(required=True, record=True)
                    assert isinstance(row, Changeset)
                    assert row._state.db == 'default'
                    assert row.actor_ref == str(actor)
                    if expected_outcome == 'failure':
                        raise ValueError('handled operation failure')
            except ValueError:
                pass
            assert row.outcome is None
        assert context.current_changeset() is outer
    assert context.current_changeset() is None
    stored = Changeset._base_manager.get(pk=row.pk)
    assert stored.state == Changeset.State.CLOSED
    assert stored.outcome == expected_outcome
    with system_context(reason='tests.storage.export'):
        assert stored.document()
    assert Changeset._base_manager.get(pk=row.pk).hash == stored.hash
    stored.scope, stored.key = 'storage', 'created'
    stored.record_created(stored)
    assert stored.created_content_type._state.db == 'default'
    assert Changeset._base_manager.get(pk=row.pk).created_object_id == str(row.pk)
relationships = active_relationship_model().objects
assert relationships.filter(
    resource_type='provenance/changeset', resource_id=to_object_ref(stored).resource_id, relation='actor',
    subject_type='auth/user', subject_id='1').count() == 1
assert PermissionAuditEvent.objects.filter(kind=PermissionAuditEvent.KIND_RELATIONSHIP_GRANT).exists()
class LockProbe(PersistenceMixin):
    value = models.CharField(max_length=30)
    class Meta:
        app_label = 'provenance'
with connection.schema_editor() as editor:
    editor.create_model(LockProbe)
LockProbe.objects.create(id=1, value='locked')
probe = LockProbe(id=1)
with transaction.atomic():
    locked = probe.lock_subject()
    assert locked._state.db == 'default' and locked.value == 'locked'

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
with connection.schema_editor() as editor:
    editor.create_model(ReplayRow)
    editor.create_model(ReplayRow.history.model)
with schema_changes():
    definition = SchemaDefinition.objects.create(resource_type='provenance/replayrow')
    from rebac.models import SchemaPermission
    for action in ('create', 'read', 'write'):
        SchemaPermission.objects.create(
            definition_id=definition.pk, name=action, expression='authenticated')
with actor_context(actor), context.changeset(fresh=True):
    original = ReplayRow(id=1, title='before', counter=7)
    original.save()
with actor_context(actor):
    with context.changeset(fresh=True):
        replay_row = ReplayRow._base_manager.get(pk=1)
        replay_row.title = 'after'
        replay_row.save(update_fields=['title'])
        edited = context.current_changeset(required=True)
    assert edited._state.db == 'default'
    assert ReplayRow.history.filter(changeset_id=edited.pk).exists()
    version = ReplayRow.history.filter(changeset_id=edited.pk).select_related('changeset').get()
    restored = version.get_instance()
    assert restored._state.db == 'default' and restored.counter == 7
    reverted = edited.revert()
    assert reverted._state.db == 'default'
    replay_row = ReplayRow._base_manager.get(pk=1)
    assert (replay_row.title, replay_row.counter) == ('before', 7)
"""
    environment = {key: value for key, value in os.environ.items() if key != 'DJANGO_SETTINGS_MODULE'}
    result = subprocess.run(
        [sys.executable, '-c', script], cwd=root, env=environment,
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
