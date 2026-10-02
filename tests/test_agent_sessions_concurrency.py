"""PostgreSQL session interleavings through real verbs and independent connections."""

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from rebac import actor_context

from angee.agents import provisioning
from angee.agents.models import SessionStatus, TurnStatus
from angee.agents.runners import TurnOutcome
from angee.agents.testing.models import Agent, AgentSession, AgentTurn
from angee.base.scoping import system_queryset
from tests.test_agent_sessions import session as session
from tests.test_decisions_concurrency import submit, wait_for_lock

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


def test_two_simultaneous_claims_claim_exactly_one_turn(session: AgentSession) -> None:
    first = session.post("First queued turn")
    pending = session.post("Second queued turn")
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            system_queryset(AgentSession).select_for_update().get(pk=session.pk)
            contender, pid = submit(pool, session.claim_turn)
            wait_for_lock(pid, contender)
            claimed = session.claim_turn()
            assert claimed is not None and claimed.pk == first.pk
        assert contender.result(timeout=10) is None
    first.refresh_from_db()
    pending.refresh_from_db()
    assert first.status == TurnStatus.RUNNING
    assert pending.status == TurnStatus.PENDING


def test_post_blocked_behind_settlement_sends_exactly_one_wakeup(
    session: AgentSession, capture_tasks: list[Any],
) -> None:
    session.post("Settle before the new post")
    claimed = session.claim_turn()
    assert claimed is not None
    owner = session.owner
    capture_tasks.clear()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            system_queryset(AgentSession).select_for_update().get(pk=session.pk)
            session.settle_turn(claimed, TurnOutcome(kind="completed", text="Done"))
            contender, pid = submit(pool, lambda: session.post("Post while settling", actor=owner))
            wait_for_lock(pid, contender)
            assert capture_tasks == []
        posted = contender.result(timeout=10)
    assert posted.status == TurnStatus.PENDING
    assert [name for name, _options in capture_tasks] == ["agents.run_session"]


@pytest.mark.parametrize("winner", ["start", "deprovision"])
def test_start_races_with_in_process_deprovision(session: AgentSession, winner: str) -> None:
    owner = session.owner
    agent = session.agent

    def deprovision() -> Any:
        with actor_context(owner):
            return provisioning.deprovision_agent(agent.sqid)

    def start() -> Any:
        return AgentSession.objects.start(agent, owner=owner, context={}, actor=owner)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            system_queryset(Agent).select_for_update().get(pk=agent.pk)
            if winner == "start":
                created = start()
                contender, pid = submit(pool, deprovision)
            else:
                assert deprovision().ok
                contender, pid = submit(pool, start)
            wait_for_lock(pid, contender)
        if winner == "start":
            assert contender.result(timeout=10).ok
            created.refresh_from_db()
            assert created.status == SessionStatus.CLOSED
        else:
            with pytest.raises(ValidationError, match="Provision this agent"):
                contender.result(timeout=10)
    session.refresh_from_db()
    assert session.status == SessionStatus.CLOSED
    assert not system_queryset(AgentSession).filter(agent=agent).exclude(status=SessionStatus.CLOSED).exists()


def test_stop_during_an_append_keeps_the_streamed_batch(session: AgentSession) -> None:
    session.post("Stop while saving a tool event")
    turn = session.claim_turn()
    assert turn is not None
    owner = session.owner
    principal = session.agent.principal_subject()
    update = {"sessionUpdate": "tool_call_update", "toolCallId": "call-1", "status": "in_progress"}
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            system_queryset(AgentTurn).select_for_update().get(pk=turn.pk)
            contender, pid = submit(pool, lambda: session.cancel_turn(turn, actor=owner))
            wait_for_lock(pid, contender)
            with actor_context(principal):
                assert turn.append_updates([update])
        contender.result(timeout=10)
    turn.refresh_from_db()
    session.refresh_from_db()
    assert turn.status == TurnStatus.CANCELED
    assert turn.updates == [update]
    assert session.status == SessionStatus.IDLE


def test_append_waiting_for_stop_keeps_the_batch_and_reports_cancellation(session: AgentSession) -> None:
    session.post("Save the last tool event after Stop")
    turn = session.claim_turn()
    assert turn is not None
    worker_turn = AgentTurn.objects.get(pk=turn.pk)
    principal = session.agent.principal_subject()
    update = {"sessionUpdate": "tool_call_update", "toolCallId": "call-1", "status": "in_progress"}

    def append() -> bool:
        with actor_context(principal):
            return worker_turn.append_updates([update])

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            session.cancel_turn(turn)
            contender, pid = submit(pool, append)
            wait_for_lock(pid, contender)
        assert contender.result(timeout=10) is False
    turn.refresh_from_db()
    session.refresh_from_db()
    assert turn.status == TurnStatus.CANCELED
    assert turn.updates == [update]
    assert session.status == SessionStatus.IDLE
