"""Execution-mode defaults and plain settlements at the public step boundary."""

from datetime import timedelta

import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.utils import timezone
from pydantic import BaseModel, field_serializer

from angee.workflows.context import StepContext
from angee.workflows.reviews import ReviewStep
from angee.workflows.steps import NextPage, RetryPolicy, Step, resolve_step
from angee.workflows.testing.models import StepAttempt, StepRun, Workflow, WorkflowRun


class IOContractStep(Step):
    """An IO declaration with the default whole-attempt deadline."""

    key = "io_contract"
    mode = "IO"
    retry = RetryPolicy(max_attempts=3)


def context(step: type[Step]) -> StepContext:
    """Build only contract values; runtime tests obtain claims through the runner."""

    return StepContext(
        run=WorkflowRun(), step_run=StepRun(), step=step, attempt=StepAttempt(),
        input={}, config=None, actor=None, now=timezone.now(),
    )


def test_mode_defaults_preserve_custom_inherited_timeouts(register_step):
    """Only a mode change chooses a new default; subclassing preserves declared limits."""

    class CustomIO(IOContractStep):
        timeout = timedelta(seconds=90)

    class InheritedIO(CustomIO):
        mode = "IO"

    class DatabaseAgain(InheritedIO):
        mode = "DATABASE"

    assert Step.timeout == timedelta(seconds=30)
    assert IOContractStep.timeout == timedelta(minutes=5)
    assert CustomIO.timeout == InheritedIO.timeout == timedelta(seconds=90)
    assert DatabaseAgain.timeout == timedelta(seconds=30)
    assert not IOContractStep.effect_idempotent
    register_step(IOContractStep)
    assert resolve_step("io_contract") is IOContractStep


def test_unknown_execution_mode_is_rejected_at_resolution(register_step):
    """Publication cannot accept a step with an undefined transaction boundary."""

    class Unsupported(Step):
        key = "unsupported_contract"
        mode = "UNSUPPORTED"

    register_step(Unsupported)
    with pytest.raises(ImproperlyConfigured, match="DATABASE or IO"):
        resolve_step(Unsupported.key)


@pytest.mark.parametrize("soft, hard", [(840, 900), (120, 90)])
@pytest.mark.parametrize("margin_ms", [1, 0, -1])
def test_io_timeout_preserves_settlement_time_below_both_worker_limits(
    register_step, settings, soft, hard, margin_ms,
):
    """Publication requires more than the settlement reserve below either worker limit."""

    settings.CELERY_TASK_SOFT_TIME_LIMIT = soft
    settings.CELERY_TASK_TIME_LIMIT = hard

    class BoundedIO(IOContractStep):
        timeout = timedelta(seconds=min(soft, hard) - 30, milliseconds=-margin_ms)

    register_step(BoundedIO)
    if margin_ms > 0:
        assert resolve_step(BoundedIO.key) is BoundedIO
    else:
        with pytest.raises(ImproperlyConfigured, match="soft and hard time limits"):
            resolve_step(BoundedIO.key)


def test_next_page_serializes_once_at_the_body_boundary():
    """Paging helpers retain body values until the runner checks the settlement."""

    serializations = []

    class Checkpoint(BaseModel):
        position: int

        @field_serializer("position")
        def serialize_position(self, value):
            serializations.append(value)
            return str(value)

    checkpoint = Checkpoint(position=5)
    settlement = context(IOContractStep).next_page(checkpoint)
    assert settlement.state is checkpoint
    assert serializations == []

    checked = IOContractStep.check(settlement)

    assert isinstance(checked, NextPage)
    assert checked.state == {"position": "5"}
    assert serializations == [5]
    assert IOContractStep.check(context(IOContractStep).next_page()).state == {}


@pytest.mark.parametrize("kind", ["done", "wait", "next_page"])
def test_settlement_json_removes_null_bytes_from_nested_values_and_keys(kind):
    """Every JSON settlement payload remains storable in PostgreSQL JSONB."""

    ctx = context(IOContractStep)
    payload = {"na\x00me": ["va\x00lue", {"nested": "\x00"}]}
    if kind == "wait":
        settlement = ctx.wait(until=timezone.now(), state=payload)
    else:
        settlement = getattr(ctx, kind)(payload)
    checked = IOContractStep.check(settlement)
    assert (checked.output if kind == "done" else checked.state) == {"name": ["value", {"nested": ""}]}


@pytest.mark.parametrize("operation", ["begin_effect", "heartbeat", "raise_if_canceled"])
def test_io_operations_reject_database_contexts(operation):
    """Mode errors surface before a context can touch execution rows."""

    with pytest.raises(ValidationError, match="requires IO mode"):
        getattr(context(Step), operation)()


def test_io_context_rejects_locked_record_lookups():
    """IO bodies cannot retain database locks across external work."""

    ctx = context(IOContractStep)
    with pytest.raises(ValidationError, match="requires DATABASE mode"):
        ctx.subject_for_update()
    with pytest.raises(ValidationError, match="requires DATABASE mode"):
        ctx.load(Workflow, "absent", lock=True)


@pytest.mark.parametrize("retries, is_last", [(0, False), (1, False), (2, True)])
def test_context_retry_allowance_counts_failures_on_the_current_page(retries, is_last):
    """The lifetime attempt counter does not consume a page's retry allowance."""

    ctx = context(IOContractStep)
    ctx.step_run.attempt = 100
    ctx.step_run.retries = retries
    assert ctx.is_last_attempt is is_last


def test_context_acknowledgement_belongs_to_its_claimed_attempt():
    """Pending consent for another claim must not authorize this attempt's effects."""

    ctx = context(IOContractStep)
    ctx.step_run.retry_acknowledged_by_id = 5
    assert not ctx.retry_acknowledged
    ctx.attempt.acknowledged_by_id = 5
    assert ctx.retry_acknowledged


def test_generic_models_preserve_review_basis_override_precedence():
    """Generic contracts and explicit review bases retain their native inheritance."""

    class Value(BaseModel):
        value: int

    class Alternative(BaseModel):
        label: str

    class Typed(ReviewStep[Value, Value, Value, Value]):
        input_model = output_model = config_model = basis_model = Alternative

    class Inherited(Typed):
        pass

    class Explicit(Inherited):
        basis_model = Alternative

    class Untyped(ReviewStep[None, None, None, None]):
        pass

    for step in (Typed, Inherited, Explicit):
        assert (step.input_model, step.output_model, step.config_model) == (Value, Value, Value)
    assert Typed.basis_model is Inherited.basis_model is Explicit.basis_model is Alternative
    assert (Untyped.input_model, Untyped.output_model, Untyped.config_model, Untyped.basis_model) == (None,) * 4
