"""Real task callbacks preserve scheduling and chatter behavior."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_actual_task_schedule_log_and_record_chatter(tmp_path):
    script = r'''
import os
from io import StringIO
from pathlib import Path
os.environ.pop('DATABASE_URL', None)
from tests import settings as source_settings
source_settings.DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
os.environ['DJANGO_SETTINGS_MODULE'] = 'tests.settings'
import django
django.setup()
import tests.conftest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.utils import timezone
from rebac import RelationshipTuple, actor_context, system_context, to_object_ref, to_subject_ref, write_relationships
from angee.base.build import extension_source_map
from angee.base.testing.permissions import emit_permissions, installed_field_owners
from angee.provenance.context import changeset
from angee.provenance.testing import load_statement_sources
from tests.chatterdemo.models import ChatterDoc
Task = apps.get_model('projects', 'Task')
ActivityType = apps.get_model('projects', 'ActivityType')
Attachment = apps.get_model('messaging', 'ThreadAttachment')
Message = apps.get_model('messaging', 'Message')
Follower = apps.get_model('messaging', 'ThreadFollower')
Notification = apps.get_model('messaging', 'ThreadNotification')
Queue = apps.get_model('work', 'Queue')
configs = tuple(apps.get_app_configs())
emit_permissions(configs, Path(os.environ['ANGEE_TEST_PERMISSION_DIR']),
                 sources=extension_source_map(configs, field_owners=installed_field_owners(configs)))
call_command('migrate', run_syncdb=True, verbosity=0)
call_command('rebac', 'sync', verbosity=0, stdout=StringIO())
load_statement_sources()
with system_context(reason='tests.task.fixture'), changeset(fresh=True):
    ActivityType.objects.create(key='call', name='Call')
    user = get_user_model().objects.create_user(username='actor')
    reader = get_user_model().objects.create_user(username='reader')
    record = ChatterDoc(pk=1, title='Record')
    record.save()
    write_relationships([
        RelationshipTuple(to_object_ref(record), 'writer', to_subject_ref(user)),
        RelationshipTuple(to_object_ref(record), 'reader', to_subject_ref(reader)),
    ])
with system_context(reason='tests.task.operation'):
    record = ChatterDoc._base_manager.get(pk=1)
    user = get_user_model()._base_manager.get(username='actor')
    reader = get_user_model()._base_manager.get(username='reader')
    with actor_context(user), changeset(fresh=True):
        direct = Task.objects.create(
            title='Direct task', assignee_id=user.pk,
            created_by_id=user.pk, activity_type_id='call')
        assert direct._state.db == direct.queue._state.db == direct.stage._state.db == 'default'
        assert direct.queue.created_by_id == user.pk
        invalid = Task._base_manager.get(pk=direct.pk)
        original_queue_cache = invalid.queue
        invalid.stage_id = -1
        try:
            invalid.save()
        except ValidationError:
            pass
        else:
            raise AssertionError('missing stage accepted')
        assert invalid.queue is original_queue_cache
        queue = Queue._base_manager.get(pk=direct.queue_id)
        queue.default_estimate = 0
        queue.estimate_allow_zero = False
        try:
            queue.save()
        except ValidationError:
            pass
        else:
            raise AssertionError('invalid queue estimate accepted')
        cached_task = Task(title='Cached queue task', queue=direct.queue,
                           assignee_id=user.pk, activity_type_id='call')
        cached_task.save()
        assert cached_task._state.db == cached_task.queue._state.db == 'default'
        assert cached_task.number == direct.number + 1
        uncached_task = Task(title='Uncached queue task', queue_id=direct.queue_id,
                             assignee_id=user.pk, activity_type_id='call')
        uncached_task.save()
        assert uncached_task._state.db == uncached_task.queue._state.db == 'default'
        assert uncached_task.number == cached_task.number + 1
        task = Task.objects.schedule(record, title='Scheduled task', assignee=user, activity_type='call')
        logged = Task.objects.log(
            record, activity_type='call', occurred_on=timezone.localdate(), note='Exchange')
        assert logged.status == Task.TaskStatus.DONE
        task.complete(feedback='completion')
        task.complete(feedback='replayed completion')
        assert task.status == Task.TaskStatus.DONE
        follower = record.message_subscribe(user=reader)
        assert follower._state.db == 'default'
        assert record.message_is_follower(user=reader)
        message = record.message_system_post(
            body='note', message_type=None, subtype_key='note',
            recipient_user_ids=(reader.pk,), autofollow_author=True, idempotency_key='note')
        replay = record.message_system_post(
            body='note', message_type=None, subtype_key='note',
            recipient_user_ids=(reader.pk,), autofollow_author=True, idempotency_key='note')
        assert replay.pk == message.pk
        attachment = Attachment.objects.for_record(record)
        assert attachment.record_ref.model_label == ChatterDoc._meta.label
        assert attachment.record_ref.public_id == record.sqid
    assert Task._base_manager.count() == 5
    assert Message._base_manager.filter(preview__contains='Call done').exists()
    assert Message._base_manager.filter(preview='note').count() == 1
    assert Notification._base_manager.filter(message=message, user=reader).count() == 1
    assert Follower._base_manager.filter(last_read_message=message).exists()
'''
    result = subprocess.run(
        [sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, ANGEE_TEST_PERMISSION_DIR=str(tmp_path)),
        capture_output=True, text=True, check=False,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
