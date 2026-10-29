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
