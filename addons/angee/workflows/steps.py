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

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Any, ClassVar, Literal, Self

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist, ValidationError
from django.db import models
from jsonschema import Draft202012Validator
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from pydantic import BaseModel
from rebac import system_context

from angee.base.impl import ImplBase, ImplChoice
from angee.base.identity import instance_from_public_id
from angee.base.scoping import read_scoped_queryset, system_queryset
from angee.workflows.attempts import (
    ArtifactSpec,
    AttemptResult,
    AttemptResultKind,
    DecisionSpec,
    DecisionGateOutput,
    ExternalOperationPolicy,
    JsonPresence,
    RecoveryCapability,
    RecoveryMode,
    json_values_equal,
)
from angee.workflows.configs import GateConfig, MapConfig, WaitConfig, map_items_expression_path
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
            raise ImproperlyConfigured(
                f"{cls.__name__} does not declare an input model."
            )
        return cls.input_model.model_validate_json(
            json.dumps(value, allow_nan=False)
        )

    @classmethod
    def recovery_capability(cls, *, attempt: Any) -> RecoveryCapability:
        """Return the explicit safe recovery mode for one retained failure."""

        del attempt
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
            attempt_model.objects.heartbeat(
                attempt.pk, lease_token=attempt.lease_token, at=timestamp
            )
            return
        step_run.heartbeat_at = timestamp
        with system_context(reason="workflows.step.heartbeat"):
            step_run.save(update_fields=["heartbeat_at", "updated_at"])


def retry_policy_from_config(config: Any) -> StepRetryPolicy:
    """Return the queue retry policy declared by ``config``."""

    if not isinstance(config, Mapping):
        raise ValidationError({"config": "Step config must be a JSON object."})
    retry = config.get("retry")
    if retry in (None, "", False):
        return StepRetryPolicy()
    if not isinstance(retry, Mapping):
        raise ValidationError({"config": "Step retry must be a JSON object."})

    max_attempts = positive_int(retry.get("max_attempts", 1), "Step retry max_attempts")
    wait = 0
    linear_wait = 0
    exponential_wait = 0
    backoff = retry.get("backoff", 0)
    if isinstance(backoff, Mapping):
        wait = non_negative_int(backoff.get("wait", 0), "Step retry backoff.wait")
        linear_wait = non_negative_int(backoff.get("linear_wait", 0), "Step retry backoff.linear_wait")
        exponential_wait = non_negative_int(
            backoff.get("exponential_wait", 0),
            "Step retry backoff.exponential_wait",
        )
    else:
        wait = non_negative_int(backoff, "Step retry backoff")
    return StepRetryPolicy(
        max_attempts=max_attempts,
        wait=wait,
        linear_wait=linear_wait,
        exponential_wait=exponential_wait,
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
    def recovery_capability(cls, *, attempt: Any) -> RecoveryCapability:
        del attempt
        return RecoveryCapability(RecoveryMode.FRESH)

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
            if any(key in config for key in (
                "expected_input_schema", "expected_output_schema", "expected_subject", "expected_outcomes"
            )):
                raise ValidationError({"config": "A static call derives its contract from its publication."})
        else:
            input_schema = config.get("expected_input_schema")
            schema = config.get("expected_output_schema")
            outcomes = config.get("expected_outcomes")
            subject = config.get("expected_subject")
            if (not isinstance(input_schema, Mapping) or not isinstance(schema, Mapping)
                    or not isinstance(outcomes, list) or not isinstance(subject, str)):
                raise ValidationError({"config": "A dynamic call requires expected input, output, subject and outcomes."})
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
        if not isinstance(payload, Mapping):
            raise ValidationError({"input": "CallWorkflow input must be an object."})
        selected_id = config.get("publication") or payload.get("publication")
        if not isinstance(selected_id, str):
            raise ValidationError({"publication": "CallWorkflow input must select a published workflow."})
        if config.get("publication") and payload.get("publication") not in (None, selected_id):
            raise ValidationError({"publication": "Call input cannot replace its declared static publication."})
        publication = type(self)._publication(selected_id)
        child_input = payload.get("input")
        if not config.get("publication"):
            expected = config["expected_output_schema"]
            expected_input = config["expected_input_schema"]
            actual_outcomes = {rule["outcome"] for rule in publication.result_rules} or {"completed"}
            if (
                not json_values_equal(type(self)._input_schema(publication), expected_input)
                or
                not json_values_equal(publication.output_schema, expected)
                or publication.subject_declaration != config["expected_subject"]
                or not actual_outcomes.issubset(set(config["expected_outcomes"]))
            ):
                raise ValidationError({"publication": "Selected publication does not satisfy the call contract."})
        else:
            expected_input = type(self)._input_schema(publication)
        if list(Draft202012Validator(expected_input).iter_errors(child_input)):
            raise ValidationError({"input": "Child input does not satisfy its admitted publication contract."})
        run_model = apps.get_model("workflows", "WorkflowRun")
        root_id = step_run.run.execution_lineage_root_id()
        root = system_queryset(run_model, lock=None).select_related("created_by").get(pk=root_id)
        actor = root.created_by
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
            publication, subject, actor,
            parent_step_run=step_run, parent_relation="owned_call", origin=RunOrigin.WORKFLOW,
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
    """Built-in gate step that suspends until Slice 4 decision rows exist."""

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
        """Suspend the step, keeping only durable resume state."""

        del now
        config = dict(step_run.step.config)
        return StepResult.suspend(
            resume_state={"gate": config},
            decisions=_decision_specs_from_config(config),
        )


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
        ratio = optional_number(mapping.get("min_success_ratio", mapping.get("min_success")), "Map min_success_ratio")
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
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _decision_specs_from_config(config: Mapping[str, Any]) -> tuple[DecisionSpec, ...]:
    """Return gate decision specs from declarative config."""

    slots = config.get("slots")
    if slots is None:
        slots = [{"assignee": subject} for subject in config.get("assignees", ())]
    action = str(config.get("action", "") or "")
    payload = dict(config.get("payload") or {})
    requester = str(config.get("requester", "") or "")
    escalation = tuple(str(subject) for subject in config.get("escalation", ()) if str(subject))
    max_attempts = config.get("max_attempts")
    parsed_max_attempts = None if max_attempts in (None, "") else int(str(max_attempts))
    expires_at = _config_datetime(config.get("expires_at"))
    escalate_at = _config_datetime(config.get("escalate_at"))
    decision_schema = dict(config.get("decision_schema") or {})
    specs: list[DecisionSpec] = []
    for index, slot in enumerate(slots if isinstance(slots, list) else []):
        if not isinstance(slot, Mapping):
            continue
        specs.append(
            DecisionSpec(
                assignees=_slot_assignees(slot),
                action=action,
                payload=payload,
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
                decision_schema=decision_schema,
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
