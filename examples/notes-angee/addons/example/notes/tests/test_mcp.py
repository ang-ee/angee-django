"""End-to-end MCP server: a StreamableHTTP call lists/creates/reads notes.

Drives the mounted FastMCP StreamableHTTP app the way the agent's HTTP client does
— a JSON-RPC POST carrying an ``Authorization`` bearer. FastMCP authenticates the
bearer to a REBAC actor (``401`` without one) and the tools run scoped to it, so
``alice`` sees her own demo notes and a note she creates round-trips back. A test
catalogue verifier maps the test bearer to alice's user subject; the whole round
trip runs in one event loop, matching the single serving loop in production.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TransactionTestCase, override_settings
from rebac import SubjectRef, system_context, to_subject_ref

from angee.mcp.server import MOUNT_PATH, mcp_app, mcp_server

Note = apps.get_model("notes", "Note")
User = get_user_model()

_BEARER = "test-mcp-alice"


def _verify_bearer(bearer: str) -> SubjectRef | None:
    """Test catalogue verifier (``ANGEE_MCP_ACTOR_VERIFIER``): bearer → alice.

    Identity resolution is a framework boundary, so the user lookup runs under
    ``system_context`` — the same elevated lookup the real catalogue verifier uses
    to match a credential.
    """

    if bearer != _BEARER:
        return None
    with system_context(reason="test-mcp-auth"):
        user = User.objects.filter(username="alice").first()
    return to_subject_ref(user) if user is not None else None


@override_settings(ANGEE_MCP_ACTOR_VERIFIER=f"{__name__}._verify_bearer")
class NotesMCPServerTests(TransactionTestCase):
    """The MCP server serves the notes tools over authenticated StreamableHTTP."""

    def setUp(self) -> None:
        """Rebuild the cached server per test, sync REBAC, and load the demo data."""

        mcp_server.cache_clear()
        mcp_app.cache_clear()
        call_command("rebac", "sync", verbosity=0)
        call_command("resources", "load", include_demo=True, allow_non_dev=True, verbosity=0)
        with system_context(reason="test-setup"):
            self.alice = User.objects.get(username="alice")
            self.owned = {str(note.sqid) for note in Note.objects.filter(created_by=self.alice)}

    def test_round_trip_scoped_to_the_authenticated_actor(self) -> None:
        """``tools/list`` advertises the tools; calls run scoped to the bearer's actor."""

        self.assertTrue(self.owned)
        asyncio.run(self._scenario())

    def test_missing_bearer_is_unauthorized(self) -> None:
        """A request without a bearer is rejected by FastMCP before any tool runs."""

        asyncio.run(self._unauthorized())

    async def _scenario(self) -> None:
        """Run the StreamableHTTP round trip with the app's lifespan entered."""

        app = mcp_app()
        async with app.router.lifespan_context(app):
            tools = {tool["name"] for tool in (await self._rpc("tools/list", {}))["tools"]}
            self.assertEqual(tools, {"list_notes", "read_note", "create_note", "update_note", "delete_note"})

            # list_notes is scoped to the actor (no admin fallback): every row is
            # one alice owns, and the page is non-empty.
            listed = await self._tool("list_notes", {"limit": 50})
            listed_sqids = {row["sqid"] for row in listed}
            self.assertTrue(listed_sqids)
            self.assertTrue(listed_sqids.issubset(self.owned))

            # A note alice creates round-trips back through read_note in full.
            created = await self._tool(
                "create_note", {"title": "From MCP", "body": "one two three", "tags": ["a", "b"]}
            )
            self.assertEqual(created["title"], "From MCP")
            self.assertEqual(created["word_count"], 3)

            read = await self._tool("read_note", {"sqid": created["sqid"]})
            self.assertEqual(read["sqid"], created["sqid"])
            self.assertEqual(read["body"], "one two three")
            self.assertEqual(read["tags"], ["a", "b"])

            # update_note exercises the flatten + sqid→GlobalID write path: change the
            # title/tags on the note alice owns, then confirm the change persisted.
            updated = await self._tool(
                "update_note", {"sqid": created["sqid"], "title": "Renamed via MCP", "tags": ["x"]}
            )
            self.assertEqual(updated["sqid"], created["sqid"])
            self.assertEqual(updated["title"], "Renamed via MCP")
            self.assertEqual(updated["tags"], ["x"])

            reread = await self._tool("read_note", {"sqid": created["sqid"]})
            self.assertEqual(reread["title"], "Renamed via MCP")
            self.assertEqual(reread["tags"], ["x"])
            self.assertEqual(reread["body"], "one two three")

            # delete_note exercises the fixed-arg (confirm=true) + DeletePreview path; the
            # note is then gone, so a follow-up read returns a tool error.
            deleted = await self._tool("delete_note", {"sqid": created["sqid"]})
            self.assertGreaterEqual(deleted["total_deleted_count"], 1)
            gone = await self._rpc("tools/call", {"name": "read_note", "arguments": {"sqid": created["sqid"]}})
            self.assertTrue(gone.get("isError"), gone)

    async def _unauthorized(self) -> None:
        """A bearer-less request is rejected with ``401`` before any tool runs."""

        app = mcp_app()
        async with app.router.lifespan_context(app):
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).encode()
            status, _payload = await self._drive(body, bearer=None)
            self.assertEqual(status, 401)

    async def _tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Call one tool and return its structured result.

        FastMCP wraps a non-object return (a list, scalar) under ``result`` in the
        structured content; an object return is the content itself.
        """

        result = await self._rpc("tools/call", {"name": name, "arguments": arguments})
        self.assertFalse(result.get("isError"), result)
        structured = result["structuredContent"]
        return structured["result"] if list(structured) == ["result"] else structured

    async def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Issue one authenticated StreamableHTTP JSON-RPC call and return its ``result``."""

        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        status, payload = await self._drive(body, bearer=_BEARER)
        self.assertEqual(status, 200, payload)
        envelope = json.loads(payload)
        self.assertNotIn("error", envelope, envelope)
        return envelope["result"]

    async def _drive(self, body: bytes, *, bearer: str | None) -> tuple[int, bytes]:
        """Send one StreamableHTTP request to the mounted app and capture the response."""

        captured: dict[str, Any] = {"status": 0, "body": bytearray()}

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                captured["status"] = message["status"]
            elif message["type"] == "http.response.body":
                captured["body"].extend(message.get("body", b""))

        headers = [
            (b"host", b"localhost"),
            (b"content-type", b"application/json"),
            (b"accept", b"application/json, text/event-stream"),
        ]
        if bearer is not None:
            headers.append((b"authorization", f"Bearer {bearer}".encode()))
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": MOUNT_PATH,
            "raw_path": MOUNT_PATH.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "server": ("localhost", 80),
            "client": ("127.0.0.1", 0),
        }
        await mcp_app()(scope, receive, send)
        return captured["status"], bytes(captured["body"])
