"""Native resource import keeps row, ledger, content type and grants co-located."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_resource_import_validates_replays_and_grants(tmp_path):
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
settings.configure(
    INSTALLED_APPS=['django.contrib.contenttypes', 'django.contrib.auth', 'rebac',
                    'angee.base', 'angee.resources', 'angee.resources.testing', 'fixture_resources'],
    SECRET_KEY='resource-alias', ANGEE_MERGE_GUARDS=[],
    ANGEE_RESOURCE_SOURCE_CLASSES={'path': 'angee.resources.sources.PathSource',
                                  'url': 'angee.resources.sources.UrlSource'},
    ANGEE_RESOURCE_ENTRY_KIND_CLASSES={}, REBAC_LOCAL_BACKEND_STORAGE='registry',
    DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}},
)
import django
django.setup()
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from rebac import backend, system_context
from rebac.models import PermissionAuditEvent, SchemaDefinition, SchemaRelation, active_relationship_model
from rebac.models.schema_write import schema_changes
from angee.resources.testing.models import ContentTypeRow, Resource
call_command('migrate', run_syncdb=True, verbosity=0)
get_user_model().objects.create(username='actor')
with schema_changes():
    definition = SchemaDefinition.objects.create(resource_type='resources/probe')
    SchemaRelation.objects.create(
        definition_id=definition.pk, name='viewer', allowed_subjects=[{'type': 'auth/user'}])
with system_context(reason='tests.resources.load'):
    ledger = Resource.objects
    owner = apps.get_app_config('fixture_resources')
    call_command('resources', 'validate', 'master', stdout=StringIO())
    assert not Resource._base_manager.exists()
    assert not ContentTypeRow.objects.exists()
    first = ledger.load_addons((owner,), tiers=['master'])
    assert first.created == 2
    assert ledger.resolve('fixture_resources.seed')._state.db == 'default'
    repeated = ledger.load_addons((owner,), tiers=['master'])
    assert repeated.created == repeated.updated == 0
    assert repeated.skipped == 2
    assert backend().schema().get_definition('resources/probe') is not None
row = ContentTypeRow.objects.get()
expected = ContentType.objects.get_for_model(get_user_model())
assert row.content_type_id == expected.pk
assert Resource._base_manager.get().source_path == 'resources/rows.yaml'
assert active_relationship_model().objects.filter(
    resource_type='resources/probe', relation='viewer').count() == 1
assert PermissionAuditEvent.objects.filter(
    kind=PermissionAuditEvent.KIND_RELATIONSHIP_GRANT).count() == 1
'''
    result = subprocess.run(
        [sys.executable, '-c', script, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1], env=dict(os.environ),
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
