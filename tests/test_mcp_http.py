"""Mounted MCP host/origin validation composes the native FastMCP guard."""

import asyncio
from typing import Any

import httpx
import pytest
from django.test import override_settings
from fastmcp.server.http import HostOriginGuardMiddleware

from angee.mcp import server


@pytest.mark.parametrize("host,origin,status", [
    ("untrusted.test", None, 421),
    ("api.example.test", "https://untrusted.test", 403),
    ("api.example.test", "https://console.example.test", 204),
    ("example.test", "http://example.test", 204),
])
def test_mcp_mount_composes_native_host_and_origin_guard(
    monkeypatch: pytest.MonkeyPatch, host: str, origin: str | None, status: int,
) -> None:
    """Mounted MCP scopes are protected before Django middleware could run."""

    async def accepted(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    class NativeServer:
        def http_app(self, **options: Any) -> Any:
            assert options.pop("host_origin_protection") is True
            return HostOriginGuardMiddleware(
                accepted, mode="strict", allowed_hosts=options["allowed_hosts"],
                allowed_origins=options["allowed_origins"],
            )

    monkeypatch.setattr(server, "mcp_server", NativeServer)
    server.mcp_app.cache_clear()
    try:
        with override_settings(ALLOWED_HOSTS=[".example.test"], CSRF_TRUSTED_ORIGINS=["https://console.example.test"]):
            async def request() -> int:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=server.mcp_app()), base_url="http://127.0.0.1",
                ) as client:
                    response = await client.post(
                        "/mcp", headers={"host": host, **({"origin": origin} if origin else {})},
                    )
                return response.status_code

            assert asyncio.run(request()) == status
    finally:
        server.mcp_app.cache_clear()
