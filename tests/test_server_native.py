"""Optional serving delegates bootstrap and MCP routing to their native owners."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run(script: str) -> None:
    env = dict(os.environ)
    for name in ('DJANGO_SETTINGS_MODULE', 'DJANGO_COMPOSE_MODE', 'ANGEE_PROJECT_DIR'):
        env.pop(name, None)
    result = subprocess.run(
        [sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[1],
        env=env, capture_output=True, text=True, check=False, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('already_populated', [False, True])
def test_server_bootstrap_initializes_logging_once(already_populated):
    _run(f'already_populated = {already_populated!r}\n' + r'''
import sys
from types import ModuleType
from django.conf import settings
logging_owner = ModuleType('native_logging_probe')
calls = []
logging_owner.configure = lambda values: calls.append(values)
sys.modules[logging_owner.__name__] = logging_owner
settings.configure(
    INSTALLED_APPS=['django.contrib.contenttypes', 'django.contrib.auth', 'rebac',
                    'angee.base', 'angee.server'],
    SECRET_KEY='server-bootstrap', ANGEE_MERGE_GUARDS=[], ANGEE_WEB_STATEMENT_TIMEOUT=0,
    LOGGING_CONFIG='native_logging_probe.configure', LOGGING={'version': 1},
)
if already_populated:
    import django
    django.setup()
from angee.server.asgi import application
assert callable(application)
assert len(calls) == 1, calls
''')


def test_mcp_native_mount_uses_inner_root_and_real_lifespan():
    _run(r'''
import sys
from types import ModuleType
from django.conf import settings
from rebac import SubjectRef
verifier = ModuleType('native_mcp_verifier')
verifier.resolve_actor = lambda token: SubjectRef.of('tests/actor', '1') if token == 'native-route-token' else None
sys.modules[verifier.__name__] = verifier
settings.configure(
    INSTALLED_APPS=['django.contrib.contenttypes', 'django.contrib.auth', 'rebac', 'angee.base', 'angee.mcp'],
    SECRET_KEY='native-mcp-mount', ANGEE_MERGE_GUARDS=[],
    ALLOWED_HOSTS=['localhost'], CSRF_TRUSTED_ORIGINS=['http://localhost'],
    ANGEE_MCP_ACTOR_VERIFIER='native_mcp_verifier.resolve_actor',
)
import django
django.setup()
from unittest.mock import patch
from mcp.types import ToolAnnotations
from starlette.testclient import TestClient
from angee.mcp import server

def register_probe(mcp):
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def native_route_probe() -> str:
        return 'native'

# The real capability owns registration, app creation and its native lifespan.
with patch.object(server, '_registrars', return_value=(register_probe,)):
    from django_compose.asgi import application
    with TestClient(application, base_url='http://localhost') as client:
        headers = {'Authorization': 'Bearer native-route-token', 'Accept': 'application/json, text/event-stream'}
        request = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-03-26', 'capabilities': {},
            'clientInfo': {'name': 'native-route-proof', 'version': '1'},
        }}
        response = client.post('/mcp', headers=headers, json=request)
        assert response.status_code == 200, (response.status_code, response.text)
        assert response.json()['result']['serverInfo']['name'] == 'angee'
        nested = client.post('/mcp/mcp', headers=headers, json=request)
        assert nested.status_code == 404, (nested.status_code, nested.text)
''')


def test_native_compose_owns_ready_web_and_graphql_artifacts():
    _run(r'''
import json
import tempfile
from pathlib import Path
from angee.base.testing.packages import write_addon_distribution
from tests.composed_host import boot
with tempfile.TemporaryDirectory(prefix='native-ready-capabilities-') as temporary:
    root = Path(temporary).resolve()
    package = root / 'ready_probe'
    package.mkdir()
    (package / '__init__.py').write_text('')
    (package / 'schema.py').write_text("""
import strawberry
@strawberry.type
class Query:
    @strawberry.field
    def ready_probe(self) -> str:
        return "ready"
schemas = {"public": {"query": Query}}
""")
    web = package / 'web'
    web.mkdir()
    (web / 'package.json').write_text(json.dumps({'name': '@example/ready-probe'}))
    write_addon_distribution(
        package, 'ready_probe', depends_on=('angee.graphql', 'angee.web'), metadata_root=root)
    runtime = root / 'runtime'
    boot(Path.cwd(), runtime, [root], include_examples=False, root_apps=['ready_probe'])
    from django.apps import apps
    from django.conf import settings
    from django_compose.serving import artifact_path, read_receipt
    receipt = read_receipt(runtime)
    assert receipt is not None and not receipt.IN_PROGRESS
    assert {'web/manifest.json', 'web/tailwind.sources.css', 'schemas/public.graphql',
            'schemas/public.metadata.json', 'schemas/publishers.json'} <= set(receipt.OWNED_FILES)
    assert apps.is_installed('django_compose')
    assert 'django_compose' not in settings.COMPOSE_SNAPSHOT.dependencies
    manifest = json.loads(artifact_path('web/manifest.json').read_text())
    assert any(package['app'] == 'ready_probe' for package in manifest['addonPackages'])
    assert 'ready_probe' in artifact_path('schemas/public.graphql').read_text()
''')


def test_verified_publishers_start_and_write_without_schema_imports():
    """Fresh composed startup and first writes use only receipt-owned publisher fields."""

    _run(r'''
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from angee.base.testing.packages import write_addon_distribution
from tests.composed_host import boot
with tempfile.TemporaryDirectory(prefix='native-publisher-artifact-') as temporary:
    root = Path(temporary).resolve()
    package = root / 'publisher_probe'
    package.mkdir()
    (package / '__init__.py').write_text('')
    (package / 'schema.py').write_text("""
import strawberry
import strawberry_django
from django.contrib.auth.models import Group
from strawberry import auto
from angee.graphql.data.hasura import hasura_model_resource
from angee.graphql.subscriptions import changes
@strawberry_django.type(Group)
class GroupNode:
    id: auto
    name: auto = strawberry_django.field(name="display_name")
resource = hasura_model_resource(GroupNode, model=Group, name="probe_groups",
    filterable=("id", "name"), sortable=("name",), aggregatable=("id",),
    public_id_field="id", insert=False, update=False, delete=False)
schemas = {"public": {"query": resource.query, "types": resource.types,
    "subscription": changes(Group, field="probeGroupChanged")}}
""")
    write_addon_distribution(package, 'publisher_probe', depends_on=('angee.graphql',), metadata_root=root)
    runtime = root / 'runtime'
    boot(Path.cwd(), runtime, [root], include_examples=False, root_apps=['publisher_probe'])
    projection = runtime / 'schemas/publishers.json'
    assert json.loads(projection.read_text()) == {'auth.group': ['id', 'name']}
    script = r"""
import importlib.abc
import sys
class NoSchemaImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'publisher_probe.schema', 'angee.graphql.schema', 'strawberry_django_hasura'}:
            raise AssertionError('serving imported schema: ' + fullname)
sys.meta_path.insert(0, NoSchemaImports())
import django
django.setup()
from django.contrib.auth.models import Group
from django.db import connection, transaction
from angee.graphql import publishing
assert 'angee.graphql.schema' not in sys.modules
assert 'publisher_probe.schema' not in sys.modules
with connection.schema_editor() as editor:
    editor.create_model(Group)
observed = []
def observe(sender, payload, **kwargs):
    observed.append(payload)
publishing.change_published.connect(observe, weak=False)
with transaction.atomic():
    row = Group.objects.create(name='before')
    row.name = 'after'
    row.save(update_fields=('name',))
    publishing.broadcast_related_records((row,))
    assert len(observed) == 2
    assert observed[-1].changed_values == {'name': 'after'}
    assert len(connection.run_on_commit) == 3
    transaction.set_rollback(True)
assert not Group.objects.exists()
assert 'angee.graphql.schema' not in sys.modules
"""
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    projection.write_text('{}\n')
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert 'Generated file is missing or altered: schemas/publishers.json; run compose' in result.stderr
    projection.unlink()
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert 'run compose' in result.stderr
''')
