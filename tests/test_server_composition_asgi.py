"""Native ASGI events exercise routing and resource lifetime."""

import os
import subprocess
import sys

import pytest


def test_post_setup_url_import_guard():
    script = """
from django.conf import settings
settings.configure(INSTALLED_APPS=[], ANGEE_WEB_STATEMENT_TIMEOUT=0)
from django.core.exceptions import AppRegistryNotReady
try:
    __import__("angee.server.urls")
except AppRegistryNotReady:
    pass
else:
    raise AssertionError("post-setup seam imported before setup")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_asgi_composition():
    script = """
import asyncio, sys
from types import ModuleType
from contextlib import asynccontextmanager
from django.conf import settings
settings.configure(INSTALLED_APPS=[], ANGEE_WEB_STATEMENT_TIMEOUT=0)
import django
django.setup()
class Block:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith((
            "django_compose.build", "django_compose.declarations", "channels", "uvicorn"
        )):
            raise AssertionError(fullname)
sys.meta_path.insert(0, Block())
from django.apps import apps, AppConfig
from angee.server.asgi import AsgiContribution, _Application
from asgiref.testing import ApplicationCommunicator
from django.core.exceptions import ImproperlyConfigured
seen = []
async def mounted(scope, receive, send):
    seen.append(scope)
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})
@asynccontextmanager
async def first():
    seen.append("enter first")
    try: yield
    finally: seen.append("exit first")
@asynccontextmanager
async def second():
    seen.append("enter second")
    try: yield
    finally: seen.append("exit second")
class Config(AppConfig):
    name = "one"
    path = "/tmp"
    def compose_asgi(self): return self.value
one = Config("one", ModuleType("one"))
two = Config("two", ModuleType("two"))
one.value = AsgiContribution({"/api": mounted, "/api/deep": mounted}, (first,))
two.value = AsgiContribution(lifespans=(second,))
apps.get_app_configs = lambda: [one, two]
app = _Application()
async def main():
    scope = {"type": "http", "path": "/base/api/deep/item", "root_path": "/base", "query_string": b"a=1"}
    comm = ApplicationCommunicator(app, scope)
    assert (await comm.receive_output())["status"] == 204
    await comm.receive_output()
    await comm.wait()
    assert seen.pop() == dict(scope, root_path="/base/api/deep")
    async def fallback(scope, receive, send):
        await send({"type": "http.response.start", "status": 299, "headers": []})
    app.http = fallback
    comm = ApplicationCommunicator(app, dict(scope, path="/base/apix"))
    assert (await comm.receive_output())["status"] == 299
    await comm.wait()
    one.value = AsgiContribution({"/": mounted, "/api": mounted, "/api/deep": mounted})
    root_app = _Application()
    async def unexpected_fallback(scope, receive, send):
        raise AssertionError("root mount did not handle unmatched path")
    root_app.http = unexpected_fallback
    for path, expected_root in (
        ("/base/api/deep/item", "/base/api/deep"),
        ("/base/api/item", "/base/api"),
        ("/base/api", "/base/api"),
        ("/base/apix", "/base"),
        ("/base/other", "/base"),
        ("/base/", "/base"),
    ):
        original = dict(scope, path=path, raw_path=path.encode(), query_string=b"a=1&x=%2F")
        comm = ApplicationCommunicator(root_app, original)
        assert (await comm.receive_output())["status"] == 204
        await comm.receive_output()
        await comm.wait()
        assert seen.pop() == dict(original, root_path=expected_root)
        assert original["root_path"] == "/base"
    comm = ApplicationCommunicator(app, {"type": "lifespan"})
    await comm.send_input({"type": "lifespan.startup"})
    assert (await comm.receive_output())["type"] == "lifespan.startup.complete"
    await comm.send_input({"type": "lifespan.shutdown"})
    assert (await comm.receive_output())["type"] == "lifespan.shutdown.complete"
    await comm.wait()
    assert seen == ["enter first", "enter second", "exit second", "exit first"]
    seen.clear()
    @asynccontextmanager
    async def broken():
        raise RuntimeError("broken startup")
        yield
    app.lifespans = [first, broken]
    comm = ApplicationCommunicator(app, {"type": "lifespan"})
    await comm.send_input({"type": "lifespan.startup"})
    assert (await comm.receive_output())["type"] == "lifespan.startup.failed"
    await comm.wait()
    assert seen == ["enter first", "exit first"]
    @asynccontextmanager
    async def broken_exit():
        yield
        raise RuntimeError("broken shutdown")
    app.lifespans = [broken_exit]
    comm = ApplicationCommunicator(app, {"type": "lifespan"})
    await comm.send_input({"type": "lifespan.startup"})
    await comm.receive_output()
    await comm.send_input({"type": "lifespan.shutdown"})
    assert (await comm.receive_output())["type"] == "lifespan.shutdown.failed"
    await comm.wait()
    seen.clear()
    app.lifespans = [first]
    comm = ApplicationCommunicator(app, {"type": "lifespan"})
    await comm.send_input({"type": "lifespan.startup"})
    await comm.receive_output()
    comm.stop()
    await asyncio.sleep(0)
    assert seen == ["enter first", "exit first"]
    comm = ApplicationCommunicator(app, {"type": "websocket"})
    try: await comm.wait()
    except ValueError: pass
    else: raise AssertionError("unsupported scope accepted")
asyncio.run(main())
two.value = AsgiContribution({"/api": mounted})
try: _Application()
except ImproperlyConfigured as error: assert "one" in str(error) and "two" in str(error)
else: raise AssertionError("duplicate mount accepted")
two.value = AsgiContribution(protocols={"http": mounted})
try: _Application()
except ImproperlyConfigured as error: assert "Django HTTP fallback" in str(error) and "two" in str(error)
else: raise AssertionError("reserved protocol accepted")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_graphql_and_mcp_contribute_through_server_asgi():
    script = '''
import asyncio
import sys
from contextlib import asynccontextmanager
from importlib import import_module
from types import ModuleType
from django.conf import settings
settings.configure(INSTALLED_APPS=[], ANGEE_WEB_STATEMENT_TIMEOUT=0)
import django
django.setup()
from asgiref.testing import ApplicationCommunicator
from django.apps import apps
from angee.server.asgi import _Application
from angee.graphql.apps import GraphQLConfig
from angee.mcp.apps import MCPConfig
from angee.mcp import asgi as mcp_asgi
seen = []
async def websocket(scope, receive, send):
    seen.append(('websocket', scope['type']))
class MCP:
    async def __call__(self, scope, receive, send):
        seen.append(('mcp', scope['root_path']))
        await send({'type': 'http.response.start', 'status': 204, 'headers': []})
        await send({'type': 'http.response.body', 'body': b''})
    @asynccontextmanager
    async def lifespan(self, app):
        assert app is self
        seen.append('start mcp')
        try: yield
        finally: seen.append('stop mcp')
mcp = MCP()
graphql_asgi = ModuleType('angee.graphql.asgi')
graphql_asgi.websocket_application = lambda: websocket
sys.modules[graphql_asgi.__name__] = graphql_asgi
mcp_asgi.has_tools = lambda: True
mcp_asgi.mcp_app = lambda: mcp
configs = [
    GraphQLConfig('angee.graphql', import_module('angee.graphql')),
    MCPConfig('angee.mcp', import_module('angee.mcp')),
]
apps.get_app_configs = lambda: configs
app = _Application()
assert app.protocols['websocket'] is websocket
assert app.mounts['/mcp'] is mcp
assert len(app.lifespans) == 1
async def main():
    communicator = ApplicationCommunicator(app, {'type': 'websocket', 'path': '/graphql'})
    await communicator.wait()
    communicator = ApplicationCommunicator(app, {'type': 'http', 'path': '/mcp', 'root_path': ''})
    assert (await communicator.receive_output())['status'] == 204
    await communicator.receive_output()
    await communicator.wait()
    communicator = ApplicationCommunicator(app, {'type': 'lifespan'})
    await communicator.send_input({'type': 'lifespan.startup'})
    assert (await communicator.receive_output())['type'] == 'lifespan.startup.complete'
    await communicator.send_input({'type': 'lifespan.shutdown'})
    assert (await communicator.receive_output())['type'] == 'lifespan.shutdown.complete'
    await communicator.wait()
asyncio.run(main())
assert seen == [('websocket', 'websocket'), ('mcp', '/mcp'), 'start mcp', 'stop mcp']
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("prefix", ["", "mcp", "/mcp/", 1])
def test_asgi_invalid_mount_prefix_has_configuration_diagnostic(prefix):
    script = f"""
from types import ModuleType
from django.conf import settings
settings.configure(INSTALLED_APPS=[], ANGEE_WEB_STATEMENT_TIMEOUT=0)
import django
django.setup()
from django.apps import apps, AppConfig
from django.core.exceptions import ImproperlyConfigured
from angee.server.asgi import AsgiContribution, _Application
class Config(AppConfig):
    name = "mount_owner"
    path = "/tmp"
    def compose_asgi(self):
        return AsgiContribution(http_mounts={{{prefix!r}: object()}})
config = Config("mount_owner", ModuleType("mount_owner"))
apps.get_app_configs = lambda: [config]
try:
    _Application()
except ImproperlyConfigured as error:
    assert str(error) == "mount_owner: invalid HTTP mount " + repr({prefix!r}), str(error)
else:
    raise AssertionError("noncanonical mount accepted")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
