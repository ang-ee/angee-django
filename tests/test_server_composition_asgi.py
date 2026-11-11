"""Native ASGI events exercise the server's routing and resource lifetime."""

import os
import subprocess
import sys

import pytest


def run(script):
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# Fake app configs expose their conventional asgi module without packaged distributions.
SETUP = """
from types import ModuleType
import angee.base.addons
angee.base.addons.optional_addon_module = (
    lambda config, name, index=None: getattr(config, "modules", {}).get(name)
)
from django.conf import settings
settings.configure(
    INSTALLED_APPS=[], ANGEE_WEB_STATEMENT_TIMEOUT=0,
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
)
import django
django.setup()
from django.apps import apps, AppConfig
class Contribution(AppConfig):
    path = "/tmp"
def contributor(name, mounts=(), lifespans=(), protocols=None):
    module = ModuleType(name + ".asgi")
    module.http_mounts = lambda: list(mounts)
    module.lifespans = lambda: list(lifespans)
    module.protocols = lambda: dict(protocols or {})
    config = Contribution(name, ModuleType(name))
    config.modules = {"asgi": module}
    return config
"""


def test_post_setup_url_import_guard():
    run("""
from django.conf import settings
settings.configure(INSTALLED_APPS=[], ANGEE_WEB_STATEMENT_TIMEOUT=0)
from django.core.exceptions import AppRegistryNotReady
try:
    __import__("angee.server.urls")
except AppRegistryNotReady:
    pass
else:
    raise AssertionError("post-setup seam imported before setup")
""")


def test_asgi_composition():
    run(SETUP + """
import asyncio, sys
from contextlib import asynccontextmanager
class Block:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith((
            "django_compose.build", "django_compose.declarations", "channels", "uvicorn"
        )):
            raise AssertionError(fullname)
sys.meta_path.insert(0, Block())
from angee.server.asgi import _Application
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
configs = [
    contributor("one", mounts=[("/api", mounted), ("/api/deep", mounted)], lifespans=[first]),
    contributor("two", lifespans=[second]),
]
apps.get_app_configs = lambda: configs
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
    configs[0] = contributor("one", mounts=[("/", mounted), ("/api", mounted), ("/api/deep", mounted)])
    root_app = _Application()
    async def unexpected_fallback(scope, receive, send):
        raise AssertionError("root mount did not handle unmatched path")
    root_app.http = unexpected_fallback
    for path, expected_root, served in (
        ("/base/api/deep/item", "/base/api/deep", "/base/api/deep/item"),
        ("/base/api/item", "/base/api", "/base/api/item"),
        ("/base/api", "/base/api", "/base/api/"),
        ("/base/apix", "/base", "/base/apix"),
        ("/base/other", "/base", "/base/other"),
        ("/base/", "/base", "/base/"),
    ):
        original = dict(scope, path=path, raw_path=path.encode(), query_string=b"a=1&x=%2F")
        comm = ApplicationCommunicator(root_app, original)
        assert (await comm.receive_output())["status"] == 204
        await comm.receive_output()
        await comm.wait()
        assert seen.pop() == dict(original, root_path=expected_root, path=served, raw_path=served.encode())
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
configs[1] = contributor("two", mounts=[("/api", mounted)])
try: _Application()
except ImproperlyConfigured as error: assert "one" in str(error) and "two" in str(error)
else: raise AssertionError("duplicate mount accepted")
configs[1] = contributor("two", protocols={"http": mounted})
try: _Application()
except ImproperlyConfigured as error: assert "Django HTTP fallback" in str(error) and "two" in str(error)
else: raise AssertionError("reserved protocol accepted")
""")


def test_server_application_loads_the_native_application_once_off_the_event_loop():
    """Construction may read the database, so the first ASGI call builds it off the loop, once."""

    run(SETUP + """
import asyncio
from asgiref.testing import ApplicationCommunicator
from django.db import connection
import angee.server
loads = []
async def mounted(scope, receive, send):
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})
def http_mounts():
    # On the event loop this raises SynchronousOnlyOperation.
    loads.append(connection.get_autocommit())
    return [("/probe", mounted)]
config = contributor("probe")
config.modules["asgi"].http_mounts = http_mounts
apps.get_app_configs = lambda: [config]
async def main():
    for _ in range(2):
        scope = {"type": "http", "path": "/probe", "root_path": ""}
        comm = ApplicationCommunicator(angee.server.application, scope)
        assert (await comm.receive_output())["status"] == 204
        await comm.receive_output()
        await comm.wait()
asyncio.run(main())
from angee.server.asgi import application as native
assert loads == [True], loads
assert angee.server._native_application is native
""")


def test_graphql_and_mcp_contribute_through_server_asgi():
    run('''
import asyncio
import sys
from contextlib import asynccontextmanager
from importlib import import_module
from importlib.machinery import ModuleSpec
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
# A module in sys.modules carries a spec, so the addon owner finds the submodule natively.
graphql_asgi.__spec__ = ModuleSpec(graphql_asgi.__name__, None)
graphql_asgi.protocols = lambda: {'websocket': websocket}
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
''')


@pytest.mark.parametrize("prefix", ["", "mcp", "/mcp/", 1])
def test_asgi_invalid_mount_prefix_has_configuration_diagnostic(prefix):
    run(SETUP + f"""
from django.core.exceptions import ImproperlyConfigured
from angee.server.asgi import _Application
config = contributor("mount_owner", mounts=[({prefix!r}, object())])
apps.get_app_configs = lambda: [config]
try:
    _Application()
except ImproperlyConfigured as error:
    assert str(error) == "mount_owner: invalid HTTP mount " + repr({prefix!r}), str(error)
else:
    raise AssertionError("noncanonical mount accepted")
""")


def test_asgi_protocol_declaration_is_optional_but_noncallables_are_invalid():
    run(SETUP + """
from angee.server.asgi import _Application
from django.core.exceptions import ImproperlyConfigured
config = contributor("optional")
module = config.modules["asgi"]
del module.protocols
apps.get_app_configs = lambda: [config]
assert _Application().protocols == {}
for invalid in (None, {}, "websocket"):
    module.protocols = invalid
    try:
        _Application()
    except ImproperlyConfigured as error:
        assert "protocols must be callable" in str(error)
    else:
        raise AssertionError("invalid protocol declaration accepted")
""")



def test_a_mount_serves_its_exact_prefix_as_the_application_root():
    """``/mcp`` and ``/mcp/`` reach a root-routed app without a redirect; deeper paths are unchanged."""

    run(SETUP + """
import asyncio
from asgiref.testing import ApplicationCommunicator
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Route
from angee.server.asgi import _Application
seen = []
async def endpoint(request):
    seen.append((request.scope["path"], request.scope["root_path"], request.scope["raw_path"]))
    return Response(status_code=200)
# Routed on its own root like FastMCP's http_app(path="/"); Starlette redirects an unmatched "".
inner = Starlette(routes=[Route("/", endpoint, methods=["POST"]), Route("/deeper", endpoint, methods=["POST"])])
apps.get_app_configs = lambda: [contributor("mcp", mounts=[("/mcp", inner)])]
app = _Application()
async def post(path):
    comm = ApplicationCommunicator(app, {
        "type": "http", "method": "POST", "path": path, "raw_path": path.encode(), "root_path": "",
        "query_string": b"", "headers": [(b"host", b"testserver")], "scheme": "http",
        "server": ("testserver", 80),
    })
    await comm.send_input({"type": "http.request", "body": b"", "more_body": False})
    status = (await comm.receive_output())["status"]
    await comm.receive_output()
    await comm.wait()
    return status
async def main():
    assert [await post(path) for path in ("/mcp", "/mcp/", "/mcp/deeper")] == [200, 200, 200]
    assert seen == [("/mcp/", "/mcp", b"/mcp/"), ("/mcp/", "/mcp", b"/mcp/"), ("/mcp/deeper", "/mcp", b"/mcp/deeper")]
asyncio.run(main())
""")
