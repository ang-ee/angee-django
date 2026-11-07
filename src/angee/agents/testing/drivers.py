"""Provider-boundary fakes shared by persisted chat and ACP test suites."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

import pytest
from django.db import connection
from rebac import current_actor

from angee.agents.runners import SessionRunner, SessionUpdateSink, TurnOutcome
from angee.agents_runtime_pydantic.runtime import PydanticAIRuntime


def update_chunk(text: str, *, thought: bool = False) -> dict[str, Any]:
    return {
        "sessionUpdate": "agent_thought_chunk" if thought else "agent_message_chunk",
        "content": {"type": "text", "text": text},
    }


class FakeRunner(SessionRunner):
    """Replace only the provider boundary while retaining persistence and dispatch."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.history: list[Any] = []
        self.during_turn: Callable[[Any, Any, SessionUpdateSink], None] | None = None
        self.error: BaseException | None = None
        self.outcome = TurnOutcome(
            kind="completed",
            text="Reply",
            replay_state=[{"reply": "retained"}],
            usage={"requests": 1, "tokens": 3},
        )

    def run_turn(
        self,
        session: Any,
        turn: Any,
        *,
        deferred_results: list[Mapping[str, Any]],
        emit: SessionUpdateSink,
        deadline: float,
    ) -> TurnOutcome:
        assert current_actor() == session.agent.principal_subject()
        assert not connection.in_atomic_block
        assert deadline > time.monotonic()
        assert deferred_results == []
        self.prompts.append(turn.prompt)
        self.history.append(session.replay_state)
        if self.during_turn is not None:
            self.during_turn(session, turn, emit)
        else:
            emit(update_chunk("Reply"))
        if self.error is not None:
            raise self.error
        return self.outcome


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
    fake = FakeRunner()
    monkeypatch.setattr(PydanticAIRuntime, "session_runner", lambda self: fake)
    return fake
