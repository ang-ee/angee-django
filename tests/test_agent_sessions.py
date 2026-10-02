"""Agent-owned chat execution: one queued turn per task, with explicit Stop."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from typing import Any

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, current_actor, system_context
from rebac.models import PermissionAuditEvent

from angee.agents import provisioning
from angee.agents.models import AgentLifecycle, RuntimeStatus, SessionStatus, TurnStatus
from angee.agents.runners import SessionUpdateSink, TurnOutcome
from angee.agents.tasks import run_session
from angee.agents.testing.drivers import FakeRunner
from angee.agents.testing.drivers import runner as runner  # noqa: F401 - shared provider fixture
from angee.agents.testing.drivers import update_chunk as _chunk
from angee.agents.testing.models import Agent, AgentSession, AgentTurn
from angee.base.transitions import TransitionNotAllowed
from angee.graphql.publishing import connect_publishers, disconnect_publishers
from angee.workflows.testing.drivers import capture_tasks as capture_task_sends
from angee.workflows.testing.drivers import observe
from tests.conftest import create_platform_admin


@pytest.fixture
def session(composed_tables: None, capture_tasks: list[Any]) -> Iterator[AgentSession]:
    del composed_tables, capture_tasks
    owner = get_user_model().objects.create_user(username="session-owner")
    with system_context(reason="test agent session seed"):
        agent = Agent.objects.create(
            name="Session agent",
            owner=owner,
            runtime_class="pydantic",
            lifecycle=AgentLifecycle.READY,
            runtime_status=RuntimeStatus.RUNNING,
        )
    with actor_context(owner):
        yield AgentSession.objects.start(agent, owner=owner, context={"label": "Session context"})


def test_turns_drain_in_order_one_per_task(
    session: AgentSession,
    runner: FakeRunner,
    capture_tasks: list[Any],
) -> None:
    first = session.post("First question")
    second = session.post("Second question")
    session.refresh_from_db()
    assert session.title == "First question"
    assert session.context == {"label": "Session context"}
    assert (first.index, second.index) == (1, 2)
    capture_tasks.clear()

    run_session.run(session.pk)

    first.refresh_from_db()
    second.refresh_from_db()
    session.refresh_from_db()
    assert first.status == TurnStatus.COMPLETED
    assert first.text == "Reply"
    assert first.updates == [_chunk("Reply")]
    assert first.usage == {"requests": 1, "tokens": 3}
    assert second.status == TurnStatus.PENDING
    assert runner.prompts == ["First question"]
    assert session.replay_state == [{"reply": "retained"}]
    assert [name for name, _options in capture_tasks] == ["agents.run_session"]

    run_session.run(session.pk)

    second.refresh_from_db()
    session.refresh_from_db()
    assert second.status == TurnStatus.COMPLETED
    assert session.status == SessionStatus.IDLE
    assert session.usage == {"requests": 2, "tokens": 6}
    assert runner.prompts == ["First question", "Second question"]
    assert runner.history == [[], [{"reply": "retained"}]]


def test_post_retains_each_messages_context_without_inheriting_the_session(session: AgentSession) -> None:
    context = {"kind": "list", "type": "agents/agent", "params": {"search": "First view"}}
    first = session.post("First", context=context, actor=session.owner)
    second = session.post("Second")
    context["kind"] = "record"
    first.refresh_from_db()
    second.refresh_from_db()
    session.refresh_from_db()
    assert first.context == {"kind": "list", "type": "agents/agent", "params": {"search": "First view"}}
    assert second.context == {}
    assert first.created_by_id == second.created_by_id == session.owner_id
    assert session.context == {"label": "Session context"}
    assert (first.prompt, second.prompt) == ("First", "Second")
    assert session.title == "First"


def test_admin_post_retains_the_poster_instead_of_the_session_owner(session: AgentSession) -> None:
    """The post permission's admin arm admits a poster distinct from the owner."""

    admin = create_platform_admin("session-post-admin")
    turn = session.post("Posted by an admin", actor=admin)
    turn.refresh_from_db()
    assert admin.pk != session.owner_id
    assert turn.created_by_id == admin.pk


@pytest.mark.parametrize("context", [None, [], ["pair", "value"], "text", 42, False])
def test_post_refuses_non_object_context_before_insertion(
    session: AgentSession,
    capture_tasks: list[Any],
    context: Any,
) -> None:
    capture_tasks.clear()
    with pytest.raises(ValidationError) as refused:
        session.post("Refused", context=context)
    assert refused.value.message_dict == {"context": ["Message context must be an object."]}
    assert not session.turns.exists()
    session.refresh_from_db()
    assert session.title == ""
    assert capture_tasks == []


def test_post_during_execution_preserves_the_running_turn(session: AgentSession, runner: FakeRunner) -> None:
    first = session.post("First")
    posted: list[Any] = []

    def post_while_running(active: Any, turn: Any, emit: SessionUpdateSink) -> None:
        emit(_chunk("Partial"))
        with actor_context(session.owner):
            posted.append(session.post("Arrived while running"))
            run_session.run(session.pk)
            turn.refresh_from_db()
            assert turn.status == TurnStatus.RUNNING
            assert posted[0].status == TurnStatus.PENDING

    runner.during_turn = post_while_running
    run_session.run(session.pk)
    first.refresh_from_db()
    assert first.updates == [_chunk("Partial")]
    assert runner.prompts == ["First"]

    runner.during_turn = None
    run_session.run(session.pk)
    posted[0].refresh_from_db()
    assert posted[0].status == TurnStatus.COMPLETED
    assert runner.prompts == ["First", "Arrived while running"]


def test_running_turn_is_never_reclaimed(session: AgentSession, runner: FakeRunner) -> None:
    first = session.post("First")
    second = session.post("Second")
    claimed = session.claim_turn()
    assert claimed is not None and claimed.pk == first.pk
    assert claimed.append_updates([_chunk("Retain this")])

    assert session.claim_turn() is None
    run_session.run(session.pk)

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == TurnStatus.RUNNING
    assert first.updates == [_chunk("Retain this")]
    assert second.status == TurnStatus.PENDING
    assert runner.prompts == []


def test_stop_idles_active_turn_and_dispatches_pending(
    session: AgentSession,
    runner: FakeRunner,
    capture_tasks: list[Any],
) -> None:
    first = session.post("Stop me")
    pending = session.post("Next")
    session.claim_turn()
    capture_tasks.clear()

    session.cancel_turn(first)

    first.refresh_from_db()
    session.refresh_from_db()
    assert first.status == TurnStatus.CANCELED
    assert session.status == SessionStatus.IDLE
    assert len(capture_tasks) == 1 and capture_tasks[0][0] == "agents.run_session"
    session.settle_turn(
        first,
        TurnOutcome(kind="completed", text="Too late", replay_state=["discard"], usage={"tokens": 9}),
    )
    first.refresh_from_db()
    session.refresh_from_db()
    assert first.status == TurnStatus.CANCELED
    assert first.text == ""
    assert session.replay_state == [] and session.usage == {}

    run_session.run(session.pk)
    pending.refresh_from_db()
    assert pending.status == TurnStatus.COMPLETED
    assert runner.prompts == ["Next"]


def test_cancel_pending_turn_leaves_active_turn_running(session: AgentSession) -> None:
    active = session.post("Active")
    pending = session.post("Cancel pending")
    session.claim_turn()
    session.cancel_turn(pending)
    active.refresh_from_db()
    session.refresh_from_db()
    assert active.status == TurnStatus.RUNNING
    assert session.status == SessionStatus.RUNNING


def test_stale_outcome_cannot_settle_a_later_running_turn(session: AgentSession) -> None:
    stopped = session.post("Old turn")
    next_turn = session.post("Next turn")
    session.claim_turn()
    session.cancel_turn(stopped)
    claimed = session.claim_turn()
    assert claimed is not None and claimed.pk == next_turn.pk

    session.settle_turn(stopped, TurnOutcome(kind="failed", error="Old provider failure", usage={"tokens": 9}))

    session.refresh_from_db()
    next_turn.refresh_from_db()
    assert session.status == SessionStatus.RUNNING
    assert next_turn.status == TurnStatus.RUNNING
    assert session.last_error == "" and session.usage == {}


@pytest.mark.parametrize("close", [False, True])
def test_stop_or_close_keeps_emitted_updates_and_ends_the_stream_at_its_next_flush(
    session: AgentSession,
    runner: FakeRunner,
    close: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [time.monotonic()]
    monkeypatch.setattr("angee.agents.sessions.monotonic", lambda: clock[0])
    turn = session.post("Streaming")
    after_stop: list[str] = []

    def stop_while_streaming(active: Any, running: Any, emit: SessionUpdateSink) -> None:
        emit(_chunk("Before stop"))
        with actor_context(session.owner):
            if close:
                session.close()
            else:
                session.cancel_turn(running)
        clock[0] += 1
        emit(_chunk("Before observing stop"))
        after_stop.append("runner continued")

    runner.during_turn = stop_while_streaming
    run_session.run(session.pk)

    turn.refresh_from_db()
    session.refresh_from_db()
    assert after_stop == []
    assert turn.status == TurnStatus.CANCELED
    assert turn.error == ""
    assert turn.updates == [_chunk("Before stop"), _chunk("Before observing stop")]
    assert session.status == (SessionStatus.CLOSED if close else SessionStatus.IDLE)


def test_close_cancels_open_turns_and_refuses_future_posts(
    session: AgentSession,
    runner: FakeRunner,
    capture_tasks: list[Any],
) -> None:
    first = session.post("Active")
    pending = session.post("Pending")
    session.claim_turn()
    capture_tasks.clear()
    session.close()
    session.close()

    first.refresh_from_db()
    pending.refresh_from_db()
    session.refresh_from_db()
    assert session.status == SessionStatus.CLOSED
    assert first.status == pending.status == TurnStatus.CANCELED
    assert capture_tasks == []
    with pytest.raises(ValidationError, match="[Cc]losed"):
        session.post("Refused")
    run_session.run(session.pk)
    assert runner.prompts == []


@pytest.mark.parametrize(
    "error",
    [TimeoutError(), SoftTimeLimitExceeded(), RuntimeError("Provider unavailable"), asyncio.CancelledError()],
)
def test_runtime_failure_is_retained_and_session_remains_usable(
    session: AgentSession,
    runner: FakeRunner,
    error: BaseException,
) -> None:
    first = session.post("Fail once")
    runner.error = error
    run_session.run(session.pk)

    first.refresh_from_db()
    session.refresh_from_db()
    assert first.status == TurnStatus.FAILED
    assert first.error
    assert session.status == SessionStatus.ERROR
    assert session.last_error == first.error
    assert first.updates == [_chunk("Reply")]

    runner.error = None
    second = session.post("Try a new message")
    run_session.run(session.pk)
    second.refresh_from_db()
    session.refresh_from_db()
    assert second.status == TurnStatus.COMPLETED
    assert session.status == SessionStatus.IDLE and session.last_error == ""
    assert runner.prompts == ["Fail once", "Try a new message"]


def test_missing_model_is_a_failed_turn(session: AgentSession) -> None:
    turn = session.post("No configured model")
    run_session.run(session.pk)
    turn.refresh_from_db()
    session.refresh_from_db()
    assert turn.status == TurnStatus.FAILED
    assert turn.error == "Agent runtime failed."
    assert session.status == SessionStatus.ERROR
    assert session.post("Another message").status == TurnStatus.PENDING


def test_missing_agent_principal_is_a_failed_turn(session: AgentSession, runner: FakeRunner) -> None:
    turn = session.post("No service user")
    with system_context(reason="test missing service principal"):
        Agent.objects.filter(pk=session.agent_id).update(user=None)

    run_session.run(session.pk)

    turn.refresh_from_db()
    session.refresh_from_db()
    assert turn.status == TurnStatus.FAILED
    assert turn.error == "Agent runtime failed."
    assert session.status == SessionStatus.ERROR
    assert runner.prompts == []


def test_tool_approval_outcome_fails_readably(session: AgentSession, runner: FakeRunner) -> None:
    session.post("Before approval")
    run_session.run(session.pk)
    session.refresh_from_db()
    prior_replay = session.replay_state
    completed = runner.outcome
    turn = session.post("Needs approval")
    runner.outcome = TurnOutcome(
        kind="needs_approval",
        approval_requests=[{"tool_call_id": "call-1", "name": "read_document", "args": {}}],
        replay_state=[{"deferred": "call-1"}],
        usage={"requests": 1},
    )
    run_session.run(session.pk)
    turn.refresh_from_db()
    session.refresh_from_db()
    assert turn.status == TurnStatus.FAILED
    assert turn.error == "Tool approvals are not available yet."
    assert session.status == SessionStatus.ERROR
    assert session.usage == {"requests": 2, "tokens": 3}
    assert session.replay_state == prior_replay
    runner.outcome = completed
    continued = session.post("Continue")
    run_session.run(session.pk)
    continued.refresh_from_db()
    assert continued.status == TurnStatus.COMPLETED


def test_append_updates_publishes_and_keeps_the_final_stopped_batch(session: AgentSession) -> None:
    turn = session.post("Stream")
    session.claim_turn()
    already_wired = disconnect_publishers(AgentTurn)
    connect_publishers(AgentTurn, readable_fields=("updates", "status"))
    try:
        with observe(AgentTurn) as published:
            assert turn.append_updates([_chunk("First"), _chunk(" second")])
        assert len(published) == 1
        turn.refresh_from_db()
        assert turn.updates == [_chunk("First"), _chunk(" second")]
        session.cancel_turn(turn)
        with observe(AgentTurn) as published:
            assert turn.append_updates([_chunk("Before observing stop")]) is False
        assert len(published) == 1
        turn.refresh_from_db()
        assert turn.updates == [_chunk("First"), _chunk(" second"), _chunk("Before observing stop")]
    finally:
        disconnect_publishers(AgentTurn)
        if already_wired:
            connect_publishers(AgentTurn)


def test_broker_error_leaves_committed_turn_first_in_queue(session: AgentSession, runner: FakeRunner) -> None:
    with capture_task_sends(error=RuntimeError("Broker unavailable")):
        with pytest.raises(RuntimeError, match="Broker unavailable"):
            session.post("Committed before failed send")
    first = session.turns.get(index=1)
    assert first.status == TurnStatus.PENDING

    session.post("Next message")
    run_session.run(session.pk)
    first.refresh_from_db()
    assert first.status == TurnStatus.COMPLETED
    assert runner.prompts == ["Committed before failed send"]


@pytest.mark.parametrize("other_status", [TurnStatus.RUNNING, TurnStatus.AWAITING_APPROVAL])
def test_database_allows_only_one_active_turn(session: AgentSession, other_status: str) -> None:
    session.post("Active")
    pending = session.post("Also active")
    session.claim_turn()
    with system_context(reason="test active turn constraint"), pytest.raises(IntegrityError), transaction.atomic():
        AgentTurn.objects.filter(pk=pending.pk).update(status=other_status)


@pytest.mark.parametrize("close", [False, True])
def test_legacy_awaiting_approval_is_released_only_by_stop_or_close(session: AgentSession, close: bool) -> None:
    turn = session.post("Legacy approval")
    claimed = session.claim_turn()
    with system_context(reason="test legacy approval state"):
        claimed.mark_awaiting_approval()
        session.refresh_from_db()
        session.mark_awaiting_approval()
    assert session.claim_turn() is None
    with pytest.raises(ValidationError, match="approval"):
        session.post("Blocked while awaiting")
    if close:
        session.close()
    else:
        session.cancel_turn(turn)
    turn.refresh_from_db()
    session.refresh_from_db()
    assert turn.status == TurnStatus.CANCELED
    assert session.status == (SessionStatus.CLOSED if close else SessionStatus.IDLE)


def test_start_and_post_enforce_call_at_the_model_verbs(session: AgentSession) -> None:
    stranger = get_user_model().objects.create_user(username="session-stranger")
    with actor_context(stranger), pytest.raises(PermissionDenied):
        AgentSession.objects.start(session.agent, owner=stranger, context={})
    with pytest.raises(PermissionDenied):
        session.post("Forbidden", actor=stranger)
    with system_context(reason="test transferred agent owner"):
        agent = Agent.objects.get(pk=session.agent_id)
        agent.owner = stranger
        agent.save(update_fields=["owner"])
    with pytest.raises(PermissionDenied):
        session.post("Former owner cannot call")


def test_stop_and_close_check_the_explicit_actor_for_bound_instances(session: AgentSession) -> None:
    turn = session.post("Owner's turn")
    session.claim_turn()
    stranger = get_user_model().objects.create_user(username="stop-stranger")

    with pytest.raises(PermissionDenied):
        session.cancel_turn(turn, actor=stranger)
    with pytest.raises(PermissionDenied):
        session.close(actor=stranger)

    turn.refresh_from_db()
    session.refresh_from_db()
    assert turn.status == TurnStatus.RUNNING
    assert session.status == SessionStatus.RUNNING


@pytest.mark.parametrize(
    ("runtime_class", "runtime_status"),
    [("none", RuntimeStatus.RUNNING), ("pydantic", RuntimeStatus.STOPPED)],
)
def test_start_requires_an_available_in_process_runtime(
    session: AgentSession,
    runtime_class: str,
    runtime_status: str,
) -> None:
    with system_context(reason="test unavailable agent"):
        agent = session.agent
        agent.runtime_class = runtime_class
        agent.runtime_status = runtime_status
        agent.save(update_fields=["runtime_class", "runtime_status"])
    with pytest.raises(ValidationError):
        AgentSession.objects.start(agent, owner=session.owner, context={})


def test_deprovision_closes_sessions(session: AgentSession) -> None:
    turn = session.post("Open during deprovision")
    session.claim_turn()
    result = provisioning.deprovision_agent(session.agent.sqid)
    session.refresh_from_db()
    turn.refresh_from_db()
    session.agent.refresh_from_db()
    assert result.ok
    assert session.status == SessionStatus.CLOSED
    assert turn.status == TurnStatus.CANCELED


def test_start_refuses_an_agent_fetched_before_deprovisioning(session: AgentSession) -> None:
    """A stale ready instance cannot open a session after teardown closed them."""

    stale_agent = Agent.objects.get(pk=session.agent_id)
    result = provisioning.deprovision_agent(stale_agent.sqid)
    assert result.ok
    assert stale_agent.can_chat

    with pytest.raises(ValidationError, match="Agent is not running"):
        AgentSession.objects.start(stale_agent, owner=session.owner, context={})

    session.refresh_from_db()
    assert session.status == SessionStatus.CLOSED
    assert AgentSession.objects.filter(agent=stale_agent).count() == 1


def test_stream_updates_do_no_database_work_before_the_flush_interval(
    session: AgentSession,
    runner: FakeRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = time.monotonic()
    monkeypatch.setattr("angee.agents.sessions.monotonic", lambda: clock)
    turn = session.post("A streamed answer")
    stream_queries: list[dict[str, Any]] = []

    def emit_many(active: Any, running: Any, emit: SessionUpdateSink) -> None:
        with CaptureQueriesContext(connection) as queries:
            for index in range(20):
                emit(_chunk(str(index)))
        stream_queries.extend(queries)

    runner.during_turn = emit_many
    run_session.run(session.pk)

    turn.refresh_from_db()
    assert stream_queries == []
    assert turn.status == TurnStatus.COMPLETED
    assert turn.updates == [_chunk(str(index)) for index in range(20)]


def test_final_flush_uses_the_agent_principal(
    session: AgentSession,
    runner: FakeRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session.post("A final partial chunk")
    append = AgentTurn.append_updates
    actors: list[Any] = []

    def append_as_agent(turn: Any, batch: Any) -> bool:
        actors.append(current_actor())
        return append(turn, batch)

    monkeypatch.setattr(AgentTurn, "append_updates", append_as_agent)
    run_session.run(session.pk)

    assert actors and all(actor == session.agent.principal_subject() for actor in actors)


def test_append_reads_only_lock_and_save_fields_without_system_audits(session: AgentSession) -> None:
    session.post("A growing transcript")
    turn = session.claim_turn()
    assert turn is not None
    with actor_context(session.agent.principal_subject()):
        audits = PermissionAuditEvent.objects.count()
        with CaptureQueriesContext(connection) as queries:
            assert turn.append_updates([_chunk("First batch")])
            assert turn.append_updates([_chunk("Second batch")])
        assert PermissionAuditEvent.objects.count() == audits
    selects = [query["sql"].split(" FROM ", 1)[0] for query in queries if query["sql"].startswith("SELECT ")]
    assert selects
    assert all('"updates"' not in projection for projection in selects)
    turn_selects = [projection for projection in selects if f'"{AgentTurn._meta.db_table}"."status"' in projection]
    assert turn_selects
    assert all(not projection.startswith("SELECT DISTINCT ") for projection in turn_selects)
    timestamp_selects = [
        projection for projection in selects if f'"{AgentTurn._meta.db_table}"."updated_at"' in projection
    ]
    assert timestamp_selects == turn_selects
    turn.refresh_from_db()
    assert turn.updates == [_chunk("First batch"), _chunk("Second batch")]


def test_failed_runner_outcome_keeps_completed_replay_history(session: AgentSession, runner: FakeRunner) -> None:
    session.post("Complete first")
    run_session.run(session.pk)
    session.refresh_from_db()
    history = session.replay_state
    turn = session.post("Fail without replacement history")
    runner.outcome = TurnOutcome(kind="failed", error="Agent runtime failed.")

    run_session.run(session.pk)

    turn.refresh_from_db()
    session.refresh_from_db()
    assert turn.status == TurnStatus.FAILED
    assert session.replay_state == history


@pytest.mark.parametrize("kind", ["failed", "needs_approval"])
def test_failed_settlement_does_not_rewrite_replay_state(
    session: AgentSession,
    runner: FakeRunner,
    kind: Any,
) -> None:
    session.post("Retain a completed answer")
    run_session.run(session.pk)
    session.refresh_from_db()
    history = session.replay_state
    session.post("A turn without replacement history")
    turn = session.claim_turn()
    assert turn is not None

    with CaptureQueriesContext(connection) as queries:
        session.settle_turn(turn, TurnOutcome(kind=kind, replay_state=["Discard"], usage={"tokens": 1}))

    session_updates = [
        query["sql"] for query in queries if query["sql"].startswith(f'UPDATE "{AgentSession._meta.db_table}" ')
    ]
    assert session_updates
    assert all('"replay_state" =' not in query for query in session_updates)
    session.refresh_from_db()
    assert session.replay_state == history


def test_stray_delivery_preserves_the_last_failed_turn_error(session: AgentSession, runner: FakeRunner) -> None:
    session.post("Complete first")
    second = session.post("Fail second")
    run_session.run(session.pk)
    runner.error = RuntimeError("Provider failure detail")
    run_session.run(session.pk)
    second.refresh_from_db()
    assert second.status == TurnStatus.FAILED

    run_session.run(session.pk)

    session.refresh_from_db()
    assert session.status == SessionStatus.ERROR
    assert session.last_error == second.error
    assert runner.prompts == ["Complete first", "Fail second"]


@pytest.mark.parametrize("awaiting_approval", [False, True])
def test_claim_transition_only_accepts_pending_turns(session: AgentSession, awaiting_approval: bool) -> None:
    session.post("Already claimed")
    turn = session.claim_turn()
    assert turn is not None
    with system_context(reason="test repeated claim"):
        if awaiting_approval:
            turn.mark_awaiting_approval()
        with pytest.raises(TransitionNotAllowed):
            turn.mark_running()


@pytest.mark.parametrize("stop", [False, True])
def test_stop_and_settle_keep_their_result_when_the_next_wakeup_fails(
    session: AgentSession,
    runner: FakeRunner,
    stop: bool,
) -> None:
    first = session.post("Current turn")
    pending = session.post("Pending turn")
    session.claim_turn()
    with capture_task_sends(error=RuntimeError("Broker unavailable")):
        if stop:
            session.cancel_turn(first)
        else:
            session.settle_turn(first, runner.outcome)
    first.refresh_from_db()
    pending.refresh_from_db()
    assert first.status == (TurnStatus.CANCELED if stop else TurnStatus.COMPLETED)
    assert pending.status == TurnStatus.PENDING

    session.post("Wake the pending turn")
    run_session.run(session.pk)
    pending.refresh_from_db()
    assert pending.status == TurnStatus.COMPLETED
    assert runner.prompts == ["Pending turn"]


def test_dropped_connection_before_final_flush_still_settles_the_turn(
    session: AgentSession,
    runner: FakeRunner,
) -> None:
    turn = session.post("Keep the completed answer")

    def drop_connection(active: Any, running: Any, emit: SessionUpdateSink) -> None:
        emit(_chunk("Before connection loss"))
        connection.connection.close()

    runner.during_turn = drop_connection
    try:
        run_session.run(session.pk)
    finally:
        connection.close()
    turn.refresh_from_db()
    session.refresh_from_db()
    assert turn.status == TurnStatus.COMPLETED
    assert turn.updates == [_chunk("Before connection loss")]
    assert session.status == SessionStatus.IDLE


@pytest.mark.parametrize(
    ("runtime_class", "runtime_status"),
    [("none", RuntimeStatus.RUNNING), ("pydantic", RuntimeStatus.STOPPED)],
)
def test_claim_rechecks_the_agent_runtime_before_running(
    session: AgentSession,
    runner: FakeRunner,
    runtime_class: str,
    runtime_status: str,
) -> None:
    turn = session.post("Queued while available")
    with system_context(reason="test changed runtime availability"):
        Agent.objects.filter(pk=session.agent_id).update(runtime_class=runtime_class, runtime_status=runtime_status)

    run_session.run(session.pk)

    turn.refresh_from_db()
    session.refresh_from_db()
    assert turn.status == TurnStatus.FAILED
    assert turn.error == session.agent.chat_blocker()
    assert session.status == SessionStatus.ERROR
    assert runner.prompts == []


def test_start_cannot_assign_another_session_owner(session: AgentSession) -> None:
    stranger = get_user_model().objects.create_user(username="other-session-owner")
    with pytest.raises(PermissionDenied):
        AgentSession.objects.start(session.agent, owner=stranger, context={}, actor=session.owner)


def test_deprovision_closes_sessions_owned_by_another_person(session: AgentSession) -> None:
    session.post("A historical session owned by another person")
    other = get_user_model().objects.create_user(username="historical-session-owner")
    with system_context(reason="test historical session owner"):
        AgentSession.objects.filter(pk=session.pk).update(owner=other)

    result = provisioning.deprovision_agent(session.agent.sqid)

    assert result.ok
    with system_context(reason="test deprovisioned session"):
        session.refresh_from_db()
    assert session.status == SessionStatus.CLOSED


def test_provider_exception_detail_is_logged_without_being_shown_to_readers(
    session: AgentSession,
    runner: FakeRunner,
    caplog: pytest.LogCaptureFixture,
) -> None:
    turn = session.post("Fail safely")
    runner.error = RuntimeError("Vendor response includes private transport detail")

    run_session.run(session.pk)

    turn.refresh_from_db()
    session.refresh_from_db()
    assert turn.error == session.last_error == "Agent runtime failed."
    assert "Vendor response includes private transport detail" in caplog.text


def _delete_session_as(user: Any, session_id: int) -> None:
    """Delete a session as ``user``; session deletion is admin-only."""

    with actor_context(user):
        AgentSession.objects.get(pk=session_id).delete()


def test_delivery_after_session_deletion_is_a_quiet_noop(
    session: AgentSession,
    runner: FakeRunner,
    caplog: pytest.LogCaptureFixture,
) -> None:
    session.post("Deleted before the worker starts")
    session_id = session.pk
    with pytest.raises(PermissionDenied):
        _delete_session_as(session.owner, session_id)
    _delete_session_as(create_platform_admin("session-admin"), session_id)

    run_session.run(session_id)

    assert session.claim_turn() is None
    assert runner.prompts == []
    assert not AgentSession.system_queryset().filter(pk=session_id).exists()
    assert not any(record.name == "angee.agents.sessions" for record in caplog.records)


@pytest.mark.parametrize("delete_session", [False, True])
def test_stop_then_delete_during_streaming_is_a_quiet_cancellation(
    session: AgentSession,
    runner: FakeRunner,
    delete_session: bool,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = [time.monotonic()]
    monkeypatch.setattr("angee.agents.sessions.monotonic", lambda: clock[0])
    turn = session.post("Deleted after Stop")
    admin = create_platform_admin("session-admin")
    after_stop: list[str] = []

    def stop_and_delete(active: Any, running: Any, emit: SessionUpdateSink) -> None:
        emit(_chunk("Before deletion"))
        with actor_context(session.owner):
            session.cancel_turn(running)
            if delete_session:
                _delete_session_as(admin, session.pk)
            else:
                AgentTurn.objects.get(pk=running.pk).delete()
        clock[0] += 1
        emit(_chunk("Before observing deletion"))
        after_stop.append("runner continued")

    runner.during_turn = stop_and_delete
    run_session.run(session.pk)

    assert after_stop == []
    assert not AgentTurn.system_queryset().filter(pk=turn.pk).exists()
    assert not any(record.name == "angee.agents.sessions" for record in caplog.records)
    with actor_context(session.agent.principal_subject()):
        assert turn.append_updates([_chunk("Worker observes deletion")]) is False
    if not delete_session:
        session.refresh_from_db()
        assert session.status == SessionStatus.IDLE
        assert session.last_error == ""


@pytest.mark.parametrize("delete_session", [False, True])
def test_settlement_after_deletion_is_a_quiet_noop(
    session: AgentSession,
    delete_session: bool,
    capture_tasks: list[Any],
) -> None:
    session.post("Finish after deletion")
    turn = session.claim_turn()
    assert turn is not None
    session.cancel_turn(turn)
    if delete_session:
        _delete_session_as(create_platform_admin("session-admin"), session.pk)
    else:
        AgentTurn.objects.get(pk=turn.pk).delete()
    capture_tasks.clear()

    session.settle_turn(turn, TurnOutcome(kind="completed", text="Discard", replay_state=["Discard"]))

    assert not AgentTurn.system_queryset().filter(pk=turn.pk).exists()
    assert capture_tasks == []
    if not delete_session:
        session.refresh_from_db()
        assert session.status == SessionStatus.IDLE
        assert session.replay_state == []


def test_executor_refuses_a_transaction_without_touching_caller_work(
    session: AgentSession,
    runner: FakeRunner,
) -> None:
    with transaction.atomic():
        turn = session.post("Still pending after refusal")
        session.title = "Caller work before execution"
        session.save(update_fields=["title"])
        with pytest.raises(RuntimeError, match="transaction"):
            run_session.run(session.pk)
        assert connection.in_atomic_block
        turn.refresh_from_db()
        assert turn.status == TurnStatus.PENDING
        session.context = {"after_refusal": "Caller transaction remains usable"}
        session.save(update_fields=["context"])

    session.refresh_from_db()
    assert session.title == "Caller work before execution"
    assert session.context == {"after_refusal": "Caller transaction remains usable"}
    assert session.status == SessionStatus.IDLE
    assert runner.prompts == []


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (TimeoutError("Private timeout transport detail"), "The agent turn exceeded its time limit."),
        (SoftTimeLimitExceeded("Private worker interruption detail"), "The agent turn exceeded its time limit."),
        (asyncio.CancelledError("Private cancellation detail"), "The agent turn was interrupted."),
    ],
)
def test_timeout_and_cancellation_log_diagnostics_with_stable_public_errors(
    session: AgentSession,
    runner: FakeRunner,
    caplog: pytest.LogCaptureFixture,
    error: BaseException,
    message: str,
) -> None:
    turn = session.post("Retain a safe failure")
    runner.error = error

    run_session.run(session.pk)

    turn.refresh_from_db()
    session.refresh_from_db()
    assert turn.status == TurnStatus.FAILED
    assert turn.error == session.last_error == message
    records = [record for record in caplog.records if record.name == "angee.agents.sessions"]
    assert records and all(record.levelname == "WARNING" for record in records)
    assert str(turn.pk) in caplog.text
    assert error.args[0] not in caplog.text


@pytest.mark.parametrize("runtime,status", [("pydantic", "stopped"), ("claude_code", "running")])
def test_chat_blocker_is_shared_by_start_post_and_claim(
    session: AgentSession,
    runtime: str,
    status: str,
) -> None:
    pending = session.post("Accepted while available")
    with system_context(reason="test shared chat availability"):
        Agent.objects.filter(pk=session.agent_id).update(runtime_class=runtime, runtime_status=status)
    agent = Agent.objects.get(pk=session.agent_id)
    blocker = agent.chat_blocker()
    assert blocker
    with pytest.raises(ValidationError) as refused:
        AgentSession.objects.start(agent, owner=session.owner, context={})
    assert refused.value.message_dict["agent"] == [blocker]
    with pytest.raises(ValidationError) as refused:
        session.post("Refused before insertion")
    assert refused.value.message_dict["agent"] == [blocker]
    assert session.claim_turn() is None
    pending.refresh_from_db()
    assert pending.status == TurnStatus.FAILED and pending.error == blocker


def test_session_cancels_active_work_without_canceling_queued_turns(session: AgentSession) -> None:
    first = session.post("Active")
    claimed = session.claim_turn()
    assert claimed.pk == first.pk
    second = session.post("Queued")
    session.cancel_active_turn()
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == TurnStatus.CANCELED and second.status == TurnStatus.PENDING
    claimed = session.claim_turn()
    assert claimed.pk == second.pk
    with system_context(reason="test suspended state projection"):
        claimed.mark_awaiting_approval()
    session.cancel_active_turn()
    claimed.refresh_from_db()
    assert claimed.status == TurnStatus.CANCELED
