"""Real SDK, cookie-authenticated Channels sockets and persisted worker turns."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from datetime import datetime
from importlib import import_module
from typing import Any

import pytest
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from rebac import (
    MissingActorError,
    RelationshipTuple,
    actor_context,
    current_actor,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

from angee import asgi
from angee.agents.acp_server import SessionAgent
from angee.agents.asgi import websocket_urlpatterns
from angee.agents.models import AgentSessionManager, TurnStatus
from angee.agents.protocol import ACP_PATH, TURN_FAILED_STOP_REASON, TURN_FAILURE_CODE
from angee.agents.runners import TurnOutcome
from angee.agents.tasks import run_session
from angee.agents.testing.drivers import FakeRunner
from angee.agents.testing.drivers import runner as runner  # noqa: F401 - shared provider fixture
from angee.agents.testing.drivers import update_chunk as _chunk
from angee.agents.testing.models import Agent, AgentSession, AgentTurn, InferenceModel
from angee.base.errors import RecordAccessSubjectRefused
from angee.graphql.publishing import connect_change_broadcast_receiver, connect_publishers, disconnect_publishers
from tests.conftest import create_platform_admin
from tests.test_agents import _provider


@pytest.fixture
def chat(composed_tables: None, capture_tasks: list[Any], monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    owner = get_user_model().objects.create_user(username="acp-owner")
    with system_context(reason="test ACP agent"):
        agent = Agent.objects.create(name="Assistant", owner=owner, runtime_class="pydantic", runtime_status="running")
    monkeypatch.setattr(settings, "ALLOWED_HOSTS", ["*"])
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "CSRF_TRUSTED_ORIGINS", [])
    connect_change_broadcast_receiver()
    connect_publishers(AgentTurn)
    try:
        with actor_context(owner):
            yield agent, owner, asgi.websocket_application(websocket_urlpatterns), capture_tasks
    finally:
        disconnect_publishers(AgentTurn)


def _cookie(user: Any) -> bytes:
    client = Client()
    with system_context(reason="test cookie login"):
        client.force_login(get_user_model().objects.get(pk=user.pk), backend="angee.iam.auth.ModelBackend")
    return f"{settings.SESSION_COOKIE_NAME}={client.cookies[settings.SESSION_COOKIE_NAME].value}".encode()


class Peer:
    """Keep interleaved replies and updates while driving a real communicator."""

    def __init__(self, application: Any, agent: Any, cookie: bytes, version: int) -> None:
        self.socket = WebsocketCommunicator(
            application,
            f"{ACP_PATH}{agent.sqid}/",
            headers=[
                (b"host", b"testserver"),
                (b"origin", b"http://testserver"),
                (b"cookie", cookie),
            ],
        )
        self.version = version
        self.next_id = 0
        self.updates: list[dict[str, Any]] = []
        self.notifications: list[dict[str, Any]] = []
        self.responses: dict[int, dict[str, Any]] = {}

    async def connect(self) -> dict[str, Any]:
        assert await self.socket.connect(timeout=5) == (True, None)
        params = {"protocolVersion": self.version}
        params.update(
            {"info": {"name": "test", "version": "1"}, "capabilities": {}}
            if self.version == 2
            else {
                "clientInfo": {"name": "test", "version": "1"},
                "clientCapabilities": {},
            }
        )
        return await self.request("initialize", params)

    async def send(self, method: str, params: dict[str, Any], *, binary: bool = False) -> int:
        self.next_id += 1
        payload = json.dumps({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params}) + "\n"
        if binary:
            await self.socket.send_to(bytes_data=payload.encode())
        else:
            await self.socket.send_to(text_data=payload)
        return self.next_id

    async def receive(self) -> None:
        payload = json.loads(await self.socket.receive_from(timeout=5))
        if payload.get("method") == "session/update":
            self.updates.append(payload["params"]["update"])
            self.notifications.append(payload["params"])
        else:
            self.responses[payload["id"]] = payload

    async def response(self, request_id: int) -> dict[str, Any]:
        while request_id not in self.responses:
            await self.receive()
        return self.responses.pop(request_id)

    async def request(self, method: str, params: dict[str, Any], *, binary: bool = False) -> dict[str, Any]:
        payload = await self.response(await self.send(method, params, binary=binary))
        assert "error" not in payload, payload
        return payload["result"]

    async def new(self, **kwargs: Any) -> str:
        return (await self.request("session/new", {"cwd": "/ignored", "mcpServers": [], **kwargs}))["sessionId"]

    async def prompt(self, session_id: str, text: str) -> int:
        return await self.send("session/prompt", {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]})

    async def resume(self, session_id: str, *, replay: bool = True) -> None:
        params: dict[str, Any] = {"sessionId": session_id, "cwd": "/ignored", "mcpServers": []}
        if self.version == 2 and replay:
            params["replayFrom"] = {"type": "start"}
        await self.request("session/resume" if self.version == 2 else "session/load", params)

    async def state(self, state: str, stop: str | None = None) -> dict[str, Any]:
        def found() -> dict[str, Any] | None:
            return next(
                (
                    u
                    for u in reversed(self.updates)
                    if u.get("state") == state and (stop is None or u.get("stopReason") == stop)
                ),
                None,
            )

        while found() is None:
            await self.receive()
        return found()  # type: ignore[return-value]

    async def cancel(self, session_id: str) -> None:
        await self.socket.send_json_to(
            {"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": session_id}}
        )


async def _row(session_id: str, owner: Any) -> Any:
    return await database_sync_to_async(lambda: AgentSession.objects.with_actor(owner).get(sqid=session_id))()


def _worker(session: Any) -> asyncio.Task[None]:
    return asyncio.create_task(database_sync_to_async(run_session.run, thread_sensitive=False)(session.pk))


@pytest.mark.parametrize("version", [1, 2])
def test_initialize_new_prompt_stream_and_queued_turns(chat: Any, runner: FakeRunner, version: int) -> None:
    agent, owner, application, tasks = chat
    cookie = _cookie(owner)

    async def scenario() -> None:
        peer = Peer(application, agent, cookie, version)
        init = await peer.connect()
        assert init["protocolVersion"] == version
        if version == 1:
            assert init["agentCapabilities"]["loadSession"]
            assert init["agentCapabilities"]["sessionCapabilities"] == {"list": {}}
        else:
            assert init["capabilities"] == {"session": {}}
        session_id = await peer.new(
            _meta={"angee": {"context": {"kind": "list", "type": "agents/agent"}}},
            mcpServers=[{"name": "ignored", "command": "/never-executed", "args": [], "env": []}],
        )
        session = await _row(session_id, owner)
        assert session.context == {"kind": "list", "type": "agents/agent"}
        first = await peer.prompt(session_id, "First")
        if version == 2:
            first_id = (await peer.response(first))["result"]["messageId"]
        second = await peer.prompt(session_id, "Second")
        if version == 2:
            second_id = (await peer.response(second))["result"]["messageId"]
            assert first_id != second_id
        await _wait_turn(session, count=2)
        await _worker(session)
        if version == 1:
            assert (await peer.response(first))["result"] == {"stopReason": "end_turn"}
            assert second not in peer.responses
        else:
            await peer.state("running")
        await _worker(session)
        if version == 1:
            assert (await peer.response(second))["result"] == {"stopReason": "end_turn"}
            assert not any(u["sessionUpdate"] == "state_update" for u in peer.updates)
        else:
            await peer.state("idle", "end_turn")
            assert len([u for u in peer.updates if u.get("state") == "running"]) == 2
            users = [u for u in peer.updates if u["sessionUpdate"] == "user_message"]
            assert [u["messageId"] for u in users] == [first_id, second_id]
        assert [u["content"]["text"] for u in peer.updates if u["sessionUpdate"] == "agent_message_chunk"] == [
            "Reply",
            "Reply",
        ]
        assert runner.prompts == ["First", "Second"]
        assert len(tasks) >= 2
        await peer.socket.disconnect()

    asyncio.run(scenario())


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("approval", [False, True])
def test_failed_turn_and_failure_replay_keep_error_out_of_history(
    chat: Any,
    runner: FakeRunner,
    version: int,
    approval: bool,
) -> None:
    agent, owner, application, _ = chat
    cookie = _cookie(owner)
    completed = runner.outcome
    if approval:
        runner.outcome = TurnOutcome(kind="needs_approval", replay_state=[{"must_not_enter_history": True}])
    else:
        runner.error = RuntimeError("Private provider diagnostics")
    readable_error = "Tool approvals are not available yet." if approval else "Agent runtime failed."

    async def scenario() -> None:
        peer = Peer(application, agent, cookie, version)
        await peer.connect()
        session_id = await peer.new()
        request_id = await peer.prompt(session_id, "Fail")
        if version == 2:
            assert "messageId" in (await peer.response(request_id))["result"]
        session = await _row(session_id, owner)
        # v1 waits on the stored turn, so ensure insertion before executing.
        await _wait_turn(session)
        await _worker(session)
        if version == 1:
            assert (await peer.response(request_id))["error"] == {
                "code": -32603,
                "message": readable_error,
                "data": {"code": TURN_FAILURE_CODE},
            }
        else:
            idle = await peer.state("idle", TURN_FAILED_STOP_REASON)
            failure = next(u for u in peer.updates if u["sessionUpdate"] == "agent_message")
            assert failure["content"] == [{"type": "text", "text": readable_error}]
            assert "_meta" not in idle and "_meta" not in failure
        await peer.socket.disconnect()
        replay = Peer(application, agent, cookie, version)
        await replay.connect()
        await replay.resume(session_id)
        if version == 2:
            assert next(u for u in replay.updates if u["sessionUpdate"] == "agent_message") == failure
            assert "_meta" not in await replay.state("idle", TURN_FAILED_STOP_REASON)
        else:
            assert not any(u["sessionUpdate"] == "state_update" for u in replay.updates)
        assert "Private provider diagnostics" not in json.dumps(replay.updates)
        runner.error = None
        runner.outcome = completed
        retry = await replay.prompt(session_id, "Next")
        if version == 2:
            await replay.response(retry)
        await _wait_turn(session, count=2)
        await _worker(session)
        if version == 1:
            assert (await replay.response(retry))["result"]["stopReason"] == "end_turn"
        else:
            await replay.state("idle", "end_turn")
        assert runner.history == [[], []]
        await replay.socket.disconnect()

    asyncio.run(scenario())


async def _wait_turn(session: Any, *, count: int = 1) -> None:
    async def inserted() -> None:
        while await database_sync_to_async(lambda: session.turns.with_actor(session.actor()).count())() != count:
            await asyncio.sleep(0.02)

    await asyncio.wait_for(inserted(), timeout=5)


@pytest.mark.parametrize("version", [1, 2])
def test_resume_pending_turn_then_follow_its_start_and_cancel(chat: Any, version: int) -> None:
    agent, owner, application, _ = chat
    cookie = _cookie(owner)

    async def scenario() -> None:
        first, second = Peer(application, agent, cookie, version), Peer(application, agent, cookie, version)
        await first.connect()
        session_id = await first.new()
        request_id = await first.prompt(session_id, "Still queued")
        if version == 2:
            await first.response(request_id)
        session = await _row(session_id, owner)
        await _wait_turn(session)
        await second.connect()
        await second.resume(session_id)
        assert any(
            u["sessionUpdate"] == ("user_message" if version == 2 else "user_message_chunk") for u in second.updates
        )
        if version == 2:
            assert second.updates[-1] == {"sessionUpdate": "state_update", "state": "running"}
        await database_sync_to_async(session.claim_turn)()
        if version == 2:
            await first.state("running")
            await second.state("running")
        await second.cancel(session_id)
        if version == 2:
            await first.state("idle", "cancelled")
            await second.state("idle", "cancelled")
        else:
            assert (await first.response(request_id))["result"] == {"stopReason": "cancelled"}
        await first.socket.disconnect()
        await second.socket.disconnect()

    asyncio.run(scenario())


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("cancel", [False, True])
def test_disconnect_resume_running_turn_and_other_tab_cancel(
    chat: Any,
    runner: FakeRunner,
    monkeypatch: pytest.MonkeyPatch,
    version: int,
    cancel: bool,
) -> None:
    agent, owner, application, _ = chat
    cookie = _cookie(owner)
    release = threading.Event()
    monkeypatch.setattr("angee.agents.sessions.SESSION_UPDATE_FLUSH_SECONDS", 0)

    def stream(session: Any, turn: Any, emit: Any) -> None:
        emit(_chunk("So far"))
        assert release.wait(10), "Test did not release the fake provider"
        emit(_chunk(" and end"))

    runner.during_turn = stream

    async def scenario() -> None:
        first = Peer(application, agent, cookie, version)
        await first.connect()
        session_id = await first.new()
        prompt_id = await first.prompt(session_id, "Keep going")
        if version == 2:
            await first.response(prompt_id)
        session = await _row(session_id, owner)
        await _wait_turn(session)
        worker = _worker(session)
        try:
            while not any(
                u.get("content", {}).get("text") == "So far"
                for u in first.updates
                if isinstance(u.get("content"), dict)
            ):
                await first.receive()
            if version == 2:
                await first.state("running")
            await first.socket.disconnect()
            second = Peer(application, agent, cookie, version)
            await second.connect()
            await second.resume(session_id)
            assert any(
                u["sessionUpdate"] == ("user_message" if version == 2 else "user_message_chunk") for u in second.updates
            )
            assert any(
                u.get("content", {}).get("text") == "So far"
                for u in second.updates
                if isinstance(u.get("content"), dict)
            )
            if version == 2:
                assert second.updates[-1]["state"] == "running"
            third = Peer(application, agent, cookie, version)
            await third.connect()
            await third.resume(session_id, replay=False)
            if version == 2:
                assert any(
                    u.get("sessionUpdate") == "agent_message" and u["content"][0]["text"] == "So far"
                    for u in third.updates
                )
            if cancel:
                await third.cancel(session_id)
                if version == 2:
                    await third.state("idle", "cancelled")
                    await second.state("idle", "cancelled")
                else:

                    async def canceled() -> None:
                        while True:
                            turn = await database_sync_to_async(lambda: session.turns.first())()
                            if turn.status == TurnStatus.CANCELED:
                                return
                            await asyncio.sleep(0.02)

                    await asyncio.wait_for(canceled(), timeout=5)
            release.set()
            await worker
            if version == 2 and not cancel:
                await second.state("idle", "end_turn")
                await third.state("idle", "end_turn")
            elif not cancel:
                while not any(
                    u.get("content", {}).get("text") == " and end"
                    for u in second.updates
                    if isinstance(u.get("content"), dict)
                ):
                    await second.receive()
            assert runner.prompts == ["Keep going"]
            if not cancel:
                assert [
                    u["content"]["text"] for u in second.updates if u["sessionUpdate"] == "agent_message_chunk"
                ] == ["So far", " and end"]
                if version == 2:
                    assert (
                        len({u["messageId"] for u in second.updates if u["sessionUpdate"] == "agent_message_chunk"})
                        == 1
                    )
            elif version == 2:
                await second.request("session/list", {})
                assert [
                    u["content"]["text"] for u in second.updates if u["sessionUpdate"] == "agent_message_chunk"
                ] == ["So far"]
            await second.socket.disconnect()
            await third.socket.disconnect()
            completed = Peer(application, agent, cookie, version)
            await completed.connect()
            await completed.resume(session_id)
            if version == 2:
                assert completed.updates[-1]["stopReason"] == ("cancelled" if cancel else "end_turn")
                if cancel:
                    assert completed.updates[0] == {"sessionUpdate": "state_update", "state": "running"}
                else:
                    assert not any(u.get("state") == "running" for u in completed.updates)
                assert [
                    u["content"]["text"] for u in completed.updates if u["sessionUpdate"] == "agent_message_chunk"
                ] == ["So far", " and end"]
            else:
                assert not any(u["sessionUpdate"] == "state_update" for u in completed.updates)
            await completed.socket.disconnect()
        finally:
            release.set()
            await worker

    asyncio.run(scenario())


@pytest.mark.parametrize("version", [1, 2])
def test_binary_and_text_frames_and_sdk_validation(chat: Any, version: int) -> None:
    agent, owner, application, _ = chat
    cookie = _cookie(owner)

    async def scenario() -> None:
        peer = Peer(application, agent, cookie, version)
        await peer.connect()
        binary = await peer.request("session/new", {"cwd": "/", "mcpServers": []}, binary=True)
        text = await peer.new()
        assert binary["sessionId"] != text
        malformed = await peer.response(await peer.send("session/prompt", {"sessionId": text, "prompt": "bad"}))
        assert malformed["error"]["code"] == -32602
        empty = await peer.response(await peer.prompt(text, " "))
        assert empty["error"]["code"] == -32602
        assert "message is required" in empty["error"]["message"]
        # Multiple records per frame and a last record without a newline.
        payload = [{"jsonrpc": "2.0", "id": i, "method": "session/list", "params": {}} for i in (101, 102)]
        await peer.socket.send_to(bytes_data="\n".join(json.dumps(p) for p in payload).encode())
        assert len((await peer.response(101))["result"]["sessions"]) == 2
        assert len((await peer.response(102))["result"]["sessions"]) == 2
        await peer.socket.disconnect()

    asyncio.run(scenario())


@pytest.mark.parametrize("version", [1, 2])
def test_connection_actor_survives_sdk_tasks_and_sync_verbs_and_hides_other_sessions(
    chat: Any,
    monkeypatch: pytest.MonkeyPatch,
    version: int,
) -> None:
    agent, owner, application, _ = chat
    other = create_platform_admin("acp-other")
    cookie, other_cookie = _cookie(owner), _cookie(other)
    with actor_context(owner):
        private = AgentSession.objects.start(agent, owner=owner, context={})
        private.post("Private")
    actors: list[Any] = []
    start = AgentSessionManager.start
    post = AgentSession.post
    prompt = SessionAgent.prompt

    def start_spy(manager: Any, *args: Any, **kwargs: Any) -> Any:
        assert current_actor() == to_subject_ref(kwargs["actor"])
        actors.append(current_actor())
        return start(manager, *args, **kwargs)

    def post_spy(session: Any, *args: Any, **kwargs: Any) -> Any:
        assert current_actor() == to_subject_ref(kwargs["actor"])
        actors.append(current_actor())
        return post(session, *args, **kwargs)

    async def prompt_spy(adapter: Any, *args: Any, **kwargs: Any) -> Any:
        assert current_actor() == to_subject_ref(adapter.user)
        return await prompt(adapter, *args, **kwargs)

    monkeypatch.setattr(AgentSessionManager, "start", start_spy)
    monkeypatch.setattr(AgentSession, "post", post_spy)
    monkeypatch.setattr(SessionAgent, "prompt", prompt_spy)

    async def scenario() -> None:
        peer = Peer(application, agent, other_cookie, version)
        await peer.connect()
        own = await peer.new()
        assert own != str(private.sqid)
        listed = await peer.request("session/list", {})
        assert [s["sessionId"] for s in listed["sessions"]] == [own]
        # Change the caller's ambient context after handshake: the open socket
        # must retain its cookie identity, including in the SDK's child tasks.
        with actor_context(owner):
            params = {
                "sessionId": str(private.sqid),
                "prompt": [{"type": "text", "text": "Forbidden"}],
                "_meta": {"actor": str(to_subject_ref(owner))},
            }
            error = await peer.response(await peer.send("session/prompt", params))
            assert error["error"]["code"] == -32002
            method = "session/resume" if version == 2 else "session/load"
            error = await peer.response(
                await peer.send(method, {"sessionId": str(private.sqid), "cwd": "/", "mcpServers": []})
            )
            assert error["error"]["code"] == -32002
            if version == 2:
                error = await peer.response(await peer.send("session/close", {"sessionId": str(private.sqid)}))
                assert error["error"]["code"] == -32002
            await peer.cancel(str(private.sqid))
            await peer.request("session/list", {})
            own_prompt = await peer.prompt(own, "My message")
            if version == 2:
                await peer.response(own_prompt)
            own_session = await _row(own, other)
            await _wait_turn(own_session)
        await peer.socket.disconnect()
        owner_peer = Peer(application, agent, cookie, version)
        await owner_peer.connect()
        listed = await owner_peer.request("session/list", {})
        assert [s["sessionId"] for s in listed["sessions"]] == [str(private.sqid)]
        await owner_peer.socket.disconnect()

    asyncio.run(scenario())
    assert actors == [to_subject_ref(other), to_subject_ref(other)]
    private.refresh_from_db()
    assert private.status == "idle" and private.turns.count() == 1


@pytest.mark.parametrize("version", [1, 2])
def test_open_socket_rechecks_call_on_new_and_post(chat: Any, version: int) -> None:
    agent, owner, application, _ = chat
    cookie = _cookie(owner)
    other = get_user_model().objects.create_user(username="acp-new-owner")

    async def scenario() -> None:
        peer = Peer(application, agent, cookie, version)
        await peer.connect()
        session_id = await peer.new()

        def transfer() -> None:
            with system_context(reason="test revoke ACP call"):
                changed = Agent.objects.get(pk=agent.pk)
                changed.owner = other
                changed.save(update_fields=["owner"])

        await database_sync_to_async(transfer)()
        for method, params in (
            ("session/new", {"cwd": "/", "mcpServers": []}),
            ("session/prompt", {"sessionId": session_id, "prompt": [{"type": "text", "text": "Denied"}]}),
        ):
            error = await peer.response(await peer.send(method, params))
            assert error["error"]["code"] == -32000
        session = await _row(session_id, owner)
        assert await database_sync_to_async(lambda: session.turns.count())() == 0
        await peer.socket.disconnect()

    asyncio.run(scenario())


@pytest.mark.parametrize("origin", ["http://untrusted.example", "null", ""])
def test_cross_origin_handshake_refused(chat: Any, origin: str) -> None:
    agent, owner, application, _ = chat
    cookie = _cookie(owner)

    async def scenario() -> None:
        socket = WebsocketCommunicator(
            application,
            f"{ACP_PATH}{agent.sqid}/",
            headers=[
                (b"cookie", cookie),
                (b"origin", origin.encode()),
            ],
        )
        assert (await socket.connect())[0] is False
        await socket.disconnect()

    asyncio.run(scenario())


@pytest.mark.parametrize("unavailable", ["no-call", "editor", "anonymous", "stopped", "container"])
def test_handshake_requires_call_and_available_in_process_chat(chat: Any, unavailable: str) -> None:
    agent, owner, application, _ = chat
    if unavailable in ("no-call", "editor"):
        owner = get_user_model().objects.create_user(username="acp-stranger")
        if unavailable == "editor":
            with system_context(reason="test ACP editor without call"):
                write_relationships(
                    [
                        RelationshipTuple(
                            resource=to_object_ref(agent), relation="editor", subject=to_subject_ref(owner)
                        ),
                    ]
                )
    if unavailable in ("stopped", "container"):
        with system_context(reason="test ACP availability"):
            Agent.objects.filter(pk=agent.pk).update(
                **(
                    {"runtime_status": "stopped"}
                    if unavailable == "stopped"
                    else {"runtime_class": "claude_code", "service": "svc"}
                )
            )
    cookie = _cookie(owner) if unavailable != "anonymous" else b""

    async def scenario() -> None:
        peer = Peer(application, agent, cookie, 2)
        assert await peer.socket.connect() == (False, 4403)
        await peer.socket.disconnect()

    asyncio.run(scenario())


@pytest.mark.parametrize("version", [1, 2])
def test_cancel_close_and_live_updates_reach_every_attached_connection(
    chat: Any,
    monkeypatch: pytest.MonkeyPatch,
    version: int,
) -> None:
    agent, owner, application, _ = chat
    cookie = _cookie(owner)
    actors: list[Any] = []

    def spy(operation: Any) -> Any:
        def dispatch(session: Any, *args: Any, **kwargs: Any) -> Any:
            assert current_actor() == to_subject_ref(owner) == to_subject_ref(kwargs["actor"])
            actors.append(current_actor())
            return operation(session, *args, **kwargs)

        return dispatch

    monkeypatch.setattr(AgentSession, "post", spy(AgentSession.post))
    monkeypatch.setattr(AgentSession, "cancel_turn", spy(AgentSession.cancel_turn))
    monkeypatch.setattr(AgentSession, "close", spy(AgentSession.close))

    async def scenario() -> None:
        first, second = Peer(application, agent, cookie, version), Peer(application, agent, cookie, version)
        await first.connect()
        await second.connect()
        session_id = await first.new()
        await second.resume(session_id, replay=False)
        request_id = await first.prompt(session_id, "Question")
        if version == 2:
            await first.response(request_id)
        session = await _row(session_id, owner)
        await _wait_turn(session)
        turn = await database_sync_to_async(session.claim_turn)()

        def append() -> None:
            with actor_context(agent.principal_subject()):
                turn.append_updates([_chunk("Shared answer")])

        await database_sync_to_async(append)()
        for peer in (first, second):
            while not any(
                u.get("content", {}).get("text") == "Shared answer"
                for u in peer.updates
                if isinstance(u.get("content"), dict)
            ):
                await peer.receive()
        assert [u for u in first.updates if u["sessionUpdate"] == "agent_message_chunk"] == [
            u for u in second.updates if u["sessionUpdate"] == "agent_message_chunk"
        ]
        await second.cancel(session_id)
        if version == 1:
            assert (await first.response(request_id))["result"] == {"stopReason": "cancelled"}
        else:
            await first.state("idle", "cancelled")
            await second.state("idle", "cancelled")
            assert await first.request("session/close", {"sessionId": session_id}) == {}
            error = await first.response(await first.prompt(session_id, "Closed"))
            assert error["error"]["code"] == -32602 and "closed" in error["error"]["message"]
        await first.socket.disconnect()
        await second.socket.disconnect()

    asyncio.run(scenario())
    assert actors == [to_subject_ref(owner)] * (2 if version == 1 else 4)


@pytest.mark.parametrize("version", [1, 2])
def test_model_config_and_list_are_agent_scoped(chat: Any, version: int) -> None:
    agent, owner, application, _ = chat
    provider = _provider("acp-model")
    with system_context(reason="test ACP selected model"):
        model = InferenceModel.objects.create(provider=provider, name="selected-model")
        Agent.objects.filter(pk=agent.pk).update(model=model)
        other_agent = Agent.objects.create(
            name="Other", owner=owner, runtime_class="pydantic", runtime_status="running"
        )
    with actor_context(owner):
        other_session = AgentSession.objects.start(other_agent, owner=owner, context={})
    cookie = _cookie(owner)

    async def scenario() -> None:
        peer = Peer(application, agent, cookie, version)
        await peer.connect()
        old, new = await peer.new(), await peer.new()
        listed = await peer.request("session/list", {})
        assert [s["sessionId"] for s in listed["sessions"]] == [new, old]
        params = {"sessionId": new, "configId": "model", "value": "selected-model"}
        selected = await peer.request("session/set_config_option", params)
        assert selected["configOptions"][0]["currentValue"] == "selected-model"
        for config_id, value in (("model", "other-model"), ("mode", "selected-model")):
            error = await peer.response(
                await peer.send(
                    "session/set_config_option",
                    {
                        **params,
                        "configId": config_id,
                        "value": value,
                    },
                )
            )
            assert error["error"]["code"] == -32602
        error = await peer.response(await peer.prompt(str(other_session.sqid), "Wrong agent"))
        assert error["error"]["code"] == -32002
        if version == 1:
            for method in ("session/resume", "session/close"):
                error = await peer.response(await peer.send(method, {"sessionId": new, "cwd": "/", "mcpServers": []}))
                assert error["error"]["code"] == -32601
        await peer.socket.disconnect()

    asyncio.run(scenario())


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("context", [None, [], "text", 42, False])
def test_invalid_context_is_rejected_before_start(chat: Any, version: int, context: Any) -> None:
    agent, owner, application, _ = chat
    cookie = _cookie(owner)

    async def scenario() -> None:
        peer = Peer(application, agent, cookie, version)
        await peer.connect()
        error = await peer.response(
            await peer.send(
                "session/new",
                {
                    "cwd": "/",
                    "mcpServers": [],
                    "_meta": {"angee": {"context": context}},
                },
            )
        )
        assert error["error"] == {"code": -32602, "message": "Session context must be an object.", "data": None}
        assert (await peer.request("session/list", {}))["sessions"] == []
        await peer.socket.disconnect()

    asyncio.run(scenario())


@pytest.mark.parametrize("version", [1, 2])
def test_unavailable_agent_is_refused_before_prompt_insertion(chat: Any, version: int) -> None:
    agent, owner, application, _ = chat
    cookie = _cookie(owner)

    async def scenario() -> None:
        peer = Peer(application, agent, cookie, version)
        await peer.connect()
        session_id = await peer.new()

        def stop_agent() -> None:
            with system_context(reason="test ACP stopped runtime"):
                Agent.objects.filter(pk=agent.pk).update(runtime_status="stopped")

        await database_sync_to_async(stop_agent)()
        error = await peer.response(await peer.prompt(session_id, "Too late"))
        assert error["error"]["code"] == -32602
        session = await _row(session_id, owner)
        assert await database_sync_to_async(lambda: session.turns.count())() == 0
        await peer.socket.disconnect()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("host", "origin", "accepted"),
    [
        ("localhost:5173", "http://localhost:5173", True),
        ("localhost:5173", "https://trusted.example", True),
        ("localhost:5173", "http://localhost:5174", False),
        ("app.example", "http://sibling.app.example", False),
        ("app.example", None, False),
    ],
)
def test_wildcard_hosts_preserve_acp_origin_trust(
    chat: Any,
    settings: Any,
    host: str,
    origin: str | None,
    accepted: bool,
) -> None:
    agent, owner, application, _ = chat
    cookie = _cookie(owner)
    settings.CSRF_TRUSTED_ORIGINS = ["https://trusted.example"]

    async def scenario() -> None:
        headers = [(b"host", host.encode()), (b"cookie", cookie)]
        if origin is not None:
            headers.append((b"origin", origin.encode()))
        socket = WebsocketCommunicator(application, f"{ACP_PATH}{agent.sqid}/", headers=headers)
        assert (await socket.connect(timeout=5))[0] is accepted
        await socket.disconnect()

    asyncio.run(scenario())


@pytest.mark.parametrize("version", [1, 2])
def test_resource_links_are_prompt_text(chat: Any, runner: FakeRunner, version: int) -> None:
    agent, owner, application, _ = chat
    cookie = _cookie(owner)

    async def scenario() -> None:
        peer = Peer(application, agent, cookie, version)
        await peer.connect()
        sid = await peer.new()
        request = await peer.send(
            "session/prompt",
            {
                "sessionId": sid,
                "prompt": [
                    {"type": "text", "text": "Read this"},
                    {"type": "resource_link", "name": "Document", "uri": "https://resource.example/document"},
                ],
            },
        )
        if version == 2:
            await peer.response(request)
            assert peer.updates[0] == {"sessionUpdate": "state_update", "state": "running"}
        session = await _row(sid, owner)
        await _wait_turn(session)
        await _worker(session)
        if version == 1:
            assert (await peer.response(request))["result"]["stopReason"] == "end_turn"
        else:
            await peer.state("idle", "end_turn")
        assert runner.prompts == ["Read this\nDocument: https://resource.example/document"]
        await peer.socket.disconnect()

    asyncio.run(scenario())


def test_v2_thought_and_tool_projection_and_snapshot(chat: Any, runner: FakeRunner) -> None:
    agent, owner, application, _ = chat
    cookie = _cookie(owner)

    def updates(session: Any, turn: Any, emit: Any) -> None:
        emit(_chunk("Thinking", thought=True))
        emit(
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "lookup",
                "title": "Lookup",
                "kind": "read",
                "status": "pending",
            }
        )
        emit(
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "lookup",
                "status": "completed",
                "content": [{"type": "content", "content": {"type": "text", "text": "Found"}}],
            }
        )
        emit(_chunk("Answer"))

    runner.during_turn = updates

    async def scenario() -> None:
        peer = Peer(application, agent, cookie, 2)
        await peer.connect()
        sid = await peer.new()
        response = (await peer.response(await peer.prompt(sid, "Tools")))["result"]
        session = await _row(sid, owner)
        turn = await database_sync_to_async(lambda: session.turns.first())()
        assert response["messageId"] == f"{turn.sqid}:user"
        await _worker(session)
        await peer.state("idle", "end_turn")
        thought = next(u for u in peer.updates if u["sessionUpdate"] == "agent_thought_chunk")
        assert thought["messageId"] == f"{turn.sqid}:thought" and thought["content"]["text"] == "Thinking"
        tools = [u for u in peer.updates if u["sessionUpdate"] == "tool_call_update"]
        assert [u["status"] for u in tools] == ["pending", "completed"]
        assert tools[1]["content"][0]["content"]["text"] == "Found"
        await peer.socket.disconnect()
        replay = Peer(application, agent, cookie, 2)
        await replay.connect()
        await replay.resume(sid)
        assert [u for u in replay.updates if u["sessionUpdate"] != "state_update"] == [
            u for u in peer.updates if u["sessionUpdate"] != "state_update"
        ]
        assert [u for u in replay.updates if u["sessionUpdate"] == "state_update"] == [
            {"sessionUpdate": "state_update", "state": "idle", "stopReason": "end_turn"}
        ]
        await replay.socket.disconnect()

    asyncio.run(scenario())


def test_v1_stop_cancels_own_pending_waiters(chat: Any, runner: FakeRunner) -> None:
    agent, owner, application, _ = chat
    cookie = _cookie(owner)

    async def scenario() -> None:
        peer = Peer(application, agent, cookie, 1)
        await peer.connect()
        sid = await peer.new()
        requests = [await peer.prompt(sid, text) for text in ("One", "Two")]
        session = await _row(sid, owner)
        await _wait_turn(session, count=2)
        await peer.cancel(sid)
        for request in requests:
            assert (await peer.response(request))["result"] == {"stopReason": "cancelled"}
        assert await database_sync_to_async(lambda: list(session.turns.values_list("status", flat=True)))() == [
            TurnStatus.CANCELED,
            TurnStatus.CANCELED,
        ]
        await _worker(session)
        assert runner.prompts == []
        await peer.socket.disconnect()

    asyncio.run(scenario())


def test_deleting_session_fails_v1_prompt_waiter(chat: Any) -> None:
    agent, owner, application, _ = chat
    cookie = _cookie(owner)

    async def scenario() -> None:
        peer = Peer(application, agent, cookie, 1)
        await peer.connect()
        sid = await peer.new()
        request = await peer.prompt(sid, "Pending")
        session = await _row(sid, owner)
        await _wait_turn(session)

        def delete_transcript() -> None:
            with system_context(reason="test administrative transcript deletion"):
                AgentSession.system_queryset().get(pk=session.pk).delete()

        await database_sync_to_async(delete_transcript)()
        assert (await peer.response(request))["error"]["code"] == -32002
        await peer.socket.disconnect()

    asyncio.run(scenario())


@pytest.mark.parametrize("version", [1, 2])
def test_one_connection_follows_several_sessions(chat: Any, runner: FakeRunner, version: int) -> None:
    agent, owner, application, _ = chat
    cookie = _cookie(owner)

    async def scenario() -> None:
        peer = Peer(application, agent, cookie, version)
        await peer.connect()
        sessions = [await peer.new(), await peer.new()]
        for index, sid in enumerate(sessions):
            request = await peer.prompt(sid, f"Question {index}")
            if version == 2:
                await peer.response(request)
            session = await _row(sid, owner)
            await _wait_turn(session)
            await _worker(session)
            if version == 1:
                await peer.response(request)
            else:
                while not any(
                    n["sessionId"] == sid and n["update"].get("stopReason") == "end_turn" for n in peer.notifications
                ):
                    await peer.receive()
        assert [
            (n["sessionId"], n["update"]["content"]["text"])
            for n in peer.notifications
            if n["update"]["sessionUpdate"] == "agent_message_chunk"
        ] == [(sid, "Reply") for sid in sessions]
        assert runner.prompts == ["Question 0", "Question 1"]
        await peer.socket.disconnect()

    asyncio.run(scenario())


@pytest.mark.parametrize("version", [1, 2])
def test_session_list_pages_are_bounded_and_scoped(chat: Any, monkeypatch: pytest.MonkeyPatch, version: int) -> None:
    agent, owner, application, _ = chat
    monkeypatch.setattr("angee.agents.acp_server.SESSION_PAGE_SIZE", 2)
    cookie = _cookie(owner)

    async def scenario() -> None:
        peer = Peer(application, agent, cookie, version)
        await peer.connect()
        ids = [await peer.new() for _ in range(5)]
        session = await _row(ids[2], owner)
        await database_sync_to_async(session.post)("Named conversation", actor=owner)
        pages = []
        cursor = None
        while True:
            page = await peer.request("session/list", {"cursor": cursor} if cursor else {})
            assert len(page["sessions"]) <= 2
            assert all(set(row) == {"sessionId", "cwd", "title", "updatedAt"} for row in page["sessions"])
            pages.extend(page["sessions"])
            cursor = page.get("nextCursor")
            if cursor is None:
                break
        assert [row["sessionId"] for row in pages] == list(reversed(ids))
        assert pages[2]["title"] == "Named conversation"
        assert datetime.fromisoformat(pages[2]["updatedAt"]) == (await _row(ids[2], owner)).updated_at
        error = await peer.response(await peer.send("session/list", {"cursor": "invalid"}))
        assert error["error"]["code"] == -32602
        # The framework signs each cut with the actor, model and effective query.
        first = await peer.request("session/list", {})
        other = await database_sync_to_async(create_platform_admin)("acp-page-other")
        other_cookie = await database_sync_to_async(_cookie)(other)
        second = Peer(application, agent, other_cookie, version)
        await second.connect()
        error = await second.response(await second.send("session/list", {"cursor": first["nextCursor"]}))
        assert error["error"]["code"] == -32602
        await second.socket.disconnect()
        await peer.socket.disconnect()

    asyncio.run(scenario())


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("invalidated", ["logout", "inactive"])
def test_requests_revalidate_cookie_and_active_user(chat: Any, version: int, invalidated: str) -> None:
    agent, owner, application, _ = chat
    cookie = _cookie(owner)

    async def scenario() -> None:
        peer = Peer(application, agent, cookie, version)
        await peer.connect()
        await peer.new()

        def invalidate() -> None:
            with system_context(reason="test ACP authentication expiry"):
                if invalidated == "inactive":
                    get_user_model().objects.filter(pk=owner.pk).update(is_active=False)
                else:
                    import_module(settings.SESSION_ENGINE).SessionStore(
                        session_key=cookie.decode().split("=", 1)[1]
                    ).delete()

        await database_sync_to_async(invalidate)()
        await peer.send("session/new", {"cwd": "/", "mcpServers": []})
        assert await peer.socket.receive_output(timeout=5) == {"type": "websocket.close", "code": 4401}
        assert await database_sync_to_async(lambda: AgentSession.system_queryset().filter(agent=agent).count())() == 1
        await peer.socket.disconnect()

    asyncio.run(scenario())


@pytest.mark.parametrize("version", [1, 2])
def test_follower_failure_logs_closes_and_fails_waiters(
    chat: Any, monkeypatch: pytest.MonkeyPatch, caplog: Any, version: int
) -> None:
    agent, owner, application, _ = chat
    cookie = _cookie(owner)
    adapters: list[Any] = []
    init = SessionAgent.__init__

    def remember(adapter: Any, *args: Any, **kwargs: Any) -> None:
        init(adapter, *args, **kwargs)
        adapters.append(adapter)

    monkeypatch.setattr(SessionAgent, "__init__", remember)

    async def scenario() -> None:
        peer = Peer(application, agent, cookie, version)
        await peer.connect()
        sid = await peer.new()
        request = await peer.prompt(sid, "Pending")
        if version == 2:
            await peer.response(request)
        session = await _row(sid, owner)
        await _wait_turn(session)
        # Fail the next row read at the persistence boundary, after a waiter exists.
        original = SessionAgent._db

        async def broken(adapter: Any, operation: Any, *args: Any, **kwargs: Any) -> Any:
            if operation.__name__ == "changed_turn":
                raise RuntimeError(f"Private {operation.__name__} diagnostics")
            return await original(adapter, operation, *args, **kwargs)

        monkeypatch.setattr(SessionAgent, "_db", broken)
        waiters = list(adapters[0].waiters.values()) if version == 1 else []
        await database_sync_to_async(session.claim_turn)()
        while True:
            event = await peer.socket.receive_output(timeout=5)
            if event["type"] == "websocket.close":
                assert event["code"] == 1011
                break
        for _, waiter in waiters:
            assert waiter.done() and waiter.exception().code == -32603
        assert "ACP change follower failed" in caplog.text
        assert "Private changed_turn diagnostics" not in caplog.text
        await peer.socket.disconnect()

    asyncio.run(scenario())


@pytest.mark.parametrize("turn_count", [0, 8, 32])
@pytest.mark.parametrize("connection_count", [1, 3])
def test_follower_query_budget_is_independent_of_history_and_unrelated_turns(
    chat: Any,
    monkeypatch: pytest.MonkeyPatch,
    turn_count: int,
    connection_count: int,
) -> None:
    agent, owner, application, _ = chat
    cookie = _cookie(owner)
    session = AgentSession.objects.start(agent, owner=owner, context={})
    other = AgentSession.objects.start(agent, owner=owner, context={})
    with system_context(reason="test transcript query budget"):
        AgentTurn.objects.bulk_create(
            [
                AgentTurn(
                    session=session,
                    index=index + 1,
                    prompt=f"Old {index}",
                    status=TurnStatus.COMPLETED,
                    updates=[_chunk("Retained")],
                )
                for index in range(turn_count)
            ]
        )
    active = session.post("Current")
    active = session.claim_turn()
    unrelated = other.post("Unrelated")
    unrelated = other.claim_turn()
    measured: list[tuple[int, int]] = []
    original = SessionAgent._db

    async def count_reads(adapter: Any, operation: Any, *args: Any, **kwargs: Any) -> Any:
        thread_sensitive = kwargs.pop("thread_sensitive")

        def counted() -> Any:
            with CaptureQueriesContext(connection) as queries:
                result = operation(*args, **kwargs)
            measured.append((len(queries), threading.get_ident()))
            return result

        return await original(adapter, counted, thread_sensitive=thread_sensitive)

    async def scenario() -> None:
        peers = [Peer(application, agent, cookie, 2) for _ in range(connection_count)]
        for peer in peers:
            await peer.connect()
            await peer.resume(str(session.sqid), replay=False)
        shared_thread = await database_sync_to_async(threading.get_ident)()
        monkeypatch.setattr(SessionAgent, "_db", count_reads)

        def append() -> None:
            with actor_context(agent.principal_subject()):
                unrelated.append_updates([_chunk("Ignore this")])
                active.append_updates([_chunk("Budget")])

        await database_sync_to_async(append)()
        for peer in peers:
            while not any(
                u.get("content", {}).get("text") == "Budget" for u in peer.updates if isinstance(u.get("content"), dict)
            ):
                await peer.receive()
        assert len(measured) == connection_count
        assert all(count <= 8 and thread != shared_thread for count, thread in measured), measured
        print(
            f"ACP follower SQL: history={turn_count}, connections={connection_count}, "
            f"queries={[count for count, _ in measured]}"
        )
        for peer in peers:
            await peer.socket.disconnect()

    asyncio.run(scenario())


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("kind", ["internal", "permission", "actor", "validation", "domain"])
def test_refusals_share_the_graphql_classifier_without_internal_details(
    chat: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: Any,
    version: int,
    kind: str,
) -> None:
    agent, owner, application, _ = chat
    cookie = _cookie(owner)
    errors = {
        "internal": RuntimeError("Private operation diagnostics"),
        "permission": PermissionDenied("Private permission diagnostics"),
        "actor": MissingActorError("Private actor diagnostics"),
        "validation": ValidationError("Authored input refusal."),
        "domain": RecordAccessSubjectRefused(),
    }
    expected = {
        "internal": (-32603, "An unexpected error occurred.", None),
        "permission": (-32000, "Permission denied.", {"code": "PERMISSION_DENIED"}),
        "actor": (-32000, "Authentication required.", {"code": "UNAUTHENTICATED"}),
        "validation": (-32602, "Authored input refusal.", {"code": "VALIDATION"}),
        "domain": (-32000, "RECORD_ACCESS_SUBJECT_REFUSED", {"code": "RECORD_ACCESS_SUBJECT_REFUSED"}),
    }

    def refused(*args: Any, **kwargs: Any) -> None:
        raise errors[kind]

    monkeypatch.setattr(AgentSessionManager, "start", refused)

    async def scenario() -> None:
        peer = Peer(application, agent, cookie, version)
        await peer.connect()
        response = await peer.response(await peer.send("session/new", {"cwd": "/", "mcpServers": []}))
        code, message, data = expected[kind]
        assert response["error"] == {"code": code, "message": message, "data": data}
        assert "Private" not in json.dumps(response)
        assert await database_sync_to_async(lambda: AgentSession.objects.count())() == 0
        await peer.socket.disconnect()

    asyncio.run(scenario())
    if kind == "internal":
        assert "Unexpected ACP operation error (RuntimeError)" in caplog.text
        assert "Private operation diagnostics" not in caplog.text


@pytest.mark.parametrize("replay", [False, True])
def test_resume_awaiting_approval_is_idle_without_a_stop_reason(chat: Any, replay: bool) -> None:
    agent, owner, application, _ = chat
    cookie = _cookie(owner)
    session = AgentSession.objects.start(agent, owner=owner, context={})
    session.post("Suspended")
    turn = session.claim_turn()
    with system_context(reason="test future approval state"):
        turn.mark_awaiting_approval()

    async def scenario() -> None:
        peer = Peer(application, agent, cookie, 2)
        await peer.connect()
        await peer.resume(str(session.sqid), replay=replay)
        assert [u for u in peer.updates if u["sessionUpdate"] == "state_update"] == [
            {"sessionUpdate": "state_update", "state": "idle"}
        ]
        await peer.socket.disconnect()

    asyncio.run(scenario())
