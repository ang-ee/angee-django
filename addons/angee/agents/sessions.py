"""Execute one chat turn through its runtime, without retries or recovery.

The models own claim, append and settlement. A crashed or hard-limited worker
leaves its turn running until the user stops it; no delivery reclaims that turn.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from django.apps import apps
from django.db import close_old_connections, connection
from rebac import actor_context, system_context

from angee.agents.runners import TurnOutcome
from angee.jobs.timeouts import task_time_budget

logger = logging.getLogger(__name__)

SESSION_UPDATE_FLUSH_SECONDS = 0.25
"""Minimum interval between streamed transcript saves."""


class TurnStopped(Exception):
    """The turn no longer accepts updates; end its stream without recording failure."""


@dataclass
class _TurnUpdateSink:
    """Persist ACP batches and observe Stop at each flush."""

    turn: Any
    pending: list[dict[str, Any]] = field(default_factory=list)
    last_flush: float = field(default_factory=monotonic)

    def __call__(self, update: dict[str, Any]) -> None:
        self.pending.append(dict(update))
        if monotonic() - self.last_flush >= SESSION_UPDATE_FLUSH_SECONDS:
            self.flush()

    def flush(self) -> None:
        """Retain emitted updates before observing Stop or Close."""

        running = self.turn.append_updates(self.pending)
        self.pending.clear()
        self.last_flush = monotonic()
        if not running:
            raise TurnStopped


def run_next_turn(session_id: int) -> None:
    """Claim and execute at most one turn, outside a transaction as the agent."""

    if connection.in_atomic_block:
        raise RuntimeError("Agent turns must run outside a database transaction.")
    deadline = monotonic() + task_time_budget().total_seconds()
    session_model = apps.get_model("agents", "AgentSession")
    with system_context(reason="agents.session.execute"):
        session = session_model.objects.filter(pk=session_id).first()
        if session is None:
            return
        turn = session.claim_turn()
        if turn is None:
            return

    sink = _TurnUpdateSink(turn)
    try:
        with system_context(reason="agents.session.principal"):
            agent = session.agent
            principal = agent.principal_subject()
        with actor_context(principal):
            try:
                outcome = agent.runtime_backend.session_runner().run_turn(
                    session,
                    turn,
                    deferred_results=[],
                    emit=sink,
                    deadline=deadline,
                )
            finally:
                close_old_connections()
                sink.flush()
    except TurnStopped:
        return
    except (TimeoutError, SoftTimeLimitExceeded):
        logger.warning("Agent turn %s exceeded its time limit.", turn.pk)
        outcome = TurnOutcome(kind="failed", error="The agent turn exceeded its time limit.")
    except asyncio.CancelledError:
        logger.warning("Agent turn %s was interrupted.", turn.pk)
        outcome = TurnOutcome(kind="failed", error="The agent turn was interrupted.")
    except Exception:  # noqa: BLE001 - runtime failures settle this turn without retry.
        logger.exception("Agent turn %s failed.", turn.pk)
        outcome = TurnOutcome(kind="failed", error="Agent runtime failed.")
    session.settle_turn(turn, outcome)
