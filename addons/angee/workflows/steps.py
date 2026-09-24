"""Step implementation seam for workflow definitions.

Step rows store a registry key in ``step_class``. The configured class owns the
behavior selected by that key and validates the row's ``config`` before the row is
saved. Product addons register their own :class:`StepImpl` subclasses through
``ANGEE_WORKFLOW_STEP_CLASSES``; row data never stores dotted import paths.

Implementations declare ``deterministic``: deterministic implementations may be
replayed for routing, while non-deterministic activity implementations are
journaled by the runtime. Implementations may also declare ``decision_schema`` for
typed resume payloads. Suspension persists no in-process state: ``resume_state``
on the future step-run journal is the only state surviving suspension, and an
implementation must write any continuation facts there before returning a
suspended result.

Execution replay is separate from routing determinism. It is unavailable unless
the operation explicitly declares ``replay_mode`` or proves a narrower provider
capability by overriding ``recovery_capability()``.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from threading import Event, Thread
from typing import Any, ClassVar, Literal, Self

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist, ValidationError
from django.db import connections, models
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from jsonschema import Draft202012Validator
from pydantic import BaseModel
from rebac import system_context

from angee.base.db import get_write_alias, related_on
from angee.base.identity import instance_from_public_id, public_id_for
from angee.base.impl import ImplBase, ImplChoice
from angee.base.scoping import read_scoped_queryset, system_queryset
from angee.workflows.attempts import (
    ArtifactSpec,
    AttemptResult,
    AttemptResultKind,
    DecisionGateOutput,
    DecisionRecordAccess,
    DecisionSpec,
    ExternalOperationPolicy,
    JsonPresence,
    LeaseRevocationReason,
    RecoveryCapability,
    RecoveryMode,
    json_values_equal,
    validate_json_value,
)
from angee.workflows.bindings import (
    BindingContext,
    SourceValue,
    UnavailableSource,
    evaluate_binding,
    parse_binding,
)
from angee.workflows.configs import (
    CallWorkflowConfig,
    EmitConfig,
    GateConfig,
    JoinContinuationConfig,
    MapConfig,
    WaitConfig,
    is_gate_binding_mapping,
    map_items_expression_path,
)
from angee.workflows.data_contracts import (
    DataContract,
    json_value_at_path,
    model_data_contract,
    schema_data_contract,
)

_MODEL_LABEL_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")
_OUTCOME_KEY_FIELD = models.SlugField(max_length=100)


class TransientStepError(Exception):
    """Signal that a step implementation failed with a retryable condition."""


@dataclass(frozen=True, slots=True)
class StepRetryPolicy:
    """Static queue retry policy declared by one step's JSON config."""

    max_attempts: int = 1
    wait: int = 0
    linear_wait: int = 0
    exponential_wait: int = 0

    def delay_for(self, retry_index: int) -> int:
        """Return the declared delay for one positive retry-series index."""

        if retry_index <= 0:
            raise ValueError("Retry index must be positive.")
        if self.wait:
            return self.wait
        if self.linear_wait:
            return self.linear_wait * retry_index
        if self.exponential_wait:
            return self.exponential_wait * (2 ** (retry_index - 1))
        return 0


class StepEffect(str, Enum):
    """Declared external effect category of a workflow operation."""

    UNKNOWN = "unknown"
    NONE = "none"
    READ = "read"
    WRITE = "write"
    EXTERNAL = "external"


class StepExecutionMode(str, Enum):
    """Runtime boundary for an attempt, independent of authoring effect labels."""

    STANDARD = "standard"
    DATABASE_COMMAND = "database_command"
    EXTERNAL_OPERATION = "external_operation"


@dataclass(frozen=True, slots=True)
class StepOutcome:
    """One labeled routing outcome a step implementation may produce."""

    key: str
    label: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class StepOperation:
    """Complete authoring metadata projected from one registered step implementation."""

    choice: ImplChoice
    description: str
    selectable: bool
    input_contract: DataContract
    output_contract: DataContract
    outcomes: tuple[StepOutcome, ...]
    effect: StepEffect
    effect_description: str
    idempotent: bool | None
    subject_declaration: str
    map_body_operation: bool

    @property
    def input_schema(self) -> dict[str, Any] | None:
        """Return the compatibility schema from the Pydantic-owned contract."""

        return self.input_contract.raw_schema

    @property
    def output_schema(self) -> dict[str, Any] | None:
        """Return the compatibility schema from the Pydantic-owned contract."""

        return self.output_contract.raw_schema


@dataclass(frozen=True, slots=True)
class GateResumption:
    """Retained state and exact terminal slots of one resumable gate suspension."""

    outcome: str
    resolutions: dict[str, Any]
    state: dict[str, Any]
    slots: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class StepResult:
    """Result returned by a workflow step implementation.

    ``done(output, outcome)`` completes the step and routes by ``outcome``.
    ``wait(until=...)`` records a durable timer wake. External events use
    :func:`angee.workflows.engine.deliver`, whose run-scoped generation prevents
    a delivery racing with this wait from being lost. ``suspend()`` pauses the
    step until an external resolution writes the next journal facts.
    """

    kind: str
    output: Any = None
    output_present: bool = True
    outcome: str = ""
    until: datetime | None = None
    resume_state: dict[str, Any] | None = None
    decisions: tuple[DecisionSpec, ...] = ()
    waiting_kind: Literal["scheduled", "approval", "external"] | str = ""
    artifacts_present: bool = False
    artifacts: tuple[ArtifactSpec, ...] = ()
    error: str | None = None
    stacktrace: str | None = None

    def to_attempt_result(self) -> AttemptResult:
        """Convert the implementation result into the retained closed envelope."""

        kind = AttemptResultKind(self.kind)
        checkpoint = JsonPresence(self.resume_state is not None, self.resume_state)
        return AttemptResult(
            kind=kind,
            output_present=kind == AttemptResultKind.DONE and self.output_present,
            output=self.output if kind == AttemptResultKind.DONE and self.output_present else None,
            checkpoint_present=checkpoint.present,
            checkpoint=checkpoint.value,
            outcome=self.outcome,
            error=self.error,
            stacktrace=self.stacktrace,
            waiting_kind=self.waiting_kind,
            requested_until=self.until,
            decisions=self.decisions,
            artifacts_present=self.artifacts_present,
            artifacts=self.artifacts,
        )

    @classmethod
    def done(
        cls,
        output: Any = None,
        outcome: str = "",
        *,
        output_present: bool = True,
        artifacts: tuple[ArtifactSpec, ...] | list[ArtifactSpec] | None = None,
    ) -> Self:
        """Return a completed step result."""

        return cls(
            kind="done",
            output=output,
            output_present=output_present,
            outcome=outcome,
            artifacts_present=artifacts is not None,
            artifacts=tuple(artifacts or ()),
        )

    @classmethod
    def failed(
        cls,
        error: str,
        *,
        checkpoint: dict[str, Any] | None = None,
        outcome: str = "failed",
        artifacts: tuple[ArtifactSpec, ...] | list[ArtifactSpec] | None = None,
    ) -> Self:
        """Return a retained operation failure with optional recovery evidence."""

        if not error:
            raise ValueError("StepResult.failed requires an error.")
        return cls(
            kind="error",
            error=error,
            outcome=outcome,
            resume_state=checkpoint,
            artifacts_present=artifacts is not None,
            artifacts=tuple(artifacts or ()),
        )

    @classmethod
    def wait(
        cls,
        *,
        until: datetime | None = None,
        resume_state: dict[str, Any] | None = None,
        waiting_kind: Literal["scheduled", "approval", "external"] = "scheduled",
        artifacts: tuple[ArtifactSpec, ...] | list[ArtifactSpec] | None = None,
    ) -> Self:
        """Return a durable wait result."""

        if until is None:
            raise ValueError("StepResult.wait requires until.")
        return cls(
            kind="wait",
            until=until,
            resume_state=resume_state,
            waiting_kind=waiting_kind,
            artifacts_present=artifacts is not None,
            artifacts=tuple(artifacts or ()),
        )

    @classmethod
    def suspend(
        cls,
        *,
        resume_state: dict[str, Any] | None = None,
        decisions: list[DecisionSpec] | tuple[DecisionSpec, ...] = (),
        waiting_kind: Literal["scheduled", "approval", "external"] | None = None,
    ) -> Self:
        """Return a suspended step result."""

        decisions_tuple = tuple(decisions)
        resolved_kind = waiting_kind or ("approval" if decisions_tuple else "external")
        return cls(
            kind="suspend",
            resume_state=resume_state,
            decisions=decisions_tuple,
            waiting_kind=resolved_kind,
        )


class StepImpl(ImplBase):
    """Base class for registry-selected workflow step implementations."""

    description: ClassVar[str] = ""
    selectable: ClassVar[bool] = True
    input_model: ClassVar[type[BaseModel] | None] = None
    output_model: ClassVar[type[BaseModel] | None] = None
    outcomes: ClassVar[tuple[StepOutcome, ...]] = ()
    effect: ClassVar[StepEffect] = StepEffect.UNKNOWN
    execution_mode: ClassVar[StepExecutionMode] = StepExecutionMode.STANDARD
    effect_description: ClassVar[str] = ""
    idempotent: ClassVar[bool | None] = None
    subject_declaration: ClassVar[str] = ""
    deterministic: ClassVar[bool] = True
    decision_schema: ClassVar[type[Any] | None] = None
    map_body_operation: ClassVar[bool] = False
    replay_mode: ClassVar[RecoveryMode | None] = None

    @classmethod
    def validate_input(cls, value: Any) -> Any:
        """Parse one retained JSON value through the operation's input contract."""

        if cls.input_model is None:
            raise ImproperlyConfigured(f"{cls.__name__} does not declare an input model.")
        return validate_json_value(cls.input_model.model_validate_json, value)

    @classmethod
    def recovery_capability(cls, *, attempt: Any) -> RecoveryCapability:
        """Return only the replay mode explicitly declared by this operation."""

        del attempt
        if cls.replay_mode is not None:
            return RecoveryCapability(mode=cls.replay_mode)
        return RecoveryCapability(mode=None, unavailable_reason="This operation does not support recovery.")

    @classmethod
    def external_operation_policy(cls, *, attempt: Any) -> ExternalOperationPolicy:
        """Report a configured provider's proven retry/reconciliation capability.

        ``idempotent`` authoring metadata alone is not a provider guarantee.
        An operation must override this method using its actual adapter contract.
        """

        del attempt
        return ExternalOperationPolicy.UNSUPPORTED

    def run_recovery(
        self,
        step_run: Any,
        *,
        now: datetime,
        source_attempt: Any,
        mode: RecoveryMode,
    ) -> StepResult:
        """Run one admitted recovery through the operation-owned safe behavior."""

        del source_attempt
        if mode is RecoveryMode.FRESH:
            return self.run(step_run, now=now)
        raise ValidationError({"recovery": "This operation does not implement reconciliation."})

    @classmethod
    def input_contract(cls) -> DataContract:
        """Return Pydantic's declared validation shape for operation input."""

        return model_data_contract(cls.input_model, mode="validation")

    @classmethod
    def output_contract(cls) -> DataContract:
        """Return Pydantic's declared serialization shape for operation output."""

        return model_data_contract(cls.output_model, mode="serialization")

    @classmethod
    def declared_output_contract(cls, config: Any) -> DataContract:
        """Return one node's output contract; built-ins may bind a publication."""

        del config
        return cls.output_contract()

    @classmethod
    def declared_outcomes(cls, config: Any) -> tuple[StepOutcome, ...]:
        """Return the outcomes validated for one configured graph node."""

        del config
        return cls.outcomes

    @classmethod
    def is_executable(cls, *, registered_key: str) -> bool:
        """Return whether the implementation supplies concrete runtime behavior."""

        del registered_key
        return cls.run is not StepImpl.run

    @classmethod
    def map_body_target(cls, config: Any) -> str | None:
        """Return the referenced Map body key when this operation expands one."""

        del config
        return None

    @classmethod
    def map_input_path(cls, config: Any) -> tuple[str, ...] | None:
        """Return the input path supplying this operation's Map items, when declared."""

        del config
        return None

    @classmethod
    def operation(cls, *, key: str) -> StepOperation:
        """Return this registered implementation's workflow-owned authoring contract."""

        cls._validate_operation(key=key)
        choice = cls.choice()
        return StepOperation(
            choice=replace(choice, key=key),
            description=cls.description,
            selectable=cls.selectable,
            input_contract=cls.input_contract(),
            output_contract=cls.output_contract(),
            outcomes=cls.outcomes,
            effect=cls.effect,
            effect_description=cls.effect_description,
            idempotent=cls.idempotent,
            subject_declaration=cls.subject_declaration,
            map_body_operation=cls.map_body_operation,
        )

    @classmethod
    def _validate_operation(cls, *, key: str) -> None:
        """Reject malformed authoring declarations at their registered key."""

        owner = f"Workflow step implementation {key!r}"
        if not isinstance(cls.effect, StepEffect):
            raise ImproperlyConfigured(f"{owner} declares invalid effect {cls.effect!r}.")
        if not isinstance(cls.execution_mode, StepExecutionMode):
            raise ImproperlyConfigured(f"{owner} declares invalid execution mode {cls.execution_mode!r}.")
        if cls.replay_mode is not None and not isinstance(cls.replay_mode, RecoveryMode):
            raise ImproperlyConfigured(f"{owner} declares invalid replay mode {cls.replay_mode!r}.")
        if cls.execution_mode is StepExecutionMode.EXTERNAL_OPERATION and cls.effect is not StepEffect.EXTERNAL:
            raise ImproperlyConfigured(f"{owner} external operation mode requires an external effect.")
        if not isinstance(cls.outcomes, tuple):
            raise ImproperlyConfigured(f"{owner} declares invalid outcomes {cls.outcomes!r}.")
        seen: set[str] = set()
        for outcome in cls.outcomes:
            if not isinstance(outcome, StepOutcome):
                raise ImproperlyConfigured(f"{owner} declares invalid outcome {outcome!r}.")
            if (
                not isinstance(outcome.key, str)
                or not isinstance(outcome.label, str)
                or not isinstance(outcome.description, str)
            ):
                raise ImproperlyConfigured(f"{owner} declares invalid outcome {outcome!r}.")
            if not outcome.key.strip() or not outcome.label.strip():
                raise ImproperlyConfigured(f"{owner} outcome keys and labels must be non-blank.")
            try:
                _OUTCOME_KEY_FIELD.run_validators(outcome.key)
            except ValidationError as error:
                raise ImproperlyConfigured(f"{owner} declares invalid outcome key {outcome.key!r}.") from error
            if outcome.key in seen:
                raise ImproperlyConfigured(f"{owner} declares duplicate outcome key {outcome.key!r}.")
            seen.add(outcome.key)
        declaration = cls.subject_declaration
        if not isinstance(declaration, str):
            raise ImproperlyConfigured(f"{owner} declares invalid subject label {declaration!r}.")
        if declaration and _MODEL_LABEL_RE.fullmatch(declaration) is None:
            raise ImproperlyConfigured(f"{owner} declares invalid subject label {declaration!r}.")

    @classmethod
    def validate_config(cls, config: Any) -> None:
        """Validate a step row's JSON config for this implementation."""

        if not isinstance(config, Mapping):
            raise ValidationError({"config": "Step config must be a JSON object."})
        if cls.config_model is not None:
            cls.normalize_config(config)

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        """Execute one step-run journal row."""

        del now
        raise NotImplementedError(f"{type(self).__name__}.run() is supplied by a runtime slice.")

    def heartbeat(self, step_run: Any, *, at: datetime | None = None, using: str | None = None) -> None:
        """Refresh ``step_run``'s heartbeat while a long implementation is running."""

        if str(getattr(step_run.status, "value", step_run.status)) != "started":
            return
        timestamp = at or timezone.now()
        alias = get_write_alias(type(step_run), using=using, instance=step_run)
        if step_run.current_attempt_id is not None:
            attempt_model = apps.get_model("workflows", "StepAttempt")
            with system_context(reason="workflows.step.heartbeat.load"):
                attempt: Any = related_on(step_run, "current_attempt", using=alias)
            attempt_model.objects.db_manager(alias).heartbeat(attempt.pk, lease_token=attempt.lease_token, at=timestamp)
            return
        step_run.heartbeat_at = timestamp
        with system_context(reason="workflows.step.heartbeat"):
            step_run.save(using=alias, update_fields=["heartbeat_at", "updated_at"])

    @contextmanager
    def heartbeat_during(self, step_run: Any, *, using: str | None = None) -> Iterator[None]:
        """Keep one admitted STANDARD attempt's lease alive during bounded I/O.

        ``heartbeat`` needs caller-driven pulses; the reaper only expires leases.
        Opaque synchronous I/O provides no mid-call hook, so a daemon worker
        composes the attempt manager's heartbeat on the captured write alias.
        Its connection closes in the worker's finally, and this context always
        stops and joins the worker before the invoking attempt can finalize.
        A rejected lease reports its retained revocation reason as a transient
        error; delivery finalization still owns the authoritative attempt fence.
        """

        alias = get_write_alias(type(step_run), using=using, instance=step_run)
        if self.execution_mode is not StepExecutionMode.STANDARD or connections[alias].in_atomic_block:
            raise RuntimeError("Lease heartbeat during I/O requires STANDARD execution outside a transaction.")
        with system_context(reason="workflows.step.heartbeat_during.load"):
            attempt: Any = related_on(step_run, "current_attempt", using=alias)
        if attempt is None:
            raise ValidationError({"attempt": "A lease heartbeat requires a retained attempt."})
        interval = heartbeat_timeout().total_seconds() / 3
        if interval <= 0:
            raise ImproperlyConfigured("The workflow heartbeat timeout must be positive.")
        stopped = Event()
        failures: list[Exception] = []

        def pulse(*, using: str) -> None:
            with system_context(reason="workflows.step.heartbeat_during"):
                accepted = type(attempt).objects.db_manager(using).heartbeat(
                    attempt.pk, lease_token=attempt.lease_token, at=timezone.now(),
                )
                if not accepted:
                    reason = (
                        type(attempt)
                        .objects.db_manager(using)
                        .values_list("lease_revocation_reason", flat=True)
                        .get(pk=attempt.pk)
                    )
                    raise TransientStepError(
                        f"Workflow attempt lease revoked: {LeaseRevocationReason(reason).value}."
                        if reason
                        else "The workflow attempt lease is no longer active."
                    )

        def keep_alive(*, using: str) -> None:
            try:
                while not stopped.wait(interval):
                    pulse(using=using)
            except Exception as error:  # noqa: BLE001 - relay the lease failure to the invoking worker.
                failures.append(error)
            finally:
                connections[using].close()

        pulse(using=alias)
        worker = Thread(target=keep_alive, kwargs={"using": alias}, name="workflow-heartbeat", daemon=True)
        worker.start()
        try:
            yield
        finally:
            stopped.set()
            worker.join()
        if failures:
            raise failures[0]


def heartbeat_timeout() -> timedelta:
    """Return the shared lease expiry used by keepalive and stale-attempt recovery."""

    configured = getattr(settings, "ANGEE_WORKFLOWS_HEARTBEAT_TIMEOUT", 300)
    if isinstance(configured, timedelta):
        return configured
    return timedelta(seconds=float(configured))


def retry_policy_from_config(config: Any) -> StepRetryPolicy:
    """Return the queue retry policy declared by ``config``."""

    if not isinstance(config, Mapping):
        raise ValidationError({"config": "Step config must be a JSON object."})
    retry = config.get("retry")
    if retry is None:
        return StepRetryPolicy()
    if not isinstance(retry, Mapping):
        raise ValidationError({"config": "Step retry must be a JSON object."})

    max_attempts = positive_int(retry.get("max_attempts", 1), "Step retry max_attempts")
    backoff = retry.get("backoff", {})
    if not isinstance(backoff, Mapping):
        raise ValidationError({"config": "Step retry backoff must be a JSON object."})
    return StepRetryPolicy(
        max_attempts=max_attempts,
        wait=non_negative_int(backoff.get("wait", 0), "Step retry backoff.wait"),
        linear_wait=non_negative_int(backoff.get("linear_wait", 0), "Step retry backoff.linear_wait"),
        exponential_wait=non_negative_int(backoff.get("exponential_wait", 0), "Step retry backoff.exponential_wait"),
    )


def validate_retry_config(config: Any) -> None:
    """Validate the common per-step retry block."""

    retry_policy_from_config(config)


class CallWorkflow(StepImpl):
    """Call one exact published workflow and wait for its retained terminal result."""

    key = "call_workflow"
    label = "Call workflow"
    category = "Control"
    description = "Start one pinned child and route only after its terminal result is retained."
    effect = StepEffect.WRITE
    execution_mode = StepExecutionMode.DATABASE_COMMAND
    replay_mode = RecoveryMode.FRESH
    idempotent = True
    deterministic = False
    effect_description = "Child admission and the parent wait share one fenced transaction."
    config_model = CallWorkflowConfig
    outcomes = (
        StepOutcome("child_failed", "Child failed"),
        StepOutcome("child_canceled", "Child canceled"),
    )

    @classmethod
    def _publication(cls, public_id: str) -> Any:
        workflow_model = apps.get_model("workflows", "Workflow")
        publication = instance_from_public_id(
            workflow_model, public_id, queryset=system_queryset(workflow_model, lock=None)
        )
        if publication is None or publication.published_from_id is None or str(publication.status) != "published":
            raise ValidationError({"publication": "CallWorkflow requires an exact published workflow id."})
        return publication

    @classmethod
    def _workflow_for_start(cls, config: Mapping[str, Any], payload: Mapping[str, Any], *, using: str) -> Any:
        """Load one lineage head or exact version for manager-owned admission."""

        workflow_model = apps.get_model("workflows", "Workflow")
        workflow_key = config.get("workflow_key")
        if workflow_key:
            with system_context(reason="workflows.call.lineage"):
                head = (
                    system_queryset(workflow_model, using=using, lock=None)
                    .filter(published_from__isnull=True, key=workflow_key)
                    .first()
                )
            if head is None:
                raise ValidationError({"workflow_key": "CallWorkflow requires an existing workflow key."})
            return head
        selected = config.get("publication") or payload.get("publication")
        if not isinstance(selected, str):
            raise ValidationError({"publication": "CallWorkflow input must select a published workflow."})
        publication = instance_from_public_id(
            workflow_model,
            selected,
            queryset=system_queryset(workflow_model, using=using, lock=None),
        )
        if publication is None or publication.published_from_id is None:
            raise ValidationError({"publication": "CallWorkflow requires an exact workflow publication id."})
        return publication

    @classmethod
    def _input_schema(cls, publication: Any) -> dict[str, Any]:
        """Use the child's immutable public invocation schema."""

        return publication.input_schema

    @classmethod
    def validate_config(cls, config: Any) -> None:
        super().validate_config(config)
        normalized = cls.normalize_config(config)
        if normalized.get("publication"):
            cls._publication(normalized["publication"])

    @classmethod
    def _validate_publication_contract(cls, publication: Any, config: Mapping[str, Any]) -> None:
        """Check the manager-admitted publication against the declared stable contract."""

        if config.get("publication"):
            if public_id_for(type(publication), publication.pk) != config["publication"]:
                raise ValidationError({"publication": "Retained child publication differs from the static call."})
            return
        if config.get("workflow_key") and publication.key != config["workflow_key"]:
            raise ValidationError({"workflow_key": "Retained child belongs to a different workflow key."})
        actual_outcomes = {rule["outcome"] for rule in publication.result_rules} or {"completed"}
        if (
            not json_values_equal(cls._input_schema(publication), config["expected_input_schema"])
            or not json_values_equal(publication.output_schema, config["expected_output_schema"])
            or publication.subject_declaration != config["expected_subject"].strip().lower()
            or not actual_outcomes.issubset(set(config["expected_outcomes"]))
        ):
            raise ValidationError({"publication": "Selected publication does not satisfy the call contract."})

    @classmethod
    def declared_output_contract(cls, config: Any) -> DataContract:
        cls.validate_config(config)
        if config.get("publication"):
            return schema_data_contract(cls._publication(config["publication"]).output_schema)
        return schema_data_contract(config["expected_output_schema"])

    @classmethod
    def declared_outcomes(cls, config: Any) -> tuple[StepOutcome, ...]:
        cls.validate_config(config)
        if config.get("publication"):
            business = [rule["outcome"] for rule in cls._publication(config["publication"]).result_rules]
        else:
            business = config["expected_outcomes"]
        if not business:
            business = ["completed"]
        return (*tuple(StepOutcome(key, key.replace("_", " ").title()) for key in business), *cls.outcomes)

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        del now
        from angee.workflows import engine  # Runtime edge; the operation registry imports this module first.
        from angee.workflows.states import RunOrigin, RunStatus

        alias = get_write_alias(type(step_run), instance=step_run)
        config = type(self).normalize_config(step_run.step.config)
        payload = step_run.input
        if not isinstance(payload, Mapping):
            raise ValidationError({"input": "CallWorkflow input must be an object."})
        run_model = apps.get_model("workflows", "WorkflowRun")
        actor = step_run.run.execution_admission_actor(using=alias)
        workflow = type(self)._workflow_for_start(config, payload, using=alias)
        subject_spec = payload.get("subject", step_run.run.subject)
        if subject_spec is None or isinstance(subject_spec, models.Model):
            subject = subject_spec
        elif isinstance(subject_spec, Mapping):
            model_label, public_id = subject_spec.get("model"), subject_spec.get("id")
            if not isinstance(model_label, str) or not isinstance(public_id, str):
                raise ValidationError({"subject": "Child subject needs a model label and public id."})
            try:
                model = apps.get_model(model_label)
            except (LookupError, ValueError) as error:
                raise ValidationError({"subject": "Child subject model is not installed."}) from error
            scoped = read_scoped_queryset(model, actor, action="write")
            if scoped is not None:
                scoped = scoped.using(alias)
            subject = None if scoped is None else instance_from_public_id(model, public_id, queryset=scoped)
            if subject is None:
                raise ValidationError({"subject": "Child subject is unavailable to the execution actor."})
        else:
            raise ValidationError({"subject": "Child subject must be an exact record reference or null."})
        child = engine.start(
            workflow,
            subject,
            actor,
            parent_step_run=step_run,
            parent_relation="owned_call",
            origin=RunOrigin.WORKFLOW,
            using=alias,
            input=JsonPresence("input" in payload, payload.get("input")),
        )
        type(self)._validate_publication_contract(child.workflow, config)
        attempt_model = apps.get_model("workflows", "StepAttempt")
        attempt_model.objects.db_manager(alias).bind_call_child(
            step_run.current_attempt_id,
            lease_token=step_run.current_attempt.lease_token,
            child=child,
        )
        current = system_queryset(run_model, using=alias, lock=("self",)).get(pk=child.pk)
        if current.status not in RunStatus.TERMINAL:
            return StepResult.suspend(decisions=(), waiting_kind="external")
        if current.result is None:
            raise ValidationError({"result": "Terminal child has no retained workflow result."})
        if current.status == RunStatus.SUCCEEDED:
            declared = {rule["outcome"] for rule in child.workflow.result_rules} or {"completed"}
            if current.result.get("outcome") not in declared:
                raise ValidationError({"result": "Child produced an undeclared business outcome."})
            return StepResult.done(output=current.result["output"], outcome=current.result["outcome"])
        return StepResult.done(
            output_present=False,
            outcome="child_canceled" if current.status == RunStatus.CANCELED else "child_failed",
        )


class JoinContinuation(StepImpl):
    """Wait for one provenance-bound continuation and its accepted recovery."""

    key = "join_continuation"
    label = "Join continuation"
    category = "Control"
    description = "Join one admitted continuation, waking on delivery and reconciling on a bounded timer."
    effect = StepEffect.READ
    replay_mode = RecoveryMode.FRESH
    idempotent = True
    deterministic = False
    effect_description = "Reads one admitted continuation run and subscribes to its terminal delivery."
    config_model = JoinContinuationConfig
    outcomes = (StepOutcome("child_failed", "Child failed"),)

    @classmethod
    def declared_output_contract(cls, config: Any) -> DataContract:
        normalized = cls.normalize_config(config)
        return schema_data_contract(normalized["expected_output_schema"])

    @classmethod
    def declared_outcomes(cls, config: Any) -> tuple[StepOutcome, ...]:
        normalized = cls.normalize_config(config)
        business = tuple(
            StepOutcome(key, key.replace("_", " ").title())
            for key in normalized["expected_outcomes"]
            if key != "child_failed"
        )
        return (*business, *cls.outcomes)

    @staticmethod
    def _validate_child_contract(child: Any, config: Mapping[str, Any]) -> None:
        publication = child.workflow
        actual_outcomes = {rule["outcome"] for rule in publication.result_rules} or {"completed"}
        if (
            not json_values_equal(publication.output_schema, config["expected_output_schema"])
            or publication.subject_declaration != config["expected_subject"].strip().lower()
            or not actual_outcomes.issubset(set(config["expected_outcomes"]))
        ):
            raise ValidationError({"child": "Continuation child does not satisfy the declared join contract."})

    def run(self, step_run: Any, *, now: datetime) -> StepResult:

        alias = get_write_alias(type(step_run), instance=step_run)
        config = type(self).normalize_config(step_run.step.config)
        actor = step_run.run.execution_admission_actor(using=alias)
        child_id = json_value_at_path(step_run.input, config["child_id_path"], field="child")
        if not isinstance(child_id, str) or not child_id:
            raise ValidationError({"child": "Continuation child identity must be a public id."})
        child, completion = apps.get_model("workflows", "StepAttempt").objects.db_manager(alias).join_continuation(
            step_run.pk,
            lease_token=step_run.current_attempt.lease_token,
            child_id=child_id,
            expected_starter_class=config["expected_starter_class"],
            actor=actor,
        )
        type(self)._validate_child_contract(child, config)
        if completion is None:
            state = step_run.resume_state if isinstance(step_run.resume_state, Mapping) else {}
            previous = state.get("join_reconcile_after")
            delay = config["reconcile_after"] if type(previous) is not int else min(previous * 2, 3600)
            return StepResult.wait(
                until=now + timedelta(seconds=delay),
                waiting_kind="external",
                resume_state={"join_reconcile_after": delay},
            )
        if completion.status in {"failed", "canceled"}:
            return StepResult.done(output_present=False, outcome="child_failed")
        result = completion.result
        if (
            not isinstance(result, Mapping)
            or result.get("status") != "succeeded"
            or result.get("outcome") not in config["expected_outcomes"]
            or "output" not in result
        ):
            raise ValidationError({"child": "Continuation completion does not satisfy the declared join result."})
        errors = list(Draft202012Validator(config["expected_output_schema"]).iter_errors(result["output"]))
        if errors:
            raise ValidationError({"child": "Continuation output does not satisfy the declared join schema."})
        return StepResult.done(output=result["output"], outcome=result["outcome"])


class EmitStep(StepImpl):
    """Project a value and bind its explicit result artifacts."""

    key = "emit"
    label = "Emit result"
    category = "Control"
    description = "Emit a schema-checked projection with explicit artifact bindings."
    effect = StepEffect.READ
    replay_mode = RecoveryMode.FRESH
    idempotent = True
    deterministic = False
    effect_description = "Reads declared artifact records without changing domain state."
    config_model = EmitConfig

    @classmethod
    def declared_output_contract(cls, config: Any) -> DataContract:
        normalized = cls.normalize_config(config)
        return schema_data_contract(normalized["output_schema"])

    @classmethod
    def declared_outcomes(cls, config: Any) -> tuple[StepOutcome, ...]:
        outcome = cls.normalize_config(config)["outcome"]
        return (StepOutcome(outcome, outcome.replace("_", " ").title()),)

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        del now

        alias = get_write_alias(type(step_run), instance=step_run)
        config = type(self).normalize_config(step_run.step.config)
        output = step_run.input
        if list(Draft202012Validator(config["output_schema"]).iter_errors(output)):
            raise ValidationError({"output": "Emit input does not satisfy its declared projection contract."})
        actor = step_run.run.execution_admission_actor(using=alias)
        artifacts: list[ArtifactSpec] = []
        for binding in config["artifacts"]:
            model = apps.get_model(binding["model"])
            public_id = json_value_at_path(output, tuple(binding["id_path"]), field="artifacts")
            if not isinstance(public_id, str):
                raise ValidationError({"artifacts": "Artifact id paths must select public-id strings."})
            queryset = read_scoped_queryset(model, actor, action="read")
            if queryset is not None:
                queryset = queryset.using(alias)
            target = None if queryset is None else instance_from_public_id(model, public_id, queryset=queryset)
            if target is None:
                raise ValidationError({"artifacts": "An emitted artifact is unavailable to the workflow actor."})
            artifacts.append(ArtifactSpec(target, binding["label"]))
        return StepResult.done(
            output=output,
            outcome=config["outcome"],
            artifacts=artifacts,
        )


class WaitStep(StepImpl):
    """Built-in timer wait step."""

    key = "wait"
    label = "Wait"
    category = "Control"
    description = "Wait until the configured timestamp before routing through the timer outcome."
    outcomes = (StepOutcome("timer", "Timer elapsed"),)
    effect = StepEffect.NONE
    effect_description = "Does not read or change the workflow subject."
    idempotent = True
    config_model = WaitConfig

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        """Return done once the timer or event condition has arrived."""

        if str(getattr(step_run.status, "value", step_run.status)) == "waiting":
            if step_run.wait_until is not None and step_run.wait_until <= now:
                return StepResult.done(output=step_run.output, outcome="timer")
            return StepResult.wait(
                until=step_run.wait_until,
                resume_state=dict(step_run.resume_state),
            )

        config = dict(step_run.step.config)
        until = _config_until(config.get("until")) if "until" in config else None
        if until is not None and until <= now:
            return StepResult.done(output={}, outcome="timer")
        return StepResult.wait(until=until, resume_state={"config": config})


class GateStep(StepImpl):
    """The single built-in owner of static, bound, and resumable review gates."""

    key = "gate"
    label = "Gate"
    category = "Control"
    description = "Suspend execution until the configured approval policy resolves."
    outcomes = tuple(
        StepOutcome(key, label)
        for key, label in (
            ("completed", "Completed"),
            ("rejected", "Rejected"),
            ("escalated", "Escalated"),
            ("expired", "Expired"),
        )
    )
    effect = StepEffect.NONE
    output_model: ClassVar[type[BaseModel] | None] = DecisionGateOutput
    effect_description = "Creates workflow decision journals without changing the workflow subject."
    config_model: ClassVar[type[BaseModel] | None] = GateConfig

    @classmethod
    def validate_config(cls, config: Any) -> None:
        """Reject hand-authored static unions at the workflow definition boundary."""

        if not isinstance(config, Mapping):
            raise ValidationError({"config": "Gate config must be an object."})
        decision_schema = config.get("decision_schema", {})
        try:
            bound_schema = is_gate_binding_mapping(decision_schema)
        except ValueError as error:
            raise ValidationError({"decision_schema": str(error)}) from error
        if isinstance(decision_schema, Mapping) and not bound_schema and "oneOf" in decision_schema:
            raise ValidationError({"decision_schema": "Static gates declare actions, not hand-written oneOf."})
        slots = config.get("slots")
        if isinstance(slots, list) and any(
            isinstance(slot, Mapping)
            and isinstance(slot.get("decision_schema"), Mapping)
            and "oneOf" in slot["decision_schema"]
            for slot in slots
        ):
            raise ValidationError({"slots": "Static gate slots cannot declare hand-written oneOf schemas."})
        super().validate_config(config)

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        """Resolve bound declarations and suspend or complete a resumed gate."""

        del now
        resumed = type(self).resumption(step_run)
        if resumed is not None:
            return StepResult.done(output=resumed.resolutions, outcome=resumed.outcome)
        return type(self).gate_result(step_run, config=type(self).gate_config(step_run))

    @classmethod
    def gate_config(cls, step_run: Any) -> Mapping[str, Any]:
        """Return the authored config; domain adapters may derive it from admitted input."""

        config = step_run.step.config
        if not isinstance(config, Mapping):
            raise ValidationError({"config": "Gate config must be an object."})
        return config

    @classmethod
    def gate_result(
        cls,
        step_run: Any,
        *,
        config: Mapping[str, Any],
        retained_state: Mapping[str, Any] | None = None,
    ) -> StepResult:
        """Return the canonical clean completion or retained Decision suspension."""

        resolved = cls._resolved_config(step_run, config)
        if resolved is None:
            output = DecisionGateOutput(resolutions=(), outcome="completed").model_dump(mode="json")
            return StepResult.done(output=output, outcome="completed")
        state: dict[str, Any] = {"gate": {"policy": resolved["policy"]}}
        if retained_state:
            state["state"] = copy.deepcopy(dict(retained_state))
        if resolved["resume"]:
            state["_resume_after_decisions"] = True
        return StepResult.suspend(
            resume_state=state,
            decisions=_decision_specs_from_config(resolved),
        )

    @classmethod
    def resumption(cls, step_run: Any) -> GateResumption | None:
        """Return the exact settled slots of this step's current resumable suspension."""

        state = getattr(step_run, "resume_state", {})
        if not isinstance(state, Mapping) or not state.get("_resume_after_decisions"):
            return None
        outcome = state.get("_decision_outcome")
        resolutions = state.get("_decision_resolutions")
        decision_ids = state.get("_decision_ids")
        if outcome is None and resolutions is None:
            return None
        if (
            not isinstance(outcome, str)
            or not isinstance(resolutions, dict)
            or not isinstance(decision_ids, list)
            or any(type(decision_id) is not int for decision_id in decision_ids)
        ):
            raise ValidationError({"gate": "Resumable gate state is incomplete."})
        decisions = {
            decision.pk: decision
            for decision in step_run.decisions.filter(pk__in=decision_ids).order_by("declaration_index", "pk")
        }
        if set(decisions) != set(decision_ids):
            raise ValidationError({"gate": "Resumable gate Decisions are unavailable."})
        slots = tuple(
            {
                **{
                    key: copy.deepcopy(value)
                    for key, value in dict(decisions[decision_id].payload or {}).items()
                    if key != "facts"
                },
                "approved": decisions[decision_id].verdict == "completed",
                "verdict": str(decisions[decision_id].verdict),
                "resolution": copy.deepcopy(dict(decisions[decision_id].resolution or {})),
            }
            for decision_id in decision_ids
        )
        retained = state.get("state")
        if retained is not None and not isinstance(retained, dict):
            raise ValidationError({"gate": "Resumable gate retained state must be an object."})
        return GateResumption(
            outcome=outcome,
            resolutions=copy.deepcopy(resolutions),
            state=copy.deepcopy(retained or {}),
            slots=slots,
        )

    @classmethod
    def _resolved_config(
        cls,
        step_run: Any,
        config: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Short-circuit a clean input or validate one fully resolved gate config."""

        resolved = dict(config)
        clean = cls._bound_value(step_run, resolved.get("clean", False), field="clean")
        if type(clean) is not bool:
            raise ValidationError({"clean": "Gate clean bindings must resolve to a boolean."})
        if clean:
            return None
        resolved["clean"] = False
        for name in ("slots", "payload", "decision_schema", "targets", "record_access"):
            if name in resolved:
                resolved[name] = cls._bound_value(step_run, resolved[name], field=name)
        return GateStep.normalize_config(resolved)

    @staticmethod
    def _bound_value(step_run: Any, value: Any, *, field: str) -> Any:
        """Evaluate one binding tree from the invocation's retained admitted input."""

        try:
            is_binding = is_gate_binding_mapping(value)
        except ValueError as error:
            raise ValidationError({field: str(error)}) from error
        if not is_binding:
            return value
        attempt = getattr(step_run, "current_attempt", None)
        present = bool(attempt is not None and attempt.input_present)
        admitted = attempt.input if attempt is not None else step_run.input
        context = BindingContext(
            workflow_input=SourceValue(
                JsonPresence(present, admitted),
                {"kind": "attempt_input", "step_run_id": getattr(step_run, "pk", None)},
            ),
            step_outputs={},
            map_item=UnavailableSource(
                "source_unavailable",
                "Gate config bindings read the admitted step input; bind other sources into it first.",
                {"kind": "map_item"},
            ),
        )
        evaluation = evaluate_binding(parse_binding(value), context)
        if evaluation.diagnostics or evaluation.value is None or not evaluation.value.present:
            messages = "; ".join(item.message for item in evaluation.diagnostics)
            raise ValidationError({field: messages or "Gate binding did not produce a value."})
        return evaluation.value.value


class DecisionApplyStep(StepImpl):
    """Dispatch a predecessor Decision identity to a concrete domain command.

    The command owns ancestry-before-record locking, persisted binding and
    verdict checks, actor permission, expected state, and database idempotence.
    Timer resolutions carry no human actor; commands decide their domain meaning.
    """

    deterministic = False
    gate_step_class: ClassVar[type[GateStep]] = GateStep

    @classmethod
    def _validate_operation(cls, *, key: str) -> None:
        """Require every concrete apply adapter to publish its complete contract."""

        super()._validate_operation(key=key)
        if cls is DecisionApplyStep:
            return
        if (
            cls.input_model is None
            or cls.output_model is None
            or not cls.outcomes
            or "effect" not in cls.__dict__
            or "execution_mode" not in cls.__dict__
            or "idempotent" not in cls.__dict__
        ):
            raise ImproperlyConfigured(
                f"Decision apply implementation {key!r} must declare input/output models, outcomes, "
                "effect, execution_mode and idempotency."
            )

    def invoke_command(self, step_run: Any, *, decision_id: int, actor: Any, now: datetime) -> StepResult:
        """Dispatch the Decision identity and actor to the domain manager."""

        raise NotImplementedError

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        """Load the predecessor identity and preserve the command's typed result."""

        alias = get_write_alias(type(step_run), instance=step_run)
        decision = apps.get_model("workflows", "Decision").objects.db_manager(alias).predecessor_decision(
            step_run, type(self).gate_step_class
        )
        actor = decision.resolution_actor_subject()
        result = self.invoke_command(step_run, decision_id=decision.pk, actor=actor, now=now)
        if not isinstance(result, StepResult):
            raise TypeError("Decision apply manager commands must return StepResult.")
        if result.kind == "done":
            if result.outcome not in {outcome.key for outcome in type(self).outcomes}:
                raise ValidationError({"outcome": "Decision apply returned an undeclared outcome."})
            if result.output_present:
                output_model = type(self).output_model
                assert output_model is not None
                validate_json_value(output_model.model_validate_json, result.output)
        return result


class MapStep(StepImpl):
    """Built-in control step that maps one target step over a journaled item list."""

    key = "map"
    label = "Map"
    category = "Control"
    description = "Run one target step for each configured item and aggregate the child results."
    outcomes = (
        StepOutcome("succeeded", "Succeeded"),
        StepOutcome("failed", "Failed"),
    )
    effect_description = "Effects and idempotency depend on the configured target operation."
    config_model = MapConfig
    map_body_operation = True

    @classmethod
    def is_executable(cls, *, registered_key: str) -> bool:
        """Match the exact operation key selected by the engine's Map query."""

        return registered_key == MapStep.key

    @classmethod
    def map_body_target(cls, config: Any) -> str | None:
        """Return the declared body step key without resolving persistence."""

        if not isinstance(config, Mapping):
            return None
        target = config.get("target_step")
        return str(target) if isinstance(target, str) and target else None

    @classmethod
    def map_input_path(cls, config: Any) -> tuple[str, ...] | None:
        """Expose the native items expression's input path for graph contract projection."""

        expression = config.get("items") if isinstance(config, Mapping) else None
        if not isinstance(expression, str):
            return None
        try:
            root, path = map_items_expression_path(expression)
        except ValueError:
            return None
        return path if root == "input" else None

    @classmethod
    def engine_expanded_filter(cls) -> dict[str, Any]:
        """Return the ORM predicate for map parent rows the engine expands."""

        return {"step__step_class": cls.key, "map_index": -1}

    @classmethod
    def target_step(cls, step_run: Any, *, using: str | None = None) -> Any:
        """Return the configured target step for one map parent row."""

        alias = get_write_alias(type(step_run), using=using, instance=step_run)
        step_run = (
            type(step_run)._base_manager.using(alias).select_related("step", "run").get(pk=step_run.pk)
        )

        config = cls.config_mapping(step_run)
        key = str(config.get("target_step") or "")
        if not key:
            raise ValidationError({"config": "Map steps require target_step."})
        try:
            step_model = step_run._meta.get_field("step").remote_field.model
            return system_queryset(step_model, using=alias).get(workflow_id=step_run.run.workflow_id, key=key)
        except ObjectDoesNotExist as error:
            raise ValidationError({"config": f"Map target step {key!r} does not exist."}) from error

    @classmethod
    def items(
        cls,
        step_run: Any,
        *,
        input: JsonPresence,
        using: str | None = None,
    ) -> list[Any]:
        """Return the item list resolved from this map step's config."""

        alias = get_write_alias(type(step_run), using=using, instance=step_run)
        step_run = (
            type(step_run)._base_manager.using(alias).select_related("step", "run").get(pk=step_run.pk)
        )

        expression = cls.config_mapping(step_run).get("items")
        value = cls.expression_value(expression, step_run, input=input)
        if not isinstance(value, list):
            raise ValidationError({"config": "Map items expression must resolve to a list."})
        return list(value)

    @classmethod
    def policy_passes(cls, config: Any, output: Mapping[str, Any]) -> bool:
        """Return whether aggregate map output satisfies ``config``."""

        total = int(output["total"])
        successes = int(output["successes"])
        failures = int(output["failures"])
        mapping = config if isinstance(config, Mapping) else {}
        if bool(mapping.get("all_must_succeed", False)):
            return failures == 0 and successes == total
        ratio = optional_number(mapping.get("min_success_ratio"), "Map min_success_ratio")
        if ratio is None:
            return failures == 0 and successes == total
        if total == 0:
            return ratio <= 0
        return successes / total >= ratio

    @classmethod
    def config_mapping(cls, step_run: Any) -> Mapping[str, Any]:
        """Return the map config as a mapping."""

        config = step_run.step.config
        return config if isinstance(config, Mapping) else {}

    @classmethod
    def expression_value(
        cls,
        expression: Any,
        step_run: Any,
        *,
        input: JsonPresence,
    ) -> Any:
        """Resolve a map ``items`` expression against subject, run, or input."""

        if isinstance(expression, list):
            return expression
        if not isinstance(expression, str) or not expression:
            raise ValidationError({"config": "Map steps require an items expression."})
        try:
            root, path = map_items_expression_path(expression)
        except ValueError as error:
            raise ValidationError({"config": str(error)}) from error
        if root == "subject":
            value = step_run.run.subject
        elif root == "run":
            value = step_run.run
        elif root == "input":
            value = input.value if input.present else None
        for part in path:
            value = cls.lookup(value, part)
        return value

    @staticmethod
    def lookup(value: Any, key: str) -> Any:
        """Read one expression path segment from a mapping or object."""

        if isinstance(value, Mapping):
            return value.get(key)
        return getattr(value, key)


def _config_until(value: Any) -> datetime | None:
    """Return an aware datetime parsed from a wait config value."""

    return _config_datetime(value)


def _config_datetime(value: Any) -> datetime | None:
    """Return an aware datetime parsed from a config value."""

    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = parse_datetime(str(value))
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def positive_int(value: Any, label: str) -> int:
    """Return ``value`` as a positive integer or raise a config error."""

    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValidationError({"config": f"{label} must be an integer."}) from error
    if parsed < 1:
        raise ValidationError({"config": f"{label} must be positive."})
    return parsed


def non_negative_int(value: Any, label: str) -> int:
    """Return ``value`` as a non-negative integer or raise a config error."""

    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValidationError({"config": f"{label} must be an integer."}) from error
    if parsed < 0:
        raise ValidationError({"config": f"{label} must be non-negative."})
    return parsed


def optional_number(value: Any, label: str) -> float | None:
    """Return ``value`` as a number when present, or raise a config error."""

    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValidationError({"config": f"{label} must be a number."})
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise ValidationError({"config": f"{label} must be a number."}) from error


def optional_positive_int(value: Any) -> int | None:
    """Leniently return ``value`` as a positive integer when possible."""

    parsed = optional_non_negative_int(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def optional_non_negative_int(value: Any) -> int | None:
    """Leniently return ``value`` as a non-negative integer when possible."""

    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except TypeError, ValueError:
        return None
    return parsed if parsed >= 0 else None


def _decision_specs_from_config(config: Mapping[str, Any]) -> tuple[DecisionSpec, ...]:
    """Return gate decision specs from declarative config."""

    gate_config = GateConfig.model_validate(config)
    slots = config.get("slots")
    if not isinstance(slots, list) or not slots:
        raise ValidationError({"slots": "Gate slots must resolve to a non-empty list."})
    action = str(config.get("action", "") or "")
    payload = dict(config.get("payload") or {})
    requester = str(config.get("requester", "") or "")
    escalation = tuple(str(subject) for subject in config.get("escalation", ()) if str(subject))
    max_attempts = config.get("max_attempts")
    parsed_max_attempts = None if max_attempts in (None, "") else int(str(max_attempts))
    expires_at = _config_datetime(config.get("expires_at"))
    escalate_at = _config_datetime(config.get("escalate_at"))
    decision_schema = gate_config.admission_decision_schema()
    targets = config.get("targets") or []
    if not isinstance(targets, list) or len(targets) not in {0, 1, len(slots)}:
        raise ValidationError({"targets": "Gate targets must be empty, shared once, or aligned with every slot."})
    shared_record_access = config.get("record_access") or []
    if not isinstance(shared_record_access, list):
        raise ValidationError({"record_access": "Gate record_access must resolve to a list."})
    specs: list[DecisionSpec] = []
    for index, slot in enumerate(slots):
        if not isinstance(slot, Mapping):
            raise ValidationError({"slots": "Every gate slot must be an object."})
        target = slot.get("target")
        if target is None and targets:
            target = targets[0] if len(targets) == 1 else targets[index]
        if target is None:
            target = {}
        if not isinstance(target, Mapping):
            raise ValidationError({"targets": "Every gate target must be an object."})
        slot_record_access = slot.get("record_access")
        record_access = shared_record_access if slot_record_access is None else slot_record_access
        if not isinstance(record_access, list):
            raise ValidationError({"record_access": "Every gate record_access value must be a list."})
        specs.append(
            DecisionSpec(
                assignees=_slot_assignees(slot),
                action=str(slot.get("action") or action),
                payload=dict(payload if slot.get("payload") is None else slot["payload"]),
                priority=int(slot.get("priority", index)),
                requester=str(slot.get("requester", requester) or requester),
                escalation=tuple(
                    str(subject)
                    for subject in (escalation if slot.get("escalation") is None else slot["escalation"])
                    if str(subject)
                ),
                max_attempts=parsed_max_attempts,
                expires_at=expires_at,
                escalate_at=escalate_at,
                decision_schema=dict(
                    decision_schema if slot.get("decision_schema") is None else slot["decision_schema"]
                ),
                target_model=str(target.get("model") or ""),
                target_id=str(target.get("id") or ""),
                target_tab=str(target.get("tab") or ""),
                record_access=tuple(DecisionRecordAccess.model_validate(item) for item in record_access),
            )
        )
    return tuple(specs)


def _slot_assignees(slot: Mapping[str, Any]) -> tuple[str, ...]:
    """Return normalized assignee subject refs for one gate slot."""

    raw = slot.get("assignees", slot.get("assignee", ()))
    if isinstance(raw, str):
        return (raw,) if raw else ()
    if raw is None:
        return ()
    return tuple(str(subject) for subject in raw if str(subject))
