"""Shared workflow declaration names and persisted lifecycle states."""

from typing import Annotated

from django.db import models
from pydantic import StringConstraints

NAME_MAX_LENGTH = 63
"""Maximum length of a workflow declaration name."""

NAME_PATTERN = rf"^[a-z][a-z0-9_]{{0,{NAME_MAX_LENGTH - 1}}}$"
"""Shared declaration syntax for node keys, outcomes and result aliases."""

type Name = Annotated[str, StringConstraints(pattern=NAME_PATTERN)]
type NodeKey = Name
type Outcome = Name

DONE_OUTCOME = "done"
"""The default outcome of a successful step or run."""

ERROR_OUTCOME = "error"
"""The built-in outcome reserved for unsuccessful step settlements."""

CANCELED_OUTCOME = "canceled"
"""The outcome of a run canceled before terminal settlement."""

INPUT_SOURCE = "input"
"""The reserved binding source for admitted workflow input."""

ITEM_SOURCE = "item"
"""The reserved binding source for a map body's current item."""


class TerminalStates(models.TextChoices):
    """Share terminal-state classification across execution lifecycles."""

    @classmethod
    def terminal_values(cls) -> tuple[str, ...]:
        """Return the terminal values supported by this lifecycle."""
        return tuple(
            member.value for name, member in cls.__members__.items()
            if name in {"SUCCEEDED", "FAILED", "SKIPPED", "CANCELED"}
        )


class RunStatus(TerminalStates):
    """Lifecycle of one workflow run."""

    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class StepRunStatus(TerminalStates):
    """Lifecycle of one graph node's execution."""

    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELED = "canceled"

    @property
    def has_outcome(self) -> bool:
        """Whether this state records a completed success or failure outcome."""
        return self in {self.SUCCEEDED, self.FAILED}


class WaitingKind(models.TextChoices):
    """Implemented durable wait kinds."""

    TIME = "time", "Time"


class AttemptResult(models.TextChoices):
    """Recorded outcome of a finished attempt."""

    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    TIMED_OUT = "timed_out", "Timed out"
    SUPERSEDED = "superseded", "Superseded"
