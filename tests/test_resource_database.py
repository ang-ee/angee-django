"""Native resource import keeps row, ledger, content type and grants co-located."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_resource_import_uses_selected_database(tmp_path):
    script = r'''
import sys
from pathlib import Path
from io import StringIO
import yaml
from angee.base.testing.packages import write_addon_distribution
root = Path(sys.argv[1])
package = root / 'fixture_resources'
package.mkdir()
(package / '__init__.py').touch()
resources = package / 'resources'
resources.mkdir()
(resources / 'rows.yaml').write_text(yaml.safe_dump({
    '_meta': {'model': 'resources_testing.ContentTypeRow'},
    'rows': [{'_xref': 'seed', 'name': 'selected row', 'content_type': 'auth.User'}],
}))
(resources / 'grants.yaml').write_text(yaml.safe_dump([
    {'resource': 'resources/probe:seed', 'relation': 'viewer', 'subject': 'auth/user:1'},
]))
write_addon_distribution(package, 'fixture_resources', capabilities={'resources': {'master': [
    {'path': 'rows.yaml'},
    {'path': 'grants.yaml', 'kind': 'grants', 'depends_on': ['rows.yaml']},
]}}, metadata_root=root)
sys.path.insert(0, str(root))
from django.conf import settings
class InstanceRouter:
    def db_for_read(self, model, **hints):
        instance = hints.get('instance')
        return instance._state.db if instance is not None else 'default'
    db_for_write = db_for_read
settings.configure(
    INSTALLED_APPS=['django.contrib.contenttypes', 'django.contrib.auth', 'rebac',
                    'angee.base', 'angee.resources', 'angee.resources.testing', 'fixture_resources'],
    SECRET_KEY='resource-alias', ANGEE_MERGE_GUARDS=[],
    ANGEE_RESOURCE_SOURCE_CLASSES={'path': 'angee.resources.sources.PathSource',
                                  'url': 'angee.resources.sources.UrlSource'},
    ANGEE_RESOURCE_ENTRY_KIND_CLASSES={}, REBAC_LOCAL_BACKEND_STORAGE='registry',
    DATABASE_ROUTERS=[InstanceRouter()],
    DATABASES={alias: {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(root / f'{alias}.sqlite3')}
               for alias in ('default', 'selected')},
)
settings.DATABASES['contender'] = {**settings.DATABASES['selected']}
import django
django.setup()
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from rebac import backend, database_context, system_context
from rebac.models import PermissionAuditEvent, SchemaDefinition, SchemaRelation, active_relationship_model
from rebac.models.schema_write import schema_changes
from angee.resources.testing.models import ContentTypeRow, Resource
for alias in ('default', 'selected'):
    call_command('migrate', database=alias, run_syncdb=True, verbosity=0)
    get_user_model().objects.db_manager(alias).create(username='actor')
with schema_changes(using='selected'):
    definition = SchemaDefinition.objects.using('selected').create(resource_type='resources/probe')
    SchemaRelation.objects.using('selected').create(
        definition_id=definition.pk, name='viewer', allowed_subjects=[{'type': 'auth/user'}])
ContentType.objects.db_manager('default').filter(app_label='auth', model='user').delete()
ContentType.objects.clear_cache()
default_type = ContentType.objects.db_manager('default').get_for_model(get_user_model())
selected_type = ContentType.objects.db_manager('selected').get_for_model(get_user_model())
assert default_type.pk != selected_type.pk
ContentTypeRow.objects.using('default').create(name='default untouched')
with database_context('default'), system_context(reason='tests.resources.selected'):
    ledger = Resource.objects.db_manager('selected')
    owner = apps.get_app_config('fixture_resources')
    call_command('resources', 'validate', 'master', database='selected', stdout=StringIO())
    assert not Resource._base_manager.using('selected').exists()
    assert not ContentTypeRow.objects.using('selected').exists()
    first = ledger.load_addons((owner,), tiers=['master'])
    assert first.created == 2
    assert ledger.resolve('fixture_resources.seed')._state.db == 'selected'
    repeated = ledger.load_addons((owner,), tiers=['master'])
    assert repeated.created == repeated.updated == 0
    assert repeated.skipped == 2
    assert backend().schema().get_definition('resources/probe') is None
row = ContentTypeRow.objects.using('selected').get()
expected = ContentType.objects.db_manager('selected').get_for_model(get_user_model())
assert row.content_type_id == expected.pk
assert Resource._base_manager.using('selected').get().source_path == 'resources/rows.yaml'
assert not Resource._base_manager.using('default').exists()
assert ContentTypeRow.objects.using('default').get().name == 'default untouched'
assert active_relationship_model().objects.using('selected').filter(
    resource_type='resources/probe', relation='viewer').count() == 1
assert not active_relationship_model().objects.using('default').exists()
assert PermissionAuditEvent.objects.using('selected').filter(
    kind=PermissionAuditEvent.KIND_RELATIONSHIP_GRANT).count() == 1
assert not PermissionAuditEvent.objects.using('default').filter(
    kind=PermissionAuditEvent.KIND_RELATIONSHIP_GRANT).exists()

# Force the absent-read / winning-insert interleaving at Django's native lookup.
# The actual unique table and get_or_create IntegrityError retry remain active.
from unittest.mock import patch
from django.db.models.query import QuerySet
from angee.resources.exceptions import ResourceLoadError
original_get = QuerySet.get
winner = None
injected = False
def absent_then_winner(queryset, *args, **kwargs):
    global injected, winner
    if queryset.model is Resource and kwargs.get('xref') == 'raced' and not injected:
        injected = True
        winner = Resource._base_manager.db_manager('contender').create(
            source_addon='race', xref='raced', source_path='winner', tier='master',
            content_hash='winner', target_model=get_user_model()._meta.label, target_id='1',
        )
        raise Resource.DoesNotExist
    return original_get(queryset, *args, **kwargs)
with database_context('selected'), system_context(reason='tests.resources.race'):
    assert not Resource._base_manager.using('selected').filter(source_addon='race', xref='raced').exists()
    with patch.object(QuerySet, 'get', absent_then_winner):
        try:
            Resource.objects.db_manager('selected').upsert_target(
                source_addon='race', xref='raced', instance=row, model=ContentTypeRow,
                source='loser', tier='master', content_hash='loser',
            )
        except ResourceLoadError:
            pass
        else:
            raise AssertionError('incompatible native get_or_create winner was overwritten')
    assert injected
    # The independent contender committed the actual unique winner; the losing
    # producer transaction cannot roll that row back.
    try:
        Resource.objects.db_manager('selected').upsert_target(
            source_addon='race', xref='raced', instance=row, model=ContentTypeRow,
            source='loser', tier='master', content_hash='loser',
        )
    except ResourceLoadError:
        pass
    else:
        raise AssertionError('committed incompatible winner was overwritten')
    winner.refresh_from_db(using='selected')
    assert winner.target_model == get_user_model()._meta.label
    assert winner.source_path == 'winner'
    manager = Resource.objects.db_manager('selected')
    first = manager.upsert_target(source_addon='race', xref='same', instance=row,
        model=ContentTypeRow, source='first', tier='master', content_hash='first')
    again = manager.upsert_target(source_addon='race', xref='same', instance=row,
        model=ContentTypeRow, source='second', tier='master', content_hash='second')
    assert first.pk == again.pk and again.content_hash == 'second'
    assert not Resource._base_manager.using('default').exists()
'''
    result = subprocess.run(
        [sys.executable, '-c', script, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1], env=dict(os.environ),
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
