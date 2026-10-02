"""ACP mappings and delivery; the SDK owns requests, schemas and version routing.

A v1 prompt waits for its own retained turn. After a worker crash it deliberately
waits until Stop; there is no task recovery or consumer-side model execution.
Permission outcomes in slice 3 may carry _meta.angee.reason; metadata is not an actor.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import wraps
from importlib import metadata
from typing import Any

from acp import schema as v1
from acp.connection import StreamDirection, StreamEvent
from acp.exceptions import RequestError
from acp.experimental.v2 import schema as v2
from channels.db import database_sync_to_async
from django.apps import apps
from django.db import transaction
from rebac import evaluator_scope

from angee.agents.models import TurnStatus
from angee.agents.protocol import ANGEE_META_KEY, TURN_FAILED_STOP_REASON, TURN_FAILURE_CODE, message_id
from angee.base.errors import classify_error
from angee.base.identity import instance_from_public_id
from angee.base.pagination import InvalidKeysetCursor, KeysetOrder
from angee.base.scoping import read_scoped_queryset
from angee.graphql.subscriptions import subscribe

logger = logging.getLogger(__name__)
SESSION_PAGE_SIZE = 50
_CURSOR_SALT = "agents.acp.sessions"
STOP_REASONS = {
    str(TurnStatus.COMPLETED): "end_turn",
    str(TurnStatus.CANCELED): "cancelled",
    str(TurnStatus.FAILED): TURN_FAILED_STOP_REASON,
}


def rpc_operation(operation: Callable[..., Any]) -> Any:
    """Keep transport-independent refusals and internal details behind the boundary."""

    @wraps(operation)
    async def guarded(*args: Any, **kwargs: Any) -> Any:
        try:
            with evaluator_scope():
                return await operation(*args, **kwargs)
        except RequestError:
            raise
        except Exception as error:
            refusal = classify_error(error)
            if not refusal.expected:
                logger.error(
                    "Unexpected ACP operation error (%s).\n%s",
                    type(error).__name__,
                    "".join(traceback.format_tb(error.__traceback__)),
                )
                raise RequestError(-32603, refusal.message) from None
            code = -32602 if refusal.code in ("VALIDATION", "BAD_USER_INPUT") else -32000
            raise RequestError(code, refusal.message, {"code": refusal.code}) from None

    return guarded


@dataclass
class _Delivery:
    updates: int = 0
    prompt_sent: bool = False
    status: str = ""
    started: bool = False
    acknowledged: bool = True


@dataclass
class _Attachment:
    session: Any
    turns: dict[str, _Delivery] = field(default_factory=dict)
    pending: dict[str, Any] = field(default_factory=dict)
    state: tuple[str, str | None] | None = None

    def observe(self, turn: Any) -> None:
        turn_id = str(turn.sqid)
        delivery = self.turns.setdefault(turn_id, _Delivery())
        if delivery.status not in STOP_REASONS:
            self.pending[turn_id] = turn


class SessionAgent(ABC):
    """Shared mapping and per-connection delivery, without session policy."""

    schema: Any
    config_id_key: str

    def __init__(self, connection: Any, user: Any, agent: Any, close_socket: Callable[..., Any]) -> None:
        self.connection = connection
        self.user = user
        self.agent = agent
        self.close_socket = close_socket
        self.session_model = apps.get_model("agents", "AgentSession")
        self.turn_model = apps.get_model("agents", "AgentTurn")
        self.attachments: dict[str, _Attachment] = {}
        self.lock = asyncio.Lock()
        self.ready = asyncio.Event()
        self.follower: asyncio.Task[None] | None = None

    @staticmethod
    def _view_context(kwargs: dict[str, Any], *, label: str) -> dict[str, Any]:
        """Extract the optional view object from SDK-expanded Angee metadata."""

        envelope = kwargs.get(ANGEE_META_KEY, {})
        context = envelope.get("context", {}) if isinstance(envelope, dict) else None
        if not isinstance(context, dict):
            raise RequestError(-32602, f"{label} context must be an object.")
        return context

    @rpc_operation
    async def new_session(self, cwd: str, **kwargs: Any) -> Any:
        context = self._view_context(kwargs, label="Session")
        session = await self._db(
            self.session_model.objects.start,
            self.agent,
            owner=self.user,
            context=context,
            actor=self.user,
        )
        await self._attach(session, replay=False)
        return self.schema.NewSessionResponse.model_validate(
            {
                "sessionId": str(session.sqid),
                "configOptions": await self._db(self._config_options, session),
            }
        )

    def _sessions(self) -> Any:
        return read_scoped_queryset(self.session_model, self.user).filter(agent=self.agent, owner=self.user)

    def _session(self, session_id: str) -> Any:
        session = instance_from_public_id(self.session_model, session_id, queryset=self._sessions())
        if session is None:
            raise RequestError.resource_not_found(session_id)
        return session

    @rpc_operation
    async def list_sessions(self, cwd: str | None = None, cursor: str | None = None, **kwargs: Any) -> Any:
        def listing() -> dict[str, Any]:
            try:
                page = self._sessions().keyset_page(
                    order=KeysetOrder("created_at"),
                    cursor_scope=(self.agent.pk, self.user.pk),
                    cursor_salt=_CURSOR_SALT,
                    before_cursor=cursor,
                    limit=SESSION_PAGE_SIZE,
                )
            except InvalidKeysetCursor:
                raise RequestError(-32602, "Invalid session cursor.") from None
            result: dict[str, Any] = {
                "sessions": [
                    {
                        "sessionId": str(session.sqid),
                        "cwd": "/",
                        "title": session.title,
                        "updatedAt": session.updated_at.isoformat(),
                    }
                    for session in page.rows
                ],
            }
            if page.has_older:
                result["nextCursor"] = page.older_cursor
            return result

        return self.schema.ListSessionsResponse.model_validate(await self._db(listing, thread_sensitive=False))

    @rpc_operation
    async def load_session(self, session_id: str, cwd: str, **kwargs: Any) -> Any:
        return await self._resume(session_id, replay=True, response=v1.LoadSessionResponse)

    @rpc_operation
    async def resume_session(self, session_id: str, cwd: str, replay_from: Any = None, **kwargs: Any) -> Any:
        if replay_from is not None and replay_from.type != "start":
            raise RequestError.invalid_params({"details": "Only replayFrom start is supported."})
        return await self._resume(session_id, replay=replay_from is not None, response=v2.ResumeSessionResponse)

    async def _resume(self, session_id: str, *, replay: bool, response: Any) -> Any:
        session = await self._db(self._session, session_id)
        await self._attach(session, replay=replay, current_state=True)
        return response.model_validate({"configOptions": await self._db(self._config_options, session)})

    @rpc_operation
    async def prompt(self, session_id: str, prompt: list[Any], **kwargs: Any) -> Any:
        context = self._view_context(kwargs, label="Message")
        blocks: list[str] = []
        for block in prompt:
            if block.type == "text":
                blocks.append(block.text)
            elif block.type == "resource_link":
                blocks.append(f"{block.name}: {block.uri}")
            else:
                raise RequestError(-32602, "Only text and resource links are supported.")
        session = await self._db(self._session, session_id)
        await self._attach(session, replay=False)
        async with self.lock:
            turn = await self._db(session.post, "\n".join(blocks), context=context, actor=self.user)
            self.attachments[session_id].observe(turn)
            acknowledgment = await self._accepted(session_id, turn)
        return await self._acknowledge(turn, acknowledgment)

    @abstractmethod
    async def _accepted(self, session_id: str, turn: Any) -> Any:
        raise NotImplementedError

    @abstractmethod
    async def _acknowledge(self, turn: Any, acknowledgment: Any) -> Any:
        raise NotImplementedError

    @rpc_operation
    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        await self.cancel_session(session_id)

    @rpc_operation
    async def cancel_session(self, session_id: str, **kwargs: Any) -> None:
        session = await self._db(self._session, session_id)
        await self._db(session.cancel_active_turn, actor=self.user)

    @rpc_operation
    async def close_session(self, session_id: str, **kwargs: Any) -> Any:
        session = await self._db(self._session, session_id)
        await self._db(session.close, actor=self.user)
        return self.schema.CloseSessionResponse.model_validate({})

    def _config_options(self, session: Any) -> list[dict[str, Any]]:
        model = session.agent.service_model_handle()
        return (
            [
                {
                    "type": "select",
                    self.config_id_key: "model",
                    "name": "Model",
                    "category": "model",
                    "currentValue": model,
                    "options": [{"value": model, "name": model}],
                }
            ]
            if model
            else []
        )

    @rpc_operation
    async def set_config_option(self, session_id: str, config_id: str, value: Any, **kwargs: Any) -> Any:
        session = await self._db(self._session, session_id)
        options = await self._db(self._config_options, session)
        if config_id != "model" or not options or value != options[0]["currentValue"]:
            raise RequestError(-32602, "The model is chosen on the agent, not per session.")
        return self.schema.SetSessionConfigOptionResponse.model_validate({"configOptions": options})

    async def _db(
        self,
        operation: Callable[..., Any],
        *args: Any,
        thread_sensitive: bool = True,
        **kwargs: Any,
    ) -> Any:
        def scoped() -> Any:
            with evaluator_scope():
                return operation(*args, **kwargs)

        return await database_sync_to_async(scoped, thread_sensitive=thread_sensitive)()

    async def _attach(self, session: Any, *, replay: bool, current_state: bool = False) -> None:
        if self.follower is None:
            self.follower = asyncio.create_task(self._follow(), name="angee-acp-changes")
        ready = asyncio.create_task(self.ready.wait())
        try:
            await asyncio.wait((ready, self.follower), return_when=asyncio.FIRST_COMPLETED)
            if self.follower.done():
                await self.follower
        finally:
            ready.cancel()
            await asyncio.gather(ready, return_exceptions=True)
        async with self.lock:
            session_id = str(session.sqid)
            if session_id in self.attachments and not replay and not current_state:
                return
            self.attachments.setdefault(session_id, _Attachment(session))
            turns = await self._db(
                lambda: list(session.turns.with_actor(self.user).order_by("index")),
                thread_sensitive=False,
            )
            await self._restore(session_id, turns, replay=replay)
            if current_state:
                await self._report_state(session_id)

    async def _restore(self, session_id: str, turns: list[Any], *, replay: bool) -> None:
        attachment = self.attachments[session_id]
        if replay:
            attachment.turns.clear()
            attachment.pending.clear()
            for turn in turns:
                await self._deliver(session_id, turn, replay=True)
        elif not attachment.turns:
            for turn in turns:
                attachment.turns[str(turn.sqid)] = _Delivery(len(turn.updates), True, turn.status)

    async def _follow(self) -> None:
        try:
            async for payload in subscribe(self.turn_model, ready=self.ready):
                related = {
                    record.id for record in payload.related_records if record.model == self.session_model._meta.label
                }
                session_ids = related.intersection(self.attachments)
                if not session_ids:
                    continue
                # Copy on the loop: database threads never iterate mutable attachments.
                attached = {self.attachments[sid].session.pk: sid for sid in session_ids}

                def changed_turn() -> Any:
                    return instance_from_public_id(
                        self.turn_model,
                        payload.id,
                        queryset=read_scoped_queryset(self.turn_model, self.user).filter(
                            session_id__in=tuple(attached)
                        ),
                    )

                async with self.lock:
                    turn = await self._db(changed_turn, thread_sensitive=False)
                    if turn is None:
                        if payload.action == "delete":
                            self._turn_deleted(payload.id)
                        continue
                    session_id = attached[turn.session_id]
                    self.attachments[session_id].observe(turn)
                    await self._deliver(session_id, turn)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(
                "ACP change follower failed (%s).\n%s",
                type(error).__name__,
                "".join(traceback.format_tb(error.__traceback__)),
            )
            self._fail_waiters(RequestError(-32603, "The session change feed failed."))
            await self.close_socket(code=1011)
            raise

    async def _deliver(self, session_id: str, turn: Any, *, replay: bool = False) -> None:
        turn_id = str(turn.sqid)
        delivery = self.attachments[session_id].turns.setdefault(turn_id, _Delivery())
        if not delivery.prompt_sent:
            await self._user_message(session_id, turn)
            delivery.prompt_sent = True
        for position, update in enumerate(self._project_updates(turn)):
            if position >= delivery.updates:
                await self._update(session_id, update)
        delivery.updates = len(turn.updates)
        if delivery.status != turn.status:
            await self._status_changed(session_id, turn, replay=replay)
        delivery.status = turn.status
        if turn.status in STOP_REASONS:
            self.attachments[session_id].pending.pop(turn_id, None)

    @abstractmethod
    async def _user_message(self, session_id: str, turn: Any) -> None:
        raise NotImplementedError

    @abstractmethod
    def _project_updates(self, turn: Any) -> Iterator[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def _update(self, session_id: str, update: dict[str, Any]) -> None:
        raise NotImplementedError

    async def _status_changed(self, session_id: str, turn: Any, *, replay: bool) -> None:
        pass

    @abstractmethod
    async def _report_state(self, session_id: str) -> None:
        raise NotImplementedError

    async def observe_stream(self, event: StreamEvent) -> None:
        """Receive SDK-owned wire events; v1 needs no post-response delivery."""

    def _turn_deleted(self, turn_id: str) -> None:
        pass

    def _fail_waiters(self, error: RequestError) -> None:
        pass

    async def disconnect(self) -> None:
        if self.follower is not None:
            self.follower.cancel()
            await asyncio.gather(self.follower, return_exceptions=True)


class V1SessionAgent(SessionAgent):
    """Native v1 prompt completion; router advertises load, not unstable resume/close."""

    schema = v1
    config_id_key = "id"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.waiters: dict[str, tuple[Any, asyncio.Future[Any]]] = {}

    @rpc_operation
    async def initialize(self, protocol_version: int, **kwargs: Any) -> Any:
        return v1.InitializeResponse.model_validate(
            {
                "protocolVersion": 1,
                "agentInfo": {"name": "angee", "version": metadata.version("django-angee")},
                "agentCapabilities": {"loadSession": True, "sessionCapabilities": {"list": {}}},
            }
        )

    async def _accepted(self, session_id: str, turn: Any) -> Any:
        turn_id = str(turn.sqid)
        self.attachments[session_id].turns[turn_id] = _Delivery(prompt_sent=True)
        waiter = asyncio.get_running_loop().create_future()
        self.waiters[turn_id] = turn, waiter
        return waiter

    async def _acknowledge(self, turn: Any, acknowledgment: Any) -> Any:
        try:
            settled = await acknowledgment
        finally:
            self.waiters.pop(str(turn.sqid), None)
        if settled.status == TurnStatus.FAILED:
            raise RequestError(-32603, settled.error, {"code": TURN_FAILURE_CODE})
        return v1.PromptResponse.model_validate({"stopReason": STOP_REASONS[settled.status]})

    @rpc_operation
    async def cancel_session(self, session_id: str, **kwargs: Any) -> None:
        session = await self._db(self._session, session_id)
        pending = tuple(turn.pk for turn, _ in self.waiters.values() if turn.session_id == session.pk)

        def cancel_pending() -> None:
            # Keep the verbs' on-commit dispatch behind the whole Stop operation:
            # canceling active work must not start this tab's queued waiter first.
            with transaction.atomic():
                for turn in session.turns.with_actor(self.user).filter(pk__in=pending, status=TurnStatus.PENDING):
                    session.cancel_turn(turn, actor=self.user)
                session.cancel_active_turn(actor=self.user)

        await self._db(cancel_pending)

    async def _user_message(self, session_id: str, turn: Any) -> None:
        await self._update(
            session_id, {"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": turn.prompt}}
        )

    def _project_updates(self, turn: Any) -> Iterator[dict[str, Any]]:
        return iter(turn.updates)

    async def _update(self, session_id: str, update: dict[str, Any]) -> None:
        note = v1.SessionNotification.model_validate({"sessionId": session_id, "update": update})
        await self.connection.session_update(session_id=session_id, update=note.update)

    async def _status_changed(self, session_id: str, turn: Any, *, replay: bool) -> None:
        waiter = self.waiters.get(str(turn.sqid))
        if turn.status in STOP_REASONS and waiter is not None and not waiter[1].done():
            waiter[1].set_result(turn)

    def _turn_deleted(self, turn_id: str) -> None:
        waiter = self.waiters.get(turn_id)
        if waiter is not None and not waiter[1].done():
            waiter[1].set_exception(RequestError.resource_not_found(turn_id))

    def _fail_waiters(self, error: RequestError) -> None:
        for _, waiter in self.waiters.values():
            if not waiter.done():
                waiter.set_exception(error)

    async def _report_state(self, session_id: str) -> None:
        """V1 exposes settlement through its prompt response, without session state."""


class V2SessionAgent(SessionAgent):
    """Bracket each foreground turn in session order, for live delivery and replay."""

    schema = v2
    config_id_key = "configId"

    @rpc_operation
    async def initialize(self, protocol_version: int, **kwargs: Any) -> Any:
        return v2.InitializeResponse.model_validate(
            {
                "protocolVersion": 2,
                "info": {"name": "angee", "version": metadata.version("django-angee")},
                "capabilities": {"session": {}},
            }
        )

    async def _accepted(self, session_id: str, turn: Any) -> Any:
        delivery = self.attachments[session_id].turns[str(turn.sqid)]
        delivery.acknowledged = False
        await self._user_message(session_id, turn)
        delivery.prompt_sent = True
        return v2.PromptResponse.model_validate({"messageId": message_id(turn.sqid, "user")})

    async def _acknowledge(self, turn: Any, acknowledgment: Any) -> Any:
        return acknowledgment

    async def observe_stream(self, event: StreamEvent) -> None:
        """Release accepted output only after the SDK sends its insertion response."""

        result = event.message.get("result")
        if event.direction != StreamDirection.OUTGOING or not isinstance(result, dict):
            return
        accepted_id = result.get("messageId")
        if accepted_id is None:
            return
        async with self.lock:
            for session_id, attachment in self.attachments.items():
                for turn_id, delivery in attachment.turns.items():
                    if not delivery.acknowledged and message_id(turn_id, "user") == accepted_id:
                        delivery.acknowledged = True
                        await self._drain(session_id)
                        return

    async def _restore(self, session_id: str, turns: list[Any], *, replay: bool) -> None:
        attachment = self.attachments[session_id]
        unacknowledged = {turn_id for turn_id, delivery in attachment.turns.items() if not delivery.acknowledged}
        if replay:
            attachment.turns.clear()
            attachment.pending.clear()
            attachment.state = None
        historical = not replay
        for turn in turns:
            turn_id = str(turn.sqid)
            historical = historical and turn.status in STOP_REASONS
            if historical and turn_id not in attachment.turns:
                attachment.turns[turn_id] = _Delivery(len(turn.updates), True, turn.status)
            attachment.observe(turn)
            if turn_id in unacknowledged:
                attachment.turns[turn_id].acknowledged = False
        await self._drain(session_id, snapshot=not replay)
        for turn in attachment.pending.values():
            delivery = attachment.turns[str(turn.sqid)]
            if not delivery.prompt_sent:
                await self._user_message(session_id, turn)
                delivery.prompt_sent = True
        if not replay and not attachment.pending and attachment.state is None and turns:
            await self._state(session_id, "idle", turns[-1])

    async def _deliver(self, session_id: str, turn: Any, *, replay: bool = False) -> None:
        attachment = self.attachments[session_id]
        delivery = attachment.turns[str(turn.sqid)]
        if not delivery.prompt_sent:
            await self._user_message(session_id, turn)
            delivery.prompt_sent = True
        await self._drain(session_id)

    async def _drain(self, session_id: str, *, snapshot: bool = False) -> None:
        """Settle a bracket before opening the next, even before its worker claim.

        Later turns may be echoed at insertion, but their output waits for every
        earlier bracket. Retain only unclosed rows; delivered cursors also fence
        off output appended after cancellation.
        """

        attachment = self.attachments[session_id]
        for turn in sorted(attachment.pending.values(), key=lambda row: row.index):
            turn_id = str(turn.sqid)
            delivery = attachment.turns[turn_id]
            if not delivery.acknowledged:
                break
            if not delivery.prompt_sent:
                await self._user_message(session_id, turn)
                delivery.prompt_sent = True
            opening = not delivery.started
            if opening:
                await self._state(session_id, "running", None)
                delivery.started = True
            projected = list(self._project_updates(turn))[delivery.updates :]
            for update in self._snapshot_updates(projected) if snapshot and opening else projected:
                await self._update(session_id, update)
            delivery.updates = len(turn.updates)
            delivery.status = turn.status
            if turn.status not in STOP_REASONS:
                break
            if turn.status == TurnStatus.FAILED:
                await self._update(
                    session_id,
                    {
                        "sessionUpdate": "agent_message",
                        "messageId": message_id(turn.sqid, "error"),
                        "content": [{"type": "text", "text": turn.error}],
                    },
                )
            await self._state(session_id, "idle", turn)
            attachment.pending.pop(turn_id)

    async def _user_message(self, session_id: str, turn: Any) -> None:
        await self._update(
            session_id,
            {
                "sessionUpdate": "user_message",
                "messageId": message_id(turn.sqid, "user"),
                "content": [{"type": "text", "text": turn.prompt}],
            },
        )

    def _project_updates(self, turn: Any) -> Iterator[dict[str, Any]]:
        """Identify contiguous text/thought segments by their first stored position.

        Stored v1-shaped chunks need no IDs or migration. Every projection uses
        the full retained prefix so batching, snapshots and replay agree.
        """

        previous = None
        segment = 0
        for position, update in enumerate(turn.updates):
            kind = update["sessionUpdate"]
            if kind != previous:
                segment = position
            if kind in ("agent_message_chunk", "agent_thought_chunk"):
                part = "agent" if kind == "agent_message_chunk" else "thought"
                yield {**update, "messageId": message_id(turn.sqid, f"{part}:{segment}")}
            elif kind in ("tool_call", "tool_call_update"):
                yield {**update, "sessionUpdate": "tool_call_update"}
            else:
                yield update
            previous = kind

    async def _update(self, session_id: str, update: dict[str, Any]) -> None:
        note = v2.UpdateSessionNotification.model_validate({"sessionId": session_id, "update": update})
        await self.connection.session_update(session_id=session_id, update=note.update)

    async def _report_state(self, session_id: str) -> None:
        attachment = self.attachments[session_id]
        # Replay already reported its newest bracket. Never settle a queued row
        # from a newest-turn snapshot or repeat a historical settlement.
        if attachment.state is None and not attachment.pending:
            await self._state(session_id, "idle", None)
        elif any(turn.status == TurnStatus.AWAITING_APPROVAL for turn in attachment.pending.values()):
            await self._state(session_id, "idle", None)

    async def _state(self, session_id: str, state: str, settled: Any) -> None:
        stop = STOP_REASONS[settled.status] if settled is not None else None
        await self._update(
            session_id,
            {
                "sessionUpdate": "state_update",
                "state": state,
                **({"stopReason": stop} if stop else {}),
            },
        )
        self.attachments[session_id].state = state, stop

    def _snapshot_updates(self, updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Upsert each projected part in its original arrival order."""

        parts: dict[tuple[str, str], dict[str, Any]] = {}
        for update in updates:
            kind = update["sessionUpdate"]
            if kind in ("agent_message_chunk", "agent_thought_chunk"):
                part = parts.setdefault(
                    (kind, update["messageId"]),
                    {
                        "sessionUpdate": kind.removesuffix("_chunk"),
                        "messageId": update["messageId"],
                        "content": [{"type": "text", "text": ""}],
                    },
                )
                part["content"][0]["text"] += update["content"]["text"]
            elif kind == "tool_call_update":
                parts.setdefault((kind, update["toolCallId"]), {}).update(update)
        return list(parts.values())
