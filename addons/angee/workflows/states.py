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
        return (cls.SUCCEEDED, cls.FAILED, cls.CANCELED)


class RunOrigin(models.TextChoices):
    """Admission source fixed with its cause when a run starts."""

    MANUAL = "manual", "Manual"
    WORKFLOW = "workflow", "Workflow"
    REPROCESS = "reprocess", "Reprocess"
    TRIGGER = "trigger", "Trigger"


class RunStatus(TerminalStates):
    """Lifecycle of one workflow run."""

    RUNNING = "running", "Running"
    WAITING = "waiting", "Waiting"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    CANCELED = "canceled", "Canceled"


class RunRelation(models.TextChoices):
    """Whether a child belongs to its parent's lifecycle or continues independently."""

    OWNED = "owned", "Owned"
    CONTINUATION = "continuation", "Continuation"


class StepRunStatus(TerminalStates):
    """Lifecycle of one graph node's execution."""

    READY = "ready", "Ready"
    RUNNING = "running", "Running"
    WAITING = "waiting", "Waiting"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    SKIPPED = "skipped", "Skipped"
    CANCELED = "canceled", "Canceled"

    @classmethod
    def terminal_values(cls) -> tuple[str, ...]:
        return tuple(str(member) for member in (cls.SUCCEEDED, cls.FAILED, cls.SKIPPED, cls.CANCELED))

    @property
    def has_outcome(self) -> bool:
        """Whether this state records a completed success or failure outcome."""
        return self in {self.SUCCEEDED, self.FAILED}


class WaitingKind(models.TextChoices):
    """Implemented durable wait kinds."""

    TIME = "time", "Time"
    RECORD = "record", "Record"
    DECISION = "decision", "Decision"
    MAP = "map", "Map"
    RUN = "run", "Run"
    ERROR = "error", "Error"


class AttemptResult(models.TextChoices):
    """Recorded outcome of a finished attempt."""

    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    TIMED_OUT = "timed_out", "Timed out"
    SUPERSEDED = "superseded", "Superseded"


class RecordOperation(models.TextChoices):
    """The operations retained on step record links."""

    READ = "read", "Read"
    CREATED = "created", "Created"
    CHANGED = "changed", "Changed"
    DELETED = "deleted", "Deleted"
    CALLED = "called", "Called"


class NoteTone(models.TextChoices):
    """The presentation tones retained with a step note."""

    INFO = "info", "Info"
    SUCCESS = "success", "Success"
    WARNING = "warning", "Warning"
    DANGER = "danger", "Danger"
