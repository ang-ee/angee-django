"""Runtime-neutral contract for executing one persisted agent turn.

The agents addon owns the session runner seam but no implementation. Provider
addons return ACP-shaped updates through the caller-supplied sink and return
opaque replay state without teaching the catalogue about their SDK types.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

SessionUpdateSink = Callable[[dict[str, Any]], None]
"""Sink for one ACP ``session/update`` payload."""

SessionHeartbeat = Callable[[], None]
"""Synchronous callback refreshing the workflow-owned execution lease."""


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    """Runtime-neutral result of one bounded agent turn."""

    kind: Literal["completed", "needs_approval", "failed"]
    usage: dict[str, int] = field(default_factory=dict)
    approval_requests: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    text: str = ""
    replay_state: Any = field(default_factory=list)


class SessionRunner:
    """Execute turns for one runtime without owning persistence or scheduling."""

    def run_turn(
        self,
        session: Any,
        turn: Any,
        *,
        deferred_results: list[Mapping[str, Any]],
        emit: SessionUpdateSink,
        heartbeat: SessionHeartbeat,
    ) -> TurnOutcome:
        """Run one turn and return its neutral outcome."""

        raise NotImplementedError
