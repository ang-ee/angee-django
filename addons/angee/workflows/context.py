"""Actor-scoped values, lookups and fenced execution verbs handed to steps."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models

from angee.base.identity import instance_from_public_id
from angee.base.scoping import read_scoped_queryset
from angee.workflows.states import DONE_OUTCOME
from angee.workflows.steps import Done, Fail, NextPage, Step, Wait


@dataclass
class StepContext:
    """One claimed attempt with its already-resolved class and scoped principal."""

    run: Any
    step_run: Any
    step: type[Step]
    attempt: Any
    input: Any
    config: Any
    actor: Any
    now: datetime
    pending_artifacts: list[models.Model] = field(default_factory=list, init=False, repr=False)

    @property
    def state(self) -> Any:
        """Return the persisted checkpoint from the preceding wait."""
        return self.step_run.state

    @property
    def idempotency_key(self) -> str:
        """Return the persisted random token plus this page's zero-based index.

        Retries and time waits retain the key. A completed ``next_page`` advances
        the index, giving the next page its own key without reusing an earlier
        page's external effects. The first claim owns token generation.
        """
        return f"{self.step_run.idempotency_token}:{self.step_run.page_index}"

    @property
    def is_last_attempt(self) -> bool:
        """Whether another failure exhausts the current page's retry allowance."""
        return self.step_run.retries + 1 >= self.step.retry.max_attempts

    @property
    def retry_acknowledged(self) -> bool:
        """Whether an operator accepted a possible duplicate for this attempt."""
        return self.attempt.acknowledged_by_id is not None

    @property
    def subject(self) -> models.Model | None:
        """Resolve the run's declared subject through the shared reference owner."""
        return self._subject(lock=False)

    def subject_for_update(self) -> models.Model:
        """Lock the actor-readable subject after the already-held workflow rows."""
        self._require_mode("DATABASE")
        subject = self._subject(lock=True)
        if subject is None:
            raise ValidationError("This run has no subject.")
        return subject

    def _subject(self, *, lock: bool) -> models.Model | None:
        reference = self.run.record_ref
        if reference.object_id is None:
            return None
        if not reference.model_label:
            raise ValidationError("The run subject was deleted.")
        return self.load(apps.get_model(reference.model_label), reference.public_id, lock=lock)

    def load[M: models.Model](
        self,
        model: type[M],
        public_id: str,
        *,
        permission: str = "read",
        lock: bool = False,
    ) -> M:
        """Load a public id through native identity and permission scoping."""
        if lock:
            self._require_mode("DATABASE")
        queryset = read_scoped_queryset(model, self.actor, action=permission)
        if queryset is None:
            raise PermissionDenied(f"{model._meta.label} has no actor-scoped read contract.")
        if lock:
            queryset = queryset.lock_if_supported(no_key=True)
        instance = instance_from_public_id(model, public_id, queryset=queryset)
        if instance is None:
            raise PermissionDenied("The requested record is absent or inaccessible.")
        return instance

    def done(self, output: Any = None, *, outcome: str = DONE_OUTCOME) -> Done:
        """Construct this step's completion for checking at the body boundary."""
        return self.step.done(output, outcome=outcome)

    def wait(self, *, until: datetime, state: Any = None) -> Wait:
        """Wait until an aware time, carrying the supplied checkpoint."""
        return Wait(until=until, state=state)

    def next_page(self, state: Any = None) -> NextPage:
        """Continue with a raw checkpoint; checking and retry reset belong to settlement."""
        return NextPage(state=state)

    def fail(self, message: str) -> Fail:
        """Return a permanent failure; settlement owns its routing outcome."""
        return Fail(error=message)

    def begin_effect(self) -> None:
        """Record possible external effects only while this IO attempt owns its fence."""
        self._require_mode("IO")
        type(self.step_run).objects.begin_effect(self.step_run)

    def heartbeat(self) -> None:
        """Extend this IO attempt's deadline through the fenced transition owner."""
        self._require_mode("IO")
        type(self.step_run).objects.heartbeat(self.step_run)

    def raise_if_canceled(self) -> None:
        """Stop cooperative IO work when cancellation or a newer attempt won."""
        self._require_mode("IO")
        type(self.step_run).objects.raise_if_canceled(self.step_run)

    def artifact(self, record: models.Model, label: str = "") -> Any:
        """Stage actor-readable evidence for this attempt's successful settlement.

        The returned artifact is unsaved until settlement commits. Failed or
        superseded attempts discard their staged evidence.
        """
        artifact = type(self.step_run).objects.artifact(self.step_run, record, label=label, actor=self.actor)
        self.pending_artifacts.append(artifact)
        return artifact

    def _require_mode(self, mode: str) -> None:
        """Reject operations outside their declared transaction boundary."""
        if self.step.mode != mode:
            raise ValidationError(f"This operation requires {mode} mode.")
