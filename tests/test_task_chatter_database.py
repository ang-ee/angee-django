"""Real task callbacks retain their saved record's native database ownership."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_actual_task_schedule_log_and_record_chatter_use_selected_database(tmp_path):
    script = r'''
import os
from io import StringIO
from pathlib import Path
os.environ.pop('DATABASE_URL', None)
from tests import settings as source_settings
source_settings.DATABASES = {
    alias: {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}
    for alias in ('default', 'selected')
}
os.environ['DJANGO_SETTINGS_MODULE'] = 'tests.settings'
import django
django.setup()
import tests.conftest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone
from rebac import (
    RelationshipTuple, actor_context, database_context, system_context,
    to_object_ref, to_subject_ref, write_relationships,
)
from angee.base.build import extension_source_map
from angee.base.testing.permissions import emit_permissions, installed_field_owners
from angee.provenance.context import changeset
from angee.provenance.testing import load_statement_sources
from tests.chatterdemo.models import ChatterDoc
Task = apps.get_model('projects', 'Task')
ActivityType = apps.get_model('projects', 'ActivityType')
Attachment = apps.get_model('messaging', 'ThreadAttachment')
Thread = apps.get_model('messaging', 'Thread')
Message = apps.get_model('messaging', 'Message')
Follower = apps.get_model('messaging', 'ThreadFollower')
Notification = apps.get_model('messaging', 'ThreadNotification')
Queue = apps.get_model('work', 'Queue')
Stage = apps.get_model('work', 'Stage')
Sequence = apps.get_model('sequence', 'Sequence')
Party = apps.get_model('parties', 'Party')
Handle = apps.get_model('parties', 'Handle')
configs = tuple(apps.get_app_configs())
emit_permissions(configs, Path(os.environ['ANGEE_TEST_PERMISSION_DIR']),
                 sources=extension_source_map(configs, field_owners=installed_field_owners(configs)))
for alias in ('default', 'selected'):
    call_command('migrate', database=alias, run_syncdb=True, verbosity=0)
    call_command('rebac', 'sync', database=alias, verbosity=0, stdout=StringIO())
# Different native content-type IDs expose a default-database pointer lookup.
ContentType.objects.db_manager('default').filter(app_label='chatterdemo', model='chatterdoc').delete()
ContentType.objects.clear_cache()
assert ContentType.objects.db_manager('default').get_for_model(ChatterDoc).pk != (
    ContentType.objects.db_manager('selected').get_for_model(ChatterDoc).pk)
for alias in ('default', 'selected'):
    with database_context(alias):
        load_statement_sources('parties')
        load_statement_sources('messaging')
    with (database_context(alias), system_context(reason='tests.task.database.fixture'),
          changeset(fresh=True, using=alias)):
        ActivityType.objects.db_manager(alias).create(key='call', name=f'{alias} call')
        user = get_user_model().objects.db_manager(alias).create_user(username='actor')
        reader = get_user_model().objects.db_manager(alias).create_user(username='reader')
        record = ChatterDoc(pk=1, title=f'{alias} record')
        record.save(using=alias)
        write_relationships([RelationshipTuple(to_object_ref(record), 'writer', to_subject_ref(user))])
        if alias == 'selected':
            write_relationships([RelationshipTuple(to_object_ref(record), 'reader', to_subject_ref(reader))])
        else:
            with actor_context(user):
                for index in range(2):
                    Task.objects.db_manager(alias).schedule(
                        record, title=f'default task {index}', assignee=user, activity_type='call')
                    record.message_system_post(body=f'default note {index}', message_type=None, subtype_key='note')
watched = (ChatterDoc, Task, ActivityType, Attachment, Thread, Message, Follower, Notification,
           Queue, Stage, Sequence, Party, Handle, Party.statements.model)
def default_snapshot():
    return {model._meta.label: list(model._base_manager.using('default').order_by('pk').values())
            for model in watched}
sentinel = default_snapshot()
with database_context('default'), system_context(reason='tests.task.database.operation'):
    record = ChatterDoc._base_manager.using('selected').get(pk=1)
    user = get_user_model()._base_manager.using('selected').get(username='actor')
    reader = get_user_model()._base_manager.using('selected').get(username='reader')
    with actor_context(user), changeset(fresh=True, using='selected'), database_context('default'):
        # Native create() supplies using to save(), while ID-valued FKs leave
        # the unsaved candidate unbound until its persistence owner prepares it.
        direct = Task.objects.db_manager('selected').create(
            title='Direct selected task', assignee_id=user.pk,
            created_by_id=user.pk, activity_type_id='call')
        assert direct._state.db == direct.queue._state.db == direct.stage._state.db == 'selected'
        assert direct.queue.created_by_id == user.pk
        original = Task._base_manager.using('default').first()
        assert original is not None
        original.stage_id = -1
        try:
            original.save(using='selected')
        except ValidationError:
            pass
        else:
            raise AssertionError('missing stage accepted')
        assert original._state.db == 'default'
        original_queue = Queue._base_manager.using('default').first()
        original_queue.default_estimate = 0
        original_queue.estimate_allow_zero = False
        try:
            original_queue.save(using='selected')
        except ValidationError:
            pass
        else:
            raise AssertionError('invalid queue estimate accepted')
        assert original_queue._state.db == 'default'
        class WriteSelectedRouter:
            def db_for_read(self, model, **hints):
                return 'default'
            def db_for_write(self, model, **hints):
                return 'selected'
        with override_settings(DATABASE_ROUTERS=[WriteSelectedRouter()]):
            queue = Queue._base_manager.using('selected').get(pk=direct.queue_id)
            queue.name = 'Selected write router'
            queue.save()
            assert queue._state.db == 'selected'
        task = Task.objects.db_manager('selected').schedule(
            record, title='Selected task', assignee=user, activity_type='call')
        assert task._state.db == 'selected'
        logged = Task.objects.db_manager('selected').log(
            record, activity_type='call', occurred_on=timezone.localdate(), note='Selected exchange')
        assert logged._state.db == 'selected' and logged.status == Task.TaskStatus.DONE
        # These saved-row verbs must select their own database with default ambient.
        task.complete(feedback="selected completion")
        task.complete(feedback="replayed completion")
        assert task.status == Task.TaskStatus.DONE
        follower = record.message_subscribe(user=reader)
        assert follower._state.db == 'selected'
        assert record.message_is_follower(user=reader)
        message = record.message_system_post(
            body='selected note', message_type=None, subtype_key='note',
            recipient_user_ids=(reader.pk,), autofollow_author=True, idempotency_key='selected-note')
        assert message._state.db == 'selected'
        replay = record.message_system_post(
            body='selected note', message_type=None, subtype_key='note',
            recipient_user_ids=(reader.pk,), autofollow_author=True, idempotency_key='selected-note')
        assert replay.pk == message.pk and replay._state.db == 'selected'
        attachment = Attachment.objects.for_record(record)
        assert attachment._state.db == 'selected'
        assert attachment.record_ref.model_label == ChatterDoc._meta.label
        assert attachment.record_ref.public_id == record.sqid
    assert Task._base_manager.using('selected').count() == 3
    assert Message._base_manager.using('selected').filter(preview__contains='selected call done').exists()
    assert Message._base_manager.using('selected').filter(preview='selected note').count() == 1
    assert Notification._base_manager.using('selected').filter(message=message, user=reader).count() == 1
    assert Follower._base_manager.using('selected').filter(last_read_message=message).exists()
    assert default_snapshot() == sentinel
'''
    result = subprocess.run(
        [sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, ANGEE_TEST_PERMISSION_DIR=str(tmp_path)),
        capture_output=True, text=True, check=False,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
