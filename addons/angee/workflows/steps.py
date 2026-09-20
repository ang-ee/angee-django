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
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Any, ClassVar, Literal, Self

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist, ValidationError
from django.db import models
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from jsonschema import Draft202012Validator
from pydantic import BaseModel
from rebac import system_context

from angee.base.identity import instance_from_public_id
from angee.base.impl import ImplBase, ImplChoice
from angee.base.scoping import read_scoped_queryset, system_queryset
from angee.workflows.attempts import (
    ArtifactSpec,
    AttemptResult,
    AttemptResultKind,
    DecisionGateOutput,
    DecisionRecordAccess,
    DecisionResolution,
    DecisionSpec,
    ExternalOperationPolicy,
    JsonPresence,
    RecoveryCapability,
    RecoveryMode,
    json_values_equal,
)
from angee.workflows.bindings import (
    BindingContext,
    SourceValue,
    UnavailableSource,
    evaluate_binding,
    parse_binding,
)
from angee.workflows.configs import (
    GateConfig,
    MapConfig,
    WaitConfig,
    is_gate_binding_mapping,
    map_items_expression_path,
)
from angee.workflows.data_contracts import DataContract, model_data_contract, schema_data_contract

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

    @classmethod
    def validate_input(cls, value: Any) -> Any:
        """Parse one retained JSON value through the operation's input contract."""

        if cls.input_model is None:
            raise ImproperlyConfigured(f"{cls.__name__} does not declare an input model.")
        return cls.input_model.model_validate_json(json.dumps(value, allow_nan=False))

    @classmethod
    def recovery_capability(cls, *, attempt: Any) -> RecoveryCapability:
        """Return the recovery mode proven by the operation's execution boundary.

        A failed database command has no committed domain effect: the fenced
        attempt manager invokes the command and finalizes its result inside one
        transaction, and records any failure only after that transaction rolls
        back. A committed command already owns a successful retained result and
        cannot be admitted as failed recovery evidence. Fresh execution is
        therefore native for this mode; narrower operation-specific policies
        can still override it.
        """

        del attempt
        if cls.execution_mode is StepExecutionMode.DATABASE_COMMAND:
            return RecoveryCapability(mode=RecoveryMode.FRESH)
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

    def heartbeat(self, step_run: Any, *, at: datetime | None = None) -> None:
        """Refresh ``step_run``'s heartbeat while a long implementation is running."""

        if str(getattr(step_run.status, "value", step_run.status)) != "started":
            return
        timestamp = at or timezone.now()
        if step_run.current_attempt_id is not None:
            attempt_model = apps.get_model("workflows", "StepAttempt")
            with system_context(reason="workflows.step.heartbeat.load"):
                attempt = attempt_model.objects.get(pk=step_run.current_attempt_id)
            attempt_model.objects.heartbeat(attempt.pk, lease_token=attempt.lease_token, at=timestamp)
            return
        step_run.heartbeat_at = timestamp
        with system_context(reason="workflows.step.heartbeat"):
            step_run.save(update_fields=["heartbeat_at", "updated_at"])


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


class HandlerStep(StepImpl):
    """Abstract activity step base registered as the built-in ``handler`` key."""

    key = "handler"
    label = "Handler"
    category = "Activity"
    description = "Legacy abstract activity handler retained for stored workflow compatibility."
    selectable = False
    deterministic = False


class CallWorkflow(StepImpl):
    """Call one exact published workflow and wait for its retained terminal result."""

    key = "call_workflow"
    label = "Call workflow"
    category = "Control"
    description = "Start one pinned child and route only after its terminal result is retained."
    effect = StepEffect.WRITE
    execution_mode = StepExecutionMode.DATABASE_COMMAND
    idempotent = True
    deterministic = False
    effect_description = "Child admission and the parent wait share one fenced transaction."
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
    def selected_publication_id(cls, *, config: Any, payload: Any) -> str:
        """Return the exact publication selected by one retained call input."""

        if not isinstance(config, Mapping):
            raise ValidationError({"config": "CallWorkflow config must be an object."})
        if not isinstance(payload, Mapping):
            raise ValidationError({"input": "CallWorkflow input must be an object."})
        selected_id = config.get("publication") or payload.get("publication")
        if not isinstance(selected_id, str):
            raise ValidationError({"publication": "CallWorkflow input must select a published workflow."})
        if config.get("publication") and payload.get("publication") not in (
            None,
            selected_id,
        ):
            raise ValidationError({"publication": "Call input cannot replace its declared static publication."})
        return selected_id

    @classmethod
    def _input_schema(cls, publication: Any) -> dict[str, Any]:
        """Use the child's immutable public invocation schema."""

        return publication.input_schema

    @classmethod
    def validate_config(cls, config: Any) -> None:
        if not isinstance(config, Mapping):
            raise ValidationError({"config": "CallWorkflow config must be an object."})
        static = config.get("publication")
        if static:
            if not isinstance(static, str):
                raise ValidationError({"publication": "Static publication must be a public id."})
            cls._publication(static)
            if any(
                key in config
                for key in ("expected_input_schema", "expected_output_schema", "expected_subject", "expected_outcomes")
            ):
                raise ValidationError({"config": "A static call derives its contract from its publication."})
        else:
            input_schema = config.get("expected_input_schema")
            schema = config.get("expected_output_schema")
            outcomes = config.get("expected_outcomes")
            subject = config.get("expected_subject")
            if (
                not isinstance(input_schema, Mapping)
                or not isinstance(schema, Mapping)
                or not isinstance(outcomes, list)
                or not isinstance(subject, str)
            ):
                raise ValidationError(
                    {"config": "A dynamic call requires expected input, output, subject and outcomes."}
                )
            try:
                Draft202012Validator.check_schema(dict(input_schema))
                Draft202012Validator.check_schema(dict(schema))
            except Exception as error:  # noqa: BLE001 - JSON Schema reports several exception types.
                raise ValidationError({"expected_output_schema": "Expected output schema is invalid."}) from error
            if any(not isinstance(key, str) or not key for key in outcomes) or len(set(outcomes)) != len(outcomes):
                raise ValidationError({"expected_outcomes": "Expected outcome keys must be distinct nonempty strings."})

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

        config = step_run.step.config
        type(self).validate_config(config)
        payload = step_run.input
        selected_id = type(self).selected_publication_id(
            config=config,
            payload=payload,
        )
        publication = type(self)._publication(selected_id)
        child_input = payload.get("input")
        if not config.get("publication"):
            expected = config["expected_output_schema"]
            expected_input = config["expected_input_schema"]
            actual_outcomes = {rule["outcome"] for rule in publication.result_rules} or {"completed"}
            if (
                not json_values_equal(type(self)._input_schema(publication), expected_input)
                or not json_values_equal(publication.output_schema, expected)
                or publication.subject_declaration != config["expected_subject"].strip().lower()
                or not actual_outcomes.issubset(set(config["expected_outcomes"]))
            ):
                raise ValidationError({"publication": "Selected publication does not satisfy the call contract."})
        else:
            expected_input = type(self)._input_schema(publication)
        if list(Draft202012Validator(expected_input).iter_errors(child_input)):
            raise ValidationError({"input": "Child input does not satisfy its admitted publication contract."})
        run_model = apps.get_model("workflows", "WorkflowRun")
        root_id = step_run.run.execution_lineage_root_id()
        root = system_queryset(run_model, lock=None).get(pk=root_id)
        actor = root.admission_actor()
        if actor is None:
            raise ValidationError({"actor": "CallWorkflow requires the admitted execution actor."})
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
            subject = None if scoped is None else instance_from_public_id(model, public_id, queryset=scoped)
            if subject is None:
                raise ValidationError({"subject": "Child subject is unavailable to the execution actor."})
        else:
            raise ValidationError({"subject": "Child subject must be an exact record reference or null."})
        child = engine.start(
            publication,
            subject,
            actor,
            parent_step_run=step_run,
            parent_relation="owned_call",
            origin=RunOrigin.WORKFLOW,
            input=JsonPresence("input" in payload, payload.get("input")),
        )
        attempt_model = apps.get_model("workflows", "StepAttempt")
        attempt_model.objects.bind_call_child(
            step_run.current_attempt_id,
            lease_token=step_run._workflow_invocation_lease_token,
            child=child,
        )
        current = system_queryset(run_model, lock=("self",)).get(pk=child.pk)
        if current.status not in RunStatus.TERMINAL:
            return StepResult.suspend(decisions=(), waiting_kind="external")
        if current.result is None:
            raise ValidationError({"result": "Terminal child has no retained workflow result."})
        if current.status == RunStatus.SUCCEEDED:
            declared = {outcome.key for outcome in type(self).declared_outcomes(config)}
            if current.result.get("outcome") not in declared - {"child_failed", "child_canceled"}:
                raise ValidationError({"result": "Child produced an undeclared business outcome."})
            return StepResult.done(output=current.result["output"], outcome=current.result["outcome"])
        return StepResult.done(
            output_present=False,
            outcome="child_canceled" if current.status == RunStatus.CANCELED else "child_failed",
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
    output_model = DecisionGateOutput
    effect_description = "Creates workflow decision journals without changing the workflow subject."
    config_model = GateConfig

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
        return cls.normalize_config(resolved)

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
    """Consume one gate slot with workflow ancestry locked before domain rows.

    ``consume_decision_resolution`` invokes ``locked_record_basis`` only after
    locking the run, step run, and current attempt. Database-command adapters
    therefore keep the manager-wide ancestry-before-record lock order through
    the surrounding invocation transaction.
    """

    deterministic = False
    gate_step_class: ClassVar[type[GateStep]] = GateStep
    resolution_path: ClassVar[tuple[str | int, ...]] = ("resolutions", 0)

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

    @classmethod
    def decision_resolution_path(cls, step_run: Any) -> tuple[str | int, ...]:
        """Locate the gate value inside the ordinary one-predecessor join envelope."""

        value = step_run.input
        if isinstance(value, Mapping) and "resolutions" in value:
            return cls.resolution_path
        if isinstance(value, Mapping) and len(value) == 1:
            return (next(iter(value)), *cls.resolution_path)
        return cls.resolution_path

    def locked_record_basis(
        self,
        step_run: Any,
        predecessor: Any,
        *,
        actor: Any,
    ) -> Collection[models.Model]:
        """Return every already-locked domain row the manager verb will mutate."""

        del step_run, predecessor, actor
        return ()

    def apply_resolution(
        self,
        step_run: Any,
        decision: Any,
        admitted: DecisionResolution,
        *,
        actor: Any,
        record_basis: Collection[models.Model],
        now: datetime,
    ) -> StepResult:
        """Call the domain manager verb and return its typed workflow result."""

        del step_run, decision, admitted, actor, record_basis, now
        raise NotImplementedError

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        """Consume retained provenance, then pass the manager result through unchanged."""

        from angee.workflows import engine  # Runtime edge; the operation registry imports this module first.

        path = type(self).decision_resolution_path(step_run)
        predecessor = engine.load_predecessor_gate_decision(step_run, type(self).gate_step_class)
        actor = engine.resolve_workflow_actor(predecessor.resolved_by, require_person=True).actor
        retained_basis: list[tuple[models.Model, ...]] = []

        def lock_record_basis() -> tuple[models.Model, ...]:
            if retained_basis:
                raise RuntimeError("Decision apply record basis was requested more than once.")
            basis = tuple(self.locked_record_basis(step_run, predecessor, actor=actor))
            retained_basis.append(basis)
            return basis

        decision, resolution = engine.consume_decision_resolution(
            step_run,
            path,
            expected_action=predecessor.action,
            expected_target=(predecessor.target_model, predecessor.target_id),
            expected_verdict="completed",
            actor=actor,
            required_record_access=lock_record_basis,
        )
        if not retained_basis:
            raise RuntimeError("Decision consumption did not acquire its record basis.")
        record_basis = retained_basis[0]
        result = self.apply_resolution(
            step_run,
            decision,
            resolution,
            actor=actor,
            record_basis=record_basis,
            now=now,
        )
        if not isinstance(result, StepResult):
            raise TypeError("Decision apply manager verbs must return StepResult.")
        if result.kind == "done":
            declared = {outcome.key for outcome in type(self).outcomes}
            if result.outcome not in declared:
                raise ValidationError({"outcome": "Decision apply returned an undeclared outcome."})
            if result.output_present:
                type(self).output_model.model_validate(result.output)
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
    def engine_expanded_filter(cls) -> dict[str, Any]:
        """Return the ORM predicate for map parent rows the engine expands."""

        return {"step__step_class": cls.key, "map_index": -1}

    @classmethod
    def target_step(cls, step_run: Any) -> Any:
        """Return the configured target step for one map parent row."""

        config = cls.config_mapping(step_run)
        key = str(config.get("target_step") or "")
        if not key:
            raise ValidationError({"config": "Map steps require target_step."})
        try:
            return step_run.run.workflow.steps.get(key=key)
        except ObjectDoesNotExist as error:
            raise ValidationError({"config": f"Map target step {key!r} does not exist."}) from error

    @classmethod
    def items(cls, step_run: Any) -> list[Any]:
        """Return the item list resolved from this map step's config."""

        expression = cls.config_mapping(step_run).get("items")
        value = cls.expression_value(expression, step_run)
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
    def expression_value(cls, expression: Any, step_run: Any) -> Any:
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
            value = step_run.input
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
    decision_schema = dict(config.get("decision_schema") or {})
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
                target_authority_path=tuple(target.get("authority_path") or ()),
                target_authority_gate_path=tuple(target.get("authority_gate_path") or ()),
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
