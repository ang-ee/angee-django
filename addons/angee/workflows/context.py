"""Actor-scoped values, lookups and fenced execution verbs handed to steps."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models
from rebac.resources import model_resource_type

from angee.base.identity import instance_from_public_id, public_id_for
from angee.base.scoping import read_scoped_queryset
from angee.decisions.contracts import DecisionRequest
from angee.workflows.runner import runner
from angee.workflows.states import DONE_OUTCOME, RunRelation
from angee.workflows.steps import Ask, Done, Fail, NextPage, Step, StepMode, Wait


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
    def map_index(self) -> int | None:
        """Return the item position, including zero, or None outside a map body."""
        return self.step_run.map_index if self.step_run.is_mapped else None

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
        self._require_mode(StepMode.DATABASE)
        subject = self._subject(lock=True)
        if subject is None:
            raise ValidationError("This run has no subject.")
        return subject

    def _subject(self, *, lock: bool) -> models.Model | None:
        model = self.run.subject_model_class
        if model is None:
            return None
        return self.load(model, public_id_for(model, self.run.subject_object_id), lock=lock)

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
            self._require_mode(StepMode.DATABASE)
        if not model_resource_type(model):
            raise PermissionDenied(f"{model._meta.label} has no actor-scoped read contract.")
        queryset = read_scoped_queryset(model, self.actor, action=permission)
        if lock:
            queryset = queryset.lock_if_supported(no_key=True)
        instance = instance_from_public_id(model, public_id, queryset=queryset)
        if instance is None:
            raise PermissionDenied("The requested record is absent or inaccessible.")
        return instance

    def done(self, output: Any = None, *, outcome: str = DONE_OUTCOME) -> Done:
        """Construct this step's completion for checking at the body boundary."""
        return self.step.done(output, outcome=outcome)

    def wait(self, *, until: datetime | None = None, state: Any = None) -> Wait:
        """Wait for registered records or an aware deadline, retaining the checkpoint."""
        return Wait(until=until, state=state)

    def watch(self, *records: models.Model) -> None:
        """Observe later saves transactionally; lock records before testing a wait predicate."""
        self._require_mode(StepMode.DATABASE)
        apps.get_model("workflows", "StepWatch").objects.register(self.step_run, records, actor=self.actor)

    def next_page(self, state: Any = None) -> NextPage:
        """Continue with a raw checkpoint; checking and retry reset belong to settlement."""
        return NextPage(state=state)

    def fail(self, message: str) -> Fail:
        """Return a permanent failure; settlement owns its routing outcome."""
        return Fail(error=message)

    def ask(self, *requests: DecisionRequest, state: Any = None) -> Ask:
        """Construct review requests; the body boundary owns validation and admission."""
        self._require_mode(StepMode.DATABASE)
        return Ask(requests=requests, state=self.state if state is None else state)

    def decision(self, decision_ref: str) -> Any:
        """Read a prior decision asked by a step in this run."""
        self._require_mode(StepMode.DATABASE)
        model = apps.get_model("decisions", "Decision")
        row = instance_from_public_id(
            model,
            decision_ref,
            queryset=model.objects.with_actor(self.actor).filter(step_run__run=self.run),
        )
        if row is None:
            raise PermissionDenied("The decision is absent or inaccessible in this run.")
        return row

    def begin_effect(self) -> None:
        """Record possible external effects only while this IO attempt owns its fence."""
        self._require_mode(StepMode.IO)
        runner.begin_effect(self.step_run)

    def heartbeat(self) -> None:
        """Extend this IO attempt's deadline through the fenced transition owner."""
        self._require_mode(StepMode.IO)
        runner.heartbeat(self.step_run)

    def raise_if_canceled(self) -> None:
        """Stop cooperative IO work when cancellation or a newer attempt won."""
        self._require_mode(StepMode.IO)
        runner.raise_if_canceled(self.step_run)

    def artifact(self, record: models.Model, label: str = "") -> Any:
        """Stage actor-readable evidence for this attempt's successful settlement.

        The returned artifact is unsaved until settlement commits. Failed or
        superseded attempts discard their staged evidence.
        """
        artifact = runner.artifact(self.step_run, record, label=label, actor=self.actor)
        self.pending_artifacts.append(artifact)
        return artifact

    def start_run(
        self,
        workflow: Any,
        *,
        subject: Any = None,
        input: Any = None,
        request_key: str | None = None,
        relation: str = str(RunRelation.OWNED),
        version: Any = None,
    ) -> Any:
        """Start one child through admission, deriving a retry-stable key when omitted."""
        return type(self.run).objects.start(
            workflow,
            actor=self.actor,
            subject=subject,
            input=input,
            request_key=request_key,
            parent_step=self.step_run,
            relation=relation,
            version=version,
        )

    def cancel_run(self, run: Any) -> None:
        """Request cancellation as this run's actor after the body commits."""
        type(self.run).objects.cancel_on_commit(run, self.actor)

    def _require_mode(self, mode: StepMode) -> None:
        """Reject operations outside their declared transaction boundary."""
        if self.step.mode != mode:
            raise ValidationError(f"This operation requires {mode} mode.")
