"""Cookie-authenticated Channels transport for the SDK's version router."""

from __future__ import annotations

import asyncio
import json
import logging
from importlib import import_module
from typing import Any

from acp.experimental import AgentProtocolRouter
from channels.auth import get_user
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.apps import apps
from django.conf import settings
from rebac import evaluator_scope
from rebac.graphql.strawberry import RebacChannelsConsumerMixin

from angee.agents.acp_server import SessionAgent, V1SessionAgent, V2SessionAgent
from angee.base.identity import instance_from_public_id
from angee.base.scoping import read_scoped_queryset
from angee.iam.permissions import is_authenticated

logger = logging.getLogger(__name__)


class WebsocketTransport:
    """Implement the SDK's public send/receive/close transport contract.

    Frames contain one or more NDJSON records, in text or binary, with an optional
    final newline. The SDK still owns JSON-RPC interpretation and validation.
    """

    def __init__(self, consumer: _AgentACPConsumer) -> None:
        self.consumer = consumer
        self.messages: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    def feed(self, data: bytes) -> None:
        for record in data.splitlines():
            if not record.strip():
                continue
            try:
                message = json.loads(record)
            except ValueError, UnicodeDecodeError:
                logger.warning("Invalid JSON on an ACP WebSocket.")
                continue
            if isinstance(message, dict):
                self.messages.put_nowait(message)

    async def receive(self) -> dict[str, Any] | None:
        message = await self.messages.get()
        if message is not None and not await self.consumer.validate_session():
            await self.consumer.close(code=4401)
            return None
        return message

    async def send(self, message: dict[str, Any]) -> None:
        await self.consumer.send(text_data=json.dumps(message, separators=(",", ":")) + "\n")

    async def close(self) -> None:
        self.messages.put_nowait(None)


class _AgentACPConsumer(AsyncWebsocketConsumer):
    """Host one SDK connection; disconnect never stops the worker's turn."""

    @database_sync_to_async
    def callable_agent(self) -> Any:
        with evaluator_scope():
            user = self.scope.get("user")
            if not is_authenticated(user) or not user.is_active:
                return None
            model = apps.get_model("agents", "Agent")
            agent = instance_from_public_id(
                model,
                self.scope["url_route"]["kwargs"]["agent"],
                queryset=read_scoped_queryset(model, user, action="call"),
            )
            return agent if agent is not None and agent.chat_blocker() is None else None

    async def validate_session(self) -> bool:
        # Refresh the store itself: Channels' connection-scoped session has cached
        # its contents, so calling get_user with it cannot detect logout elsewhere.
        session = import_module(settings.SESSION_ENGINE).SessionStore(session_key=self.session_key)
        with evaluator_scope():
            user = await get_user({**self.scope, "session": session})
        return is_authenticated(user) and user.is_active and user.pk == self.user_pk

    async def connect(self) -> None:
        agent = await self.callable_agent()
        if agent is None:
            await self.close(code=4403)
            return
        self.session_key = self.scope["session"].session_key
        self.user_pk = self.scope["user"].pk
        self.adapter: SessionAgent | None = None
        self.transport = WebsocketTransport(self)

        def build(connection: Any, adapter: type[SessionAgent]) -> Any:
            self.adapter = adapter(connection, self.scope["user"], agent, self.close)
            return self.adapter

        self.connection = AgentProtocolRouter(
            v1=lambda connection: build(connection, V1SessionAgent),
            v2=lambda connection: build(connection, V2SessionAgent),
        ).connect(self.transport)
        await self.accept()

    async def receive(self, text_data: str | None = None, bytes_data: bytes | None = None) -> None:
        self.transport.feed(text_data.encode("utf-8") if text_data is not None else bytes_data or b"")

    async def disconnect(self, code: int) -> None:
        if not hasattr(self, "connection"):
            return
        await self.connection.close()
        if self.adapter is not None:
            await self.adapter.disconnect()


class AgentACPConsumer(RebacChannelsConsumerMixin, _AgentACPConsumer):
    """Pin the cookie's REBAC actor before creating any SDK request tasks."""
