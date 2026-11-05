"""Runtime-neutral contract for executing one persisted agent turn.

The agents addon owns the session runner seam but no implementation. Provider
addons return ACP-shaped updates through the caller-supplied sink and return
opaque replay state without teaching the catalogue about their SDK types.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

SessionUpdateSink = Callable[[dict[str, Any]], None]
"""Sink for one ACP ``session/update`` payload."""


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    """Runtime-neutral result of one bounded agent turn."""

    kind: Literal["completed", "failed"]
    usage: dict[str, int] = field(default_factory=dict)
    error: str = ""
    """Public refusal composed by the runtime; never raw provider exception text."""
    text: str = ""
    replay_state: Any = field(default_factory=list)


class SessionRunner:
    """Execute turns for one runtime without owning persistence or scheduling."""

    def run_turn(
        self,
        session: Any,
        turn: Any,
        *,
        emit: SessionUpdateSink,
        deadline: float,
    ) -> TurnOutcome:
        """Run one turn before the absolute monotonic deadline and return its outcome."""

        raise NotImplementedError
