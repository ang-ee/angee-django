"""A static placement router alone decides which database every journey uses.

This is the one test with a database router. Production may place models with a
Django router; transaction owners, commit hooks and raw SQL must then follow the
routed alias instead of a hard-coded ``default``, which single-database tests
cannot observe.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_static_placement_router_keeps_every_journey_off_default(tmp_path: Path) -> None:
    script = r'''
import os
from io import StringIO
from pathlib import Path
os.environ.pop('DATABASE_URL', None)
from tests import settings as source_settings


class PlacementRouter:
    """Place every model on the second alias, as a deployment router would."""

    def db_for_read(self, model, **hints):
        return 'placed'

    db_for_write = db_for_read

    def allow_relation(self, first, second, **hints):
        return True

    def allow_migrate(self, db, app_label, **hints):
        return db == 'placed'


source_settings.DATABASES = {
    alias: {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'} for alias in ('default', 'placed')
}
source_settings.DATABASE_ROUTERS = [PlacementRouter()]
os.environ['DJANGO_SETTINGS_MODULE'] = 'tests.settings'
import django
django.setup()
import importlib
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connections
from django.test.utils import CaptureQueriesContext
from rebac import RelationshipTuple, actor_context, system_context, to_object_ref, to_subject_ref, write_relationships
from angee.base.build import extension_source_map
from angee.base.testing.permissions import emit_permissions, installed_field_owners
from angee.provenance.context import changeset
from angee.provenance.testing import load_statement_sources
from tests.chatterdemo.models import ChatterDoc
from tests.conftest import Page, addon_schema, create_user, execute_schema, result_data, vault_for
Task = apps.get_model('projects', 'Task')
ActivityType = apps.get_model('projects', 'ActivityType')
configs = tuple(apps.get_app_configs())
emit_permissions(configs, Path(os.environ['ANGEE_TEST_PERMISSION_DIR']),
                 sources=extension_source_map(configs, field_owners=installed_field_owners(configs)))
call_command('migrate', database='placed', run_syncdb=True, verbosity=0)
call_command('rebac', 'sync', database='placed', verbosity=0, stdout=StringIO())
schema = addon_schema(importlib.import_module('angee.knowledge.schema').schemas, 'public')
with CaptureQueriesContext(connections['default']) as default_queries:
    load_statement_sources('parties')
    load_statement_sources('messaging')
    # Create through managers.
    with system_context(reason='tests.placement.fixture'), changeset(fresh=True):
        ActivityType.objects.create(key='call', name='Call')
        user = create_user('actor')
        record = ChatterDoc.objects.create(title='Placed record')
        write_relationships([RelationshipTuple(to_object_ref(record), 'writer', to_subject_ref(user))])
    vault = vault_for(user, name='Placed vault')
    with actor_context(user):
        page = Page.objects.create_in(vault, title='Placed page')
    with system_context(reason='tests.placement.operation'), actor_context(user), changeset(fresh=True):
        task = Task.objects.schedule(record, title='Placed task', assignee=user, activity_type='call')
        message = record.message_system_post(body='Placed note', message_type=None, subtype_key='note')
        # Read.
        assert Task.objects.get(pk=task.pk).title == 'Placed task'
        # Update.
        task.complete(feedback='Placed completion')
        record.title = 'Renamed record'
        record.save()
    # One GraphQL document.
    rows = result_data(execute_schema(schema, 'query { pages { id title } }', user=user))['pages']
    assert [row['title'] for row in rows] == ['Placed page']
    # Delete.
    with system_context(reason='tests.placement.delete'):
        record.delete()
    assert not ChatterDoc._base_manager.filter(pk=record.pk).exists()
for row in (user, record, vault, page, task, message):
    assert row._state.db == 'placed', row
assert default_queries.captured_queries == [], default_queries.captured_queries
'''
    result = subprocess.run(
        [sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, ANGEE_TEST_PERMISSION_DIR=str(tmp_path)),
        capture_output=True, text=True, check=False,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
