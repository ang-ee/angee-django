"""Immutable draft snapshot and idempotent test-launch contracts."""

from __future__ import annotations

import copy
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection, connections, models, transaction
from django.db.models.signals import post_save
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rebac import RelationshipTuple, system_context, to_subject_ref, write_relationships
from rebac.resources import to_object_ref

from angee.workflows import engine
from angee.workflows.attempts import (
    ArtifactSpec,
    AttemptCause,
    AttemptInput,
    AttemptResult,
    AttemptResultKind,
    FixtureRole,
    FixtureSpec,
    JsonPresence,
    RecoveryCapability,
    RecoveryMode,
    workflow_result_terminal_match_error,
)
from angee.workflows.attempts import WorkflowScope as WorkflowTestScope
from angee.workflows.definitions import StaleDefinitionError
from angee.workflows.dispatch import WorkflowDispatchKind
from angee.workflows.models import RunOrigin, WorkflowStatus
from angee.workflows.steps import StepImpl, StepResult
from tests.workflows import (
    Edge,
    Step,
    StepArtifact,
    StepAttempt,
    StepRun,
    Workflow,
    WorkflowDispatch,
    WorkflowRecoveryEvidence,
    WorkflowRun,
    WorkflowTestFixture,
    advance_once,
    execute_started,
)

pytestmark = pytest.mark.django_db(transaction=True)


class _ReconcilingTestStep(StepImpl):
    calls: list[tuple[int, int]] = []

    @classmethod
    def recovery_capability(cls, *, attempt: object) -> RecoveryCapability:
        del attempt
        return RecoveryCapability(RecoveryMode.RECONCILE)

    def run_recovery(
        self,
        step_run: object,
        *,
        now: object,
        source_attempt: object,
        mode: RecoveryMode,
    ) -> StepResult:
        del now
        assert mode is RecoveryMode.RECONCILE
        self.calls.append((step_run.pk, source_attempt.pk))
        return StepResult.done(
            {"reconciled": True},
            outcome="done",
            artifacts=(ArtifactSpec(target=step_run.run.workflow, label="Recovered workflow"),),
        )


def test_recovery_reuses_exact_input_and_records_nonduplicated_artifacts(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    actor = get_user_model().objects.create_user(username="recovery-owner")
    workflow, step = _draft(owner=actor)
    with system_context(reason="recovery source setup"):
        source_run = WorkflowRun.objects.create(
            workflow=workflow,
            status="running",
            admitted_actor_ref=str(to_subject_ref(actor)),
            created_by=actor,
            input_present=True,
            input={"exact": None},
        )
        source_step_run = StepRun.objects.create(
            run=source_run,
            step=step,
            status="scheduled",
        )
    source = StepAttempt.objects.claim(
        source_step_run,
        input=AttemptInput(True, {"exact": None}, {"kind": "run_input"}),
        claimed_at=timezone.now(),
    ).attempt
    StepAttempt.objects.admit_invocation(source.pk, lease_token=source.lease_token, at=timezone.now())
    StepAttempt.objects.finalize(
        source.pk,
        lease_token=source.lease_token,
        result=AttemptResult(AttemptResultKind.ERROR, error="uncertain publication"),
        recorded_at=timezone.now(),
    )
    monkeypatch.setattr(type(step), "resolve_impl", lambda self, field: _ReconcilingTestStep)
    _ReconcilingTestStep.calls = []

    recovery = WorkflowRun.objects.start_recovery(source, request_key="recover-once", actor=actor)
    duplicate = WorkflowRun.objects.start_recovery(source, request_key="recover-once", actor=actor)
    assert duplicate.pk == recovery.pk
    with system_context(reason="recovery admission inspection"):
        evidence = WorkflowRecoveryEvidence.objects.filter(run=recovery)
        assert evidence.count() == 0
        advance = WorkflowDispatch.objects.get(run=recovery, kind=WorkflowDispatchKind.ADVANCE)
    assert engine.advance_dispatch(advance.pk)["claimed"] == 1
    with system_context(reason="recovery invocation inspection"):
        recovery_step = StepRun.objects.get(run=recovery, step=step)
        attempt = StepAttempt.objects.get(pk=recovery_step.current_attempt_id)
        execute = WorkflowDispatch.objects.get(step_attempt=attempt)
    assert attempt.input_present and attempt.input == {"exact": None}
    assert engine.execute_dispatch(execute.pk, attempt.pk, attempt.lease_token)["executed"] == 1

    with system_context(reason="recovery result inspection"):
        attempt.refresh_from_db()
        artifacts = list(StepArtifact.objects.filter(attempt=attempt))
    assert _ReconcilingTestStep.calls == [(recovery_step.pk, source.pk)]
    assert attempt.recovery_source_attempt_id == source.pk
    assert attempt.artifacts_present
    assert [(artifact.declaration_index, artifact.label) for artifact in artifacts] == [
        (0, "Recovered workflow")
    ]


def test_join_continuation_accepts_one_exact_successful_recovery(
    workflow_engine_tables: None,
) -> None:
    actor = get_user_model().objects.create_user(username="continuation-completion-owner")
    workflow, step = _draft(name="Recovered continuation", owner=actor)
    parent_workflow, starter_step = _draft(name="Continuation parent", owner=actor)
    actor_ref = str(to_subject_ref(actor))
    frozen_input = {"document": "retained"}
    with system_context(reason="continuation completion source fixture"):
        parent = WorkflowRun.objects.create(
            workflow=parent_workflow,
            status="running",
            admitted_actor_ref=actor_ref,
            created_by=actor,
        )
        starter = StepRun.objects.create(run=parent, step=starter_step, status="succeeded")
        join = StepRun.objects.create(run=parent, step=starter_step, map_index=1)
        original = WorkflowRun.objects.create(
            parent_step_run=starter,
            parent_relation="continuation",
            workflow=workflow,
            origin=RunOrigin.WORKFLOW,
            status="running",
            admitted_actor_ref=actor_ref,
            created_by=actor,
            input_present=True,
            input=frozen_input,
        )
        failed_step = StepRun.objects.create(
            run=original,
            step=step,
            status="scheduled",
        )
    failed_attempt = StepAttempt.objects.claim(
        failed_step,
        input=AttemptInput(True, frozen_input, {"kind": "run_input"}),
        claimed_at=timezone.now(),
    ).attempt
    StepAttempt.objects.admit_invocation(
        failed_attempt.pk,
        lease_token=failed_attempt.lease_token,
        at=timezone.now(),
    )
    StepAttempt.objects.finalize(
        failed_attempt.pk,
        lease_token=failed_attempt.lease_token,
        result=AttemptResult(AttemptResultKind.ERROR, error="retained failure"),
        recorded_at=timezone.now(),
    )
    with system_context(reason="continuation completion recovery fixture"):
        original.refresh_from_db()
        original.mark_failed("Retained child failed.")
        recovery = WorkflowRun.objects.create(
            workflow=workflow,
            origin=RunOrigin.RECOVERY,
            status="running",
            recovery_source_attempt=failed_attempt,
            recovery_request_actor_ref=actor_ref,
            recovery_mode=RecoveryMode.FRESH,
            admitted_actor_ref=actor_ref,
            created_by=actor,
            input_present=True,
            input=frozen_input,
        )
        recovery.mark_succeeded(outcome="deferred", output={"status": "deferred"})

    join_attempt = StepAttempt.objects.claim(join, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(
        join_attempt.pk,
        lease_token=join_attempt.lease_token,
        at=timezone.now(),
    )
    retained, completion = StepAttempt.objects.join_continuation(
        join.pk,
        lease_token=join_attempt.lease_token,
        child_id=original.sqid,
        expected_starter_class=starter_step.step_class,
        actor=actor,
    )
    assert retained.pk == original.pk
    assert completion is not None and completion.pk == recovery.pk

    with system_context(reason="conflicting continuation completion fixture"):
        conflicting = WorkflowRun.objects.create(
            workflow=workflow,
            origin=RunOrigin.RECOVERY,
            status="running",
            recovery_source_attempt=failed_attempt,
            recovery_request_actor_ref=actor_ref,
            recovery_mode=RecoveryMode.FRESH,
            admitted_actor_ref=actor_ref,
            created_by=actor,
            input_present=True,
            input={"document": "changed"},
        )
        conflicting.mark_succeeded(outcome="deferred", output={"status": "deferred"})
    with pytest.raises(ValidationError, match="conflicting retained facts"):
        StepAttempt.objects.join_continuation(
            join.pk,
            lease_token=join_attempt.lease_token,
            child_id=original.sqid,
            expected_starter_class=starter_step.step_class,
            actor=actor,
        )


def test_fresh_recovery_reuses_original_child_handoff_across_multiple_failures(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    actor = get_user_model().objects.create_user(username="recovery-child-owner")
    source_run, source_step_run, source_attempt, child_head, child = _failed_child_handoff(
        actor=actor, dedup_key="recovery-child:stable",
    )

    class FreshChildHandoff(StepImpl):
        failures_remaining = 1
        children: list[int] = []
        retained_children: list[int] = []

        @classmethod
        def recovery_capability(cls, *, attempt: object) -> RecoveryCapability:
            del attempt
            return RecoveryCapability(RecoveryMode.FRESH)

        def run(self, step_run: object, *, now: object) -> StepResult:
            del now
            retained_child = WorkflowRun.objects.retained_child_for_start(
                step_run, actor=actor, origin=RunOrigin.WORKFLOW,
            )
            assert retained_child is not None
            self.retained_children.append(retained_child.pk)
            retained = engine.start(
                child_head, subject=None, actor=actor,
                parent_step_run=step_run, parent_relation="continuation", dedup_key="recovery-child:stable",
                origin=RunOrigin.WORKFLOW,
                input=JsonPresence(True, {"child": "retained"}),
            )
            self.children.append(retained.pk)
            if self.failures_remaining:
                type(self).failures_remaining -= 1
                raise RuntimeError("uncertain after child handoff")
            return StepResult.done(
                {"child_id": retained.pk}, outcome="done",
                artifacts=(ArtifactSpec(retained, "Retained child workflow"),),
            )

    step = source_step_run.step
    monkeypatch.setattr(type(step), "resolve_impl", lambda self, field: FreshChildHandoff)
    first_recovery, first_attempt = _execute_recovery(source_attempt, actor=actor, request_key="first")
    assert first_attempt.result_kind == AttemptResultKind.ERROR
    assert "uncertain after child handoff" in first_attempt.error

    second_recovery, second_attempt = _execute_recovery(
        first_attempt, actor=actor, request_key="second",
    )
    assert second_attempt.result_kind == AttemptResultKind.DONE
    assert FreshChildHandoff.children == [child.pk, child.pk]
    assert FreshChildHandoff.retained_children == [child.pk, child.pk]
    with system_context(reason="recovery child handoff inspection"):
        retained = list(WorkflowRun.objects.filter(dedup_key="recovery-child:stable"))
        child.refresh_from_db()
    assert [row.pk for row in retained] == [child.pk]
    assert child.parent_step_run_id == source_step_run.pk
    assert source_run.same_execution_lineage(first_recovery)
    assert first_recovery.same_execution_lineage(second_recovery)
    assert child.parent_step_run.run.same_execution_lineage(second_recovery)


def test_fresh_recovery_child_handoff_rejects_changed_or_unrelated_identity(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    actor = get_user_model().objects.create_user(username="recovery-child-mismatch-owner")
    _source_run, source_step_run, source_attempt, child_head, child = _failed_child_handoff(
        actor=actor, dedup_key="recovery-child:mismatch",
    )

    class ChangedChildHandoff(StepImpl):
        @classmethod
        def recovery_capability(cls, *, attempt: object) -> RecoveryCapability:
            del attempt
            return RecoveryCapability(RecoveryMode.FRESH)

        def run(self, step_run: object, *, now: object) -> StepResult:
            del now
            engine.start(
                child_head, subject=None, actor=actor,
                parent_step_run=step_run, parent_relation="continuation", dedup_key="recovery-child:mismatch",
                origin=RunOrigin.WORKFLOW,
                input=JsonPresence(True, {"child": "changed"}),
            )
            return StepResult.done(outcome="unreachable")

    monkeypatch.setattr(
        type(source_step_run.step), "resolve_impl", lambda self, field: ChangedChildHandoff,
    )
    _recovery, attempt = _execute_recovery(
        source_attempt, actor=actor, request_key="changed-child",
    )
    assert attempt.result_kind == AttemptResultKind.ERROR
    assert "different frozen input" in attempt.error
    with system_context(reason="unrelated child parent fixture"):
        unrelated_run = WorkflowRun.objects.create(
            workflow=source_step_run.run.workflow, status="running",
            admitted_actor_ref=str(to_subject_ref(actor)), created_by=actor,
        )
        unrelated_parent = StepRun.objects.create(
            run=unrelated_run, step=source_step_run.step, status="started",
        )
    with pytest.raises(ValidationError, match="different immutable facts"):
        engine.start(
            child_head, subject=None, actor=actor,
            parent_step_run=unrelated_parent, parent_relation="continuation", dedup_key="recovery-child:mismatch",
            origin=RunOrigin.WORKFLOW,
            input=JsonPresence(True, {"child": "retained"}),
        )
    with system_context(reason="mismatched child handoff inspection"):
        assert WorkflowRun.objects.filter(dedup_key="recovery-child:mismatch").count() == 1
    assert child.parent_step_run_id == source_step_run.pk


def test_fresh_recovery_keeps_new_downstream_child_on_the_recovery_run(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    actor = get_user_model().objects.create_user(username="recovery-downstream-child-owner")
    child_head = _published_wait_workflow(actor=actor)
    with system_context(reason="downstream recovery child fixture"):
        source_workflow, recovered_step = _draft(name="Recovered predecessor", owner=actor)
        downstream_step = Step.objects.create(
            workflow=source_workflow, key="handoff", name="Handoff", step_class="fixture",
        )
        Edge.objects.create(
            workflow=source_workflow, source=recovered_step, target=downstream_step,
        )
        source_run = WorkflowRun.objects.create(
            workflow=source_workflow, status="running",
            admitted_actor_ref=str(to_subject_ref(actor)), created_by=actor,
            input_present=True, input={"parent": "retained"},
        )
        recovered_step_run = StepRun.objects.create(
            run=source_run, step=recovered_step, status="scheduled",
        )
    source_attempt = StepAttempt.objects.claim(
        recovered_step_run,
        input=AttemptInput(True, {"parent": "retained"}, {"kind": "run_input"}),
        claimed_at=timezone.now(),
    ).attempt
    StepAttempt.objects.admit_invocation(
        source_attempt.pk, lease_token=source_attempt.lease_token, at=timezone.now(),
    )
    StepAttempt.objects.finalize(
        source_attempt.pk, lease_token=source_attempt.lease_token,
        result=AttemptResult(AttemptResultKind.ERROR, error="recover predecessor"),
        recorded_at=timezone.now(),
    )

    class RecoveredPredecessor(StepImpl):
        @classmethod
        def recovery_capability(cls, *, attempt: object) -> RecoveryCapability:
            del attempt
            return RecoveryCapability(RecoveryMode.FRESH)

        def run(self, step_run: object, *, now: object) -> StepResult:
            del step_run, now
            return StepResult.done(outcome="done")

    class FirstDownstreamHandoff(StepImpl):
        def run(self, step_run: object, *, now: object) -> StepResult:
            del now
            assert WorkflowRun.objects.retained_child_for_start(
                step_run, actor=actor, origin=RunOrigin.WORKFLOW,
            ) is None
            child = engine.start(
                child_head, subject=None, actor=actor,
                parent_step_run=step_run, parent_relation="continuation", dedup_key="recovery-child:downstream",
                origin=RunOrigin.WORKFLOW,
            )
            assert WorkflowRun.objects.retained_child_for_start(
                step_run, actor=actor, origin=RunOrigin.WORKFLOW,
            ).pk == child.pk
            return StepResult.done(
                {"child_id": child.pk}, outcome="done",
                artifacts=(ArtifactSpec(child, "Downstream child workflow"),),
            )

    monkeypatch.setattr(
        type(recovered_step), "resolve_impl",
        lambda self, field: (
            RecoveredPredecessor if self.pk == recovered_step.pk else FirstDownstreamHandoff
        ),
    )
    recovery, recovered_attempt = _execute_recovery(
        source_attempt, actor=actor, request_key="recover-predecessor",
    )
    assert recovered_attempt.result_kind == AttemptResultKind.DONE
    engine.advance(recovery.pk)
    with system_context(reason="downstream recovery child invocation"):
        downstream = StepRun.objects.get(run=recovery, step=downstream_step)
        attempt = downstream.current_attempt
        dispatch = WorkflowDispatch.objects.get(step_attempt=attempt)
    assert engine.execute_dispatch(
        dispatch.pk, attempt.pk, attempt.lease_token,
    )["executed"] == 1
    with system_context(reason="downstream recovery child result"):
        attempt.refresh_from_db()
        child = WorkflowRun.objects.get(dedup_key="recovery-child:downstream")
    assert attempt.result_kind == AttemptResultKind.DONE
    assert child.parent_step_run_id == downstream.pk
    assert child.parent_step_run.run.same_execution_lineage(recovery)


def test_repeated_recovery_retains_required_original_and_current_outputs(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later recovery flattens exact predecessor evidence from the same lineage."""

    del workflow_engine_tables, no_workflow_queue
    actor = get_user_model().objects.create_user(username="repeated-recovery-owner")

    class RecoverableMiddle(StepImpl):
        @classmethod
        def recovery_capability(cls, *, attempt: object) -> RecoveryCapability:
            del attempt
            return RecoveryCapability(RecoveryMode.FRESH)

        def run_recovery(
            self,
            step_run: object,
            *,
            now: object,
            source_attempt: object,
            mode: RecoveryMode,
        ) -> StepResult:
            del now, source_attempt
            assert mode is RecoveryMode.FRESH
            assert step_run.input == {"original": "retained"}
            return StepResult.done({"middle": "recovered"}, outcome="done")

    class RecoverableEnd(StepImpl):
        @classmethod
        def recovery_capability(cls, *, attempt: object) -> RecoveryCapability:
            del attempt
            return RecoveryCapability(RecoveryMode.FRESH)

        def run(self, step_run: object, *, now: object) -> StepResult:
            del step_run, now
            raise RuntimeError("recover downstream")

        def run_recovery(
            self,
            step_run: object,
            *,
            now: object,
            source_attempt: object,
            mode: RecoveryMode,
        ) -> StepResult:
            del now, source_attempt
            assert mode is RecoveryMode.FRESH
            assert step_run.input == {
                "original": {"original": "retained"},
                "middle": {"middle": "recovered"},
            }
            return StepResult.done({"accepted": True}, outcome="done")

    with system_context(reason="repeated recovery fixture"):
        workflow = Workflow.objects.create(name="Repeated recovery", created_by=actor)
        original_step = Step.objects.create(
            workflow=workflow, key="original", name="Original", step_class="fixture", is_entry=True,
        )
        middle_step = Step.objects.create(
            workflow=workflow,
            key="middle",
            name="Middle",
            step_class="fixture",
            input_binding={"kind": "step_output", "step_key": "original", "path": []},
        )
        end_step = Step.objects.create(
            workflow=workflow,
            key="end",
            name="End",
            step_class="fixture",
            input_binding={
                "kind": "object",
                "fields": {
                    "original": {"kind": "step_output", "step_key": "original", "path": []},
                    "middle": {"kind": "step_output", "step_key": "middle", "path": []},
                },
            },
        )
        Edge.objects.create(workflow=workflow, source=original_step, target=middle_step)
        Edge.objects.create(workflow=workflow, source=middle_step, target=end_step)
        source_run = WorkflowRun.objects.create(
            workflow=workflow,
            status="running",
            admitted_actor_ref=str(to_subject_ref(actor)),
            created_by=actor,
        )
        original_run = StepRun.objects.create(
            run=source_run, step=original_step, status="scheduled",
        )
        middle_run = StepRun.objects.create(
            run=source_run, step=middle_step, status="scheduled",
        )
    original_attempt = StepAttempt.objects.claim(
        original_run, claimed_at=timezone.now(),
    ).attempt
    StepAttempt.objects.admit_invocation(
        original_attempt.pk, lease_token=original_attempt.lease_token, at=timezone.now(),
    )
    StepAttempt.objects.finalize(
        original_attempt.pk,
        lease_token=original_attempt.lease_token,
        result=AttemptResult(
            AttemptResultKind.DONE,
            output_present=True,
            output={"original": "retained"},
            outcome="done",
        ),
        recorded_at=timezone.now(),
    )
    middle_attempt = StepAttempt.objects.claim(
        middle_run,
        input=AttemptInput(
            True,
            {"original": "retained"},
            {
                "kind": "step_output",
                "attempt_id": original_attempt.pk,
                "step_run_id": original_run.pk,
                "step_key": "original",
                "path": [],
            },
        ),
        claimed_at=timezone.now(),
    ).attempt
    StepAttempt.objects.admit_invocation(
        middle_attempt.pk, lease_token=middle_attempt.lease_token, at=timezone.now(),
    )
    StepAttempt.objects.finalize(
        middle_attempt.pk,
        lease_token=middle_attempt.lease_token,
        result=AttemptResult(AttemptResultKind.ERROR, error="recover middle"),
        recorded_at=timezone.now(),
    )

    original_resolve = type(middle_step).resolve_impl
    monkeypatch.setattr(
        type(middle_step),
        "resolve_impl",
        lambda self, field: (
            RecoverableMiddle
            if self.key == "middle"
            else RecoverableEnd
            if self.key == "end"
            else original_resolve(self, field)
        ),
    )
    first, recovered_middle = _execute_recovery(
        middle_attempt, actor=actor, request_key="recover-middle",
    )
    assert recovered_middle.result_kind == AttemptResultKind.DONE
    assert advance_once(first)
    execute_started(first)
    with system_context(reason="repeated recovery downstream failure"):
        failed_end = StepRun.objects.get(run=first, step=end_step).current_attempt
    assert failed_end.result_kind == AttemptResultKind.ERROR

    second = WorkflowRun.objects.start_recovery(
        failed_end, request_key="recover-end", actor=actor,
    )
    with system_context(reason="repeated recovery admitted evidence"):
        evidence = {
            row.step.key: row.source_attempt_id
            for row in second.recovery_evidence.select_related("step")
        }
    assert evidence == {
        "original": original_attempt.pk,
        "middle": recovered_middle.pk,
    }
    started = advance_once(second)
    assert [(row.step_id, row.map_index) for row in started] == [(end_step.pk, -1)]
    execute_started(second)
    assert advance_once(second) == []
    second.refresh_from_db()
    assert second.status == "succeeded"


def test_fresh_recovery_rebinds_preparation_error_from_admitted_evidence(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed binding is prepared again from the new run's immutable basis."""

    del workflow_engine_tables, no_workflow_queue
    actor = get_user_model().objects.create_user(username="preparation-recovery-owner")

    class RecoverableBoundStep(StepImpl):
        @classmethod
        def recovery_capability(cls, *, attempt: object) -> RecoveryCapability:
            del attempt
            return RecoveryCapability(RecoveryMode.FRESH)

        def run_recovery(
            self,
            step_run: object,
            *,
            now: object,
            source_attempt: object,
            mode: RecoveryMode,
        ) -> StepResult:
            del now, source_attempt
            assert mode is RecoveryMode.FRESH
            assert step_run.input == {"retained": "source"}
            return StepResult.done({"rebound": True}, outcome="done")

    with system_context(reason="preparation recovery fixture"):
        workflow = Workflow.objects.create(name="Preparation recovery", created_by=actor)
        source_step = Step.objects.create(
            workflow=workflow, key="source", name="Source", step_class="fixture", is_entry=True,
        )
        target_step = Step.objects.create(
            workflow=workflow,
            key="target",
            name="Target",
            step_class="fixture",
            input_binding={"kind": "step_output", "step_key": "source", "path": []},
        )
        Edge.objects.create(workflow=workflow, source=source_step, target=target_step)
        source_run = WorkflowRun.objects.create(
            workflow=workflow,
            status="running",
            admitted_actor_ref=str(to_subject_ref(actor)),
            created_by=actor,
        )
        source_row = StepRun.objects.create(
            run=source_run, step=source_step, status="scheduled",
        )
        target_row = StepRun.objects.create(
            run=source_run, step=target_step, status="scheduled",
        )
    retained = StepAttempt.objects.claim(source_row, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(
        retained.pk, lease_token=retained.lease_token, at=timezone.now(),
    )
    StepAttempt.objects.finalize(
        retained.pk,
        lease_token=retained.lease_token,
        result=AttemptResult(
            AttemptResultKind.DONE,
            output_present=True,
            output={"retained": "source"},
            outcome="done",
        ),
        recorded_at=timezone.now(),
    )
    failed = StepAttempt.objects.fail_preparation(
        target_row,
        cause=AttemptCause.INITIAL,
        input=AttemptInput(
            False,
            None,
            {
                "kind": "binding",
                "diagnostics": [{"code": "source_missing", "path": []}],
            },
        ),
        result=AttemptResult(
            AttemptResultKind.PREPARATION_ERROR,
            error="Source output is unavailable.",
            outcome="failed",
        ),
        claimed_at=timezone.now(),
        recorded_at=timezone.now(),
    )
    original_resolve = type(target_step).resolve_impl
    monkeypatch.setattr(
        type(target_step),
        "resolve_impl",
        lambda self, field: (
            RecoverableBoundStep if self.key == "target" else original_resolve(self, field)
        ),
    )

    recovery, attempt = _execute_recovery(
        failed, actor=actor, request_key="rebind-preparation-error",
    )
    assert attempt.result_kind == AttemptResultKind.DONE
    assert attempt.input == {"retained": "source"}
    assert attempt.input_provenance["kind"] == "recovery_evidence"
    assert attempt.input_provenance["attempt_id"] == retained.pk
    assert advance_once(recovery) == []
    recovery.refresh_from_db()
    assert recovery.status == "succeeded"


def test_fresh_recovery_validates_downstream_map_items_against_their_expansion(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Map reached after recovery owns new item evidence within the recovery run."""

    del workflow_engine_tables, no_workflow_queue
    actor = get_user_model().objects.create_user(username="recovery-map-owner")

    class RecoveredRoot(StepImpl):
        @classmethod
        def recovery_capability(cls, *, attempt: object) -> RecoveryCapability:
            del attempt
            return RecoveryCapability(RecoveryMode.FRESH)

        def run(self, step_run: object, *, now: object) -> StepResult:
            del step_run, now
            raise RuntimeError("source failure")

        def run_recovery(
            self,
            step_run: object,
            *,
            now: object,
            source_attempt: object,
            mode: RecoveryMode,
        ) -> StepResult:
            del step_run, now, source_attempt
            assert mode is RecoveryMode.FRESH
            return StepResult.done(outcome="done")

    class MappedItem(StepImpl):
        def run(self, step_run: object, *, now: object) -> StepResult:
            del now
            assert step_run.input == {"value": "retained"}
            return StepResult.done(outcome="done")

    with system_context(reason="downstream recovery map fixture"):
        workflow = Workflow.objects.create(name="Recovery then Map", created_by=actor)
        recovered_step = Step.objects.create(
            workflow=workflow, key="recover", name="Recover", step_class="fixture", is_entry=True,
        )
        map_step = Step.objects.create(
            workflow=workflow,
            key="map",
            name="Map",
            step_class="map",
            config={"target_step": "item", "items": [{"value": "retained"}]},
        )
        item_step = Step.objects.create(
            workflow=workflow, key="item", name="Item", step_class="fixture",
        )
        Edge.objects.create(
            workflow=workflow, source=recovered_step, target=map_step, condition="done",
        )
        original_resolve = type(recovered_step).resolve_impl
        monkeypatch.setattr(
            type(recovered_step),
            "resolve_impl",
            lambda self, field: (
                RecoveredRoot
                if self.key == "recover"
                else MappedItem
                if self.key == "item"
                else original_resolve(self, field)
            ),
        )
        workflow = workflow.publish()
        recovered_step = workflow.steps.get(key="recover")
        map_step = workflow.steps.get(key="map")
        item_step = workflow.steps.get(key="item")
    source_run = engine.start(workflow, subject=None, actor=actor)
    advance_once(source_run)
    execute_started(source_run)
    with system_context(reason="downstream recovery map source failure"):
        source_attempt = StepRun.objects.get(run=source_run, step=recovered_step).current_attempt
    assert source_attempt.result_kind == AttemptResultKind.ERROR

    recovery, recovered_attempt = _execute_recovery(
        source_attempt, actor=actor, request_key="recover-before-map",
    )
    assert recovered_attempt.result_kind == AttemptResultKind.DONE
    started = advance_once(recovery)
    assert [(row.step_id, row.map_index) for row in started] == [(item_step.pk, 0)]
    with system_context(reason="downstream recovery map evidence"):
        item_attempt = started[0].current_attempt
        expansion_attempt = StepRun.objects.get(run=recovery, step=map_step).current_attempt
    assert item_attempt.map_expansion_id == expansion_attempt.pk
    assert item_attempt.map_item == {"value": "retained"}
    execute_started(recovery)
    engine.advance(recovery.pk)
    recovery.refresh_from_db()
    assert recovery.status == "succeeded"


@pytest.mark.parametrize("scenario", ["remaining_sibling", "retry_failed_recovery"])
def test_fresh_map_body_recovery_rejoins_retained_results_before_continuing(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
) -> None:
    """Map recovery carries its exact admitted aggregate across a linear branch."""

    del workflow_engine_tables, no_workflow_queue
    actor = get_user_model().objects.create_user(
        username=f"map-body-recovery-{scenario}"
    )
    original_failures = {1, 2} if scenario == "remaining_sibling" else {1}
    recovery_failures = {1: 1} if scenario == "retry_failed_recovery" else {}

    class RecoverableItem(StepImpl):
        @classmethod
        def recovery_capability(cls, *, attempt: object) -> RecoveryCapability:
            del attempt
            return RecoveryCapability(RecoveryMode.FRESH)

        def run(self, step_run: object, *, now: object) -> StepResult:
            del now
            if step_run.run.origin != RunOrigin.RECOVERY and step_run.map_index in original_failures:
                raise RuntimeError("retained page failure")
            remaining = recovery_failures.get(step_run.map_index, 0)
            if step_run.run.origin == RunOrigin.RECOVERY and remaining:
                recovery_failures[step_run.map_index] = remaining - 1
                raise RuntimeError("repeated retained page failure")
            return StepResult.done(
                {
                    "value": step_run.input["value"],
                    "recovered": step_run.run.origin == RunOrigin.RECOVERY,
                },
                outcome="done",
            )

    class CollectItems(StepImpl):
        inputs: list[dict[str, object]] = []

        def run(self, step_run: object, *, now: object) -> StepResult:
            del now
            type(self).inputs.append(copy.deepcopy(step_run.input))
            return StepResult.done({"joined": step_run.input["results"]}, outcome="done")

    with system_context(reason="Map body recovery fixture"):
        workflow = Workflow.objects.create(name="Recover Map body", created_by=actor)
        map_step = Step.objects.create(
            workflow=workflow,
            key="map",
            name="Map",
            step_class="map",
            config={
                "target_step": "page",
                "items": [{"value": 0}, {"value": 1}, {"value": 2}],
                "all_must_succeed": True,
            },
            is_entry=True,
        )
        Step.objects.create(
            workflow=workflow, key="page", name="Page", step_class="fixture",
        )
        collect_step = Step.objects.create(
            workflow=workflow, key="collect", name="Collect", step_class="fixture",
        )
        Edge.objects.create(
            workflow=workflow, source=map_step, target=collect_step, condition="succeeded",
        )
        original_resolve = type(map_step).resolve_impl
        monkeypatch.setattr(
            type(map_step),
            "resolve_impl",
            lambda self, field: (
                RecoverableItem
                if self.key == "page"
                else CollectItems
                if self.key == "collect"
                else original_resolve(self, field)
            ),
        )
        workflow = workflow.publish()
        map_step = workflow.steps.get(key="map")
        page_step = workflow.steps.get(key="page")
        collect_step = workflow.steps.get(key="collect")

    source_run = engine.start(workflow, subject=None, actor=actor)
    assert len(advance_once(source_run)) == 3
    execute_started(source_run)
    with system_context(reason="unaggregated Map recovery evidence"):
        unaggregated_source = StepRun.objects.get(
            run=source_run, step=page_step, map_index=1,
        ).current_attempt
    unavailable = StepAttempt.objects.recovery_plan(unaggregated_source, actor=actor)
    assert unavailable.capability.available is False
    assert "exact retained expansion" in unavailable.capability.unavailable_reason
    assert advance_once(source_run) == []
    with system_context(reason="Map body recovery source evidence"):
        source_controller = StepRun.objects.get(run=source_run, step=map_step)
        source_page = StepRun.objects.get(run=source_run, step=page_step, map_index=1)
        source_attempt = source_page.current_attempt
        source_results = copy.deepcopy(source_controller.output["results"])
    assert source_controller.outcome == "failed"
    assert [source_results[index]["status"] for index in sorted(original_failures)] == [
        "failed" for _index in original_failures
    ]

    first = WorkflowRun.objects.start_recovery(
        source_attempt, request_key=f"recover-map-page-first-{scenario}", actor=actor,
    )
    started = advance_once(first)
    assert [(row.step_id, row.map_index) for row in started] == [(page_step.pk, 1)]
    execute_started(first)
    assert advance_once(first) == []
    first.refresh_from_db()
    with system_context(reason="first Map recovery evidence"):
        first_page = StepRun.objects.get(run=first, step=page_step, map_index=1)
        first_basis = first.recovery_evidence.get(step=map_step, map_index=-1)
    assert first_basis.source_attempt_id == source_controller.current_attempt_id

    if scenario == "remaining_sibling":
        assert first.status == "succeeded"
        with system_context(reason="remaining Map sibling evidence"):
            first_controller = StepRun.objects.get(run=first, step=map_step)
            remaining_source = StepRun.objects.get(
                run=source_run, step=page_step, map_index=2,
            ).current_attempt
        assert first_controller.output["results"][1]["status"] == "succeeded"
        assert first_controller.output["results"][2]["status"] == "failed"
        final = WorkflowRun.objects.start_recovery(
            remaining_source,
            request_key="recover-map-page-remaining",
            actor=actor,
            prior_recovery=first,
        )
        expected_index = 2
        expected_basis_id = first_controller.current_attempt_id
    else:
        assert first.status == "failed"
        assert first_page.current_attempt.result_kind == AttemptResultKind.ERROR
        with system_context(reason="failed Map recovery has no projected controller"):
            assert not StepRun.objects.filter(run=first, step=map_step).exists()
        final = WorkflowRun.objects.start_recovery(
            first_page.current_attempt,
            request_key="retry-failed-map-page",
            actor=actor,
        )
        expected_index = 1
        expected_basis_id = source_controller.current_attempt_id

    started = advance_once(final)
    assert [(row.step_id, row.map_index) for row in started] == [
        (page_step.pk, expected_index)
    ]
    execute_started(final)
    continued = advance_once(final)
    final.refresh_from_db()
    assert [(row.step_id, row.map_index) for row in continued] == [(collect_step.pk, -1)]
    with system_context(reason="recovered Map aggregate evidence"):
        controller = StepRun.objects.get(run=final, step=map_step)
        recovered_page = StepRun.objects.get(
            run=final, step=page_step, map_index=expected_index,
        )
        basis = final.recovery_evidence.get(step=map_step, map_index=-1)
        results = controller.output["results"]
        assert controller.current_attempt.cause == AttemptCause.MAP_ENGINE
        assert recovered_page.current_attempt.map_expansion_id == source_attempt.map_expansion_id
    assert basis.source_attempt_id == expected_basis_id
    assert controller.outcome == "succeeded"
    assert results[0] == source_results[0]
    for index in original_failures:
        assert results[index] == {
            "map_index": index,
            "status": "succeeded",
            "outcome": "done",
            "output": {"value": index, "recovered": True},
            "output_present": True,
            "error": "",
        }
    execute_started(final)
    assert CollectItems.inputs[-1] == controller.output
    assert advance_once(final) == []
    final.refresh_from_db()
    assert final.status == "succeeded"

    if scenario == "remaining_sibling":
        with pytest.raises(ValidationError, match="request facts do not match"):
            WorkflowRun.objects.start_recovery(
                remaining_source,
                request_key="recover-map-page-remaining",
                actor=actor,
            )


def test_retained_child_for_start_requires_parent_and_child_read_access(
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    actor = get_user_model().objects.create_user(username="retained-child-owner")
    reader = get_user_model().objects.create_user(username="retained-child-reader")
    source_run, source_step_run, _source_attempt, _child_head, child = _failed_child_handoff(
        actor=actor, dedup_key="recovery-child:permission",
    )

    with pytest.raises(PermissionDenied, match="Parent workflow execution is unavailable"):
        WorkflowRun.objects.retained_child_for_start(
            source_step_run, actor=reader, origin=RunOrigin.WORKFLOW,
        )
    write_relationships([
        RelationshipTuple(to_object_ref(source_run), "reader", to_subject_ref(reader)),
    ])
    with pytest.raises(PermissionDenied, match="Retained child workflow execution is unavailable"):
        WorkflowRun.objects.retained_child_for_start(
            source_step_run, actor=reader, origin=RunOrigin.WORKFLOW,
        )

    write_relationships([
        RelationshipTuple(to_object_ref(child), "reader", to_subject_ref(reader)),
    ])
    retained = WorkflowRun.objects.retained_child_for_start(
        source_step_run, actor=reader, origin=RunOrigin.WORKFLOW,
    )
    assert retained is not None and retained.pk == child.pk


def test_repair_test_retains_exact_source_attempt_and_original_input(
    workflow_engine_tables: None,
) -> None:
    del workflow_engine_tables
    actor = get_user_model().objects.create_user(username="repair-owner")
    workflow, step = _draft(owner=actor)
    with system_context(reason="repair source setup"):
        source_run = WorkflowRun.objects.create(
            workflow=workflow,
            status="running",
            admitted_actor_ref=str(to_subject_ref(actor)),
            created_by=actor,
            input_present=True,
            input=None,
        )
        source_step_run = StepRun.objects.create(
            run=source_run, step=step, status="scheduled"
        )
    source = StepAttempt.objects.claim(source_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(source.pk, lease_token=source.lease_token, at=timezone.now())
    StepAttempt.objects.finalize(
        source.pk,
        lease_token=source.lease_token,
        result=AttemptResult(AttemptResultKind.ERROR, error="broken input mapping"),
        recorded_at=timezone.now(),
    )

    repaired = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=workflow.draft_revision,
        request_key="repair-test",
        subject=None,
        actor=actor,
        input=JsonPresence(True, None),
        scope=WorkflowTestScope.NODE,
        selected_step=step,
        repair_source_attempt=source,
    )
    duplicate = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=workflow.draft_revision,
        request_key="repair-test",
        subject=None,
        actor=actor,
        input=JsonPresence(True, None),
        scope=WorkflowTestScope.NODE,
        selected_step=step,
        repair_source_attempt=source,
    )

    assert duplicate.pk == repaired.pk
    assert repaired.test_repair_source_attempt_id == source.pk
    assert repaired.input_present and repaired.input is None
    with pytest.raises(ValidationError, match="repair evidence"):
        WorkflowRun.objects.start_test(
            workflow,
            expected_revision=workflow.draft_revision,
            request_key="repair-test",
            subject=None,
            actor=actor,
            input=JsonPresence(True, None),
            scope=WorkflowTestScope.NODE,
            selected_step=step,
            repair_source_attempt=None,
        )


def test_repair_context_offers_retained_done_predecessor_as_fixture(
    workflow_engine_tables: None,
) -> None:
    actor = get_user_model().objects.create_user(username="repair-fixture-owner")
    workflow, selected = _draft(owner=actor)
    with system_context(reason="repair predecessor setup"):
        predecessor = Step.objects.create(
            workflow=workflow,
            key="predecessor",
            name="Predecessor",
            step_class="agent_session",
            is_entry=True,
        )
        selected.is_entry = False
        selected.save(update_fields={"is_entry", "updated_at"})
        Edge.objects.create(workflow=workflow, source=predecessor, target=selected)
        source_run = WorkflowRun.objects.create(
            workflow=workflow,
            status="running",
            admitted_actor_ref=str(to_subject_ref(actor)),
            created_by=actor,
        )
        predecessor_run = StepRun.objects.create(
            run=source_run,
            step=predecessor,
            status="scheduled",
        )
        selected_run = StepRun.objects.create(
            run=source_run,
            step=selected,
            status="scheduled",
        )
    predecessor_attempt = StepAttempt.objects.claim(
        predecessor_run,
        claimed_at=timezone.now(),
    ).attempt
    StepAttempt.objects.admit_invocation(
        predecessor_attempt.pk,
        lease_token=predecessor_attempt.lease_token,
        at=timezone.now(),
    )
    StepAttempt.objects.finalize(
        predecessor_attempt.pk,
        lease_token=predecessor_attempt.lease_token,
        result=AttemptResult(AttemptResultKind.DONE, output_present=True, output={"kept": True}),
        recorded_at=timezone.now(),
    )
    selected_attempt = StepAttempt.objects.claim(selected_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(
        selected_attempt.pk,
        lease_token=selected_attempt.lease_token,
        at=timezone.now(),
    )
    StepAttempt.objects.finalize(
        selected_attempt.pk,
        lease_token=selected_attempt.lease_token,
        result=AttemptResult(AttemptResultKind.ERROR, error="failed"),
        recorded_at=timezone.now(),
    )

    context = WorkflowRun.objects.test_repair_context(selected_attempt, actor=actor)

    assert [fixture.attempt_id for fixture in context.fixtures] == [predecessor_attempt.sqid]


def test_output_fixture_is_immutable_nonphysical_retained_evidence(
    workflow_engine_tables: None,
) -> None:
    actor = get_user_model().objects.create_user(username="fixture-runner")
    workflow, selected = _draft(owner=actor)
    with system_context(reason="fixture source setup"):
        fixture_step = Step.objects.create(
            workflow=workflow, key="selected", name="Selected", step_class="agent_session"
        )
        Edge.objects.create(workflow=workflow, source=selected, target=fixture_step)
        workflow.refresh_from_db()

    run = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=workflow.draft_revision,
        request_key="fixture-output",
        subject=None,
        actor=actor,
        fixtures=(
            FixtureSpec(
                step_key=selected.key,
                role=FixtureRole.OUTPUT,
                value=JsonPresence(True, {"answer": None}),
                outcome="fixture",
            ),
        ),
        scope=WorkflowTestScope.NODE,
        selected_step=fixture_step,
    )

    with system_context(reason="inspect fixture evidence"):
        fixture = WorkflowTestFixture.objects.get(run=run)
        attempt = StepAttempt.objects.get(test_fixture=fixture)
        assert attempt.cause == AttemptCause.TEST_FIXTURE
        assert attempt.result_kind == AttemptResultKind.DONE
        assert attempt.started_at is None
        assert attempt.output_present is True
        assert attempt.output == {"answer": None}
        assert run.step_runs.get(step__key=selected.key).status == "succeeded"
        assert run.steps_taken == 0
        assert not WorkflowDispatch.objects.filter(step_attempt=attempt).exists()
        with pytest.raises(TypeError, match="immutable"):
            WorkflowTestFixture.objects.filter(pk=fixture.pk).update(outcome="changed")
        with pytest.raises(TypeError, match="WorkflowRunManager"):
            WorkflowTestFixture.objects.bulk_create([fixture])
        with pytest.raises(TypeError, match="immutable"):
            WorkflowTestFixture.objects.bulk_update([fixture], ["outcome"])
        queryset = WorkflowTestFixture.objects.filter(pk=fixture.pk)
        with pytest.raises(TypeError, match="immutable"):
            queryset.update(created_by=None, updated_by=None)
        with pytest.raises(TypeError, match="retained admission facts"):
            queryset.delete()
        with pytest.raises(TypeError, match="retained admission facts"):
            queryset._raw_delete(using=queryset.db)


def test_setup_plan_uses_graph_effects_and_fixture_substitution(
    workflow_engine_tables: None,
) -> None:
    actor = get_user_model().objects.create_user(username="test-plan-owner")
    workflow, entry = _draft(owner=actor)
    plan = WorkflowRun.objects.test_setup_plan(
        workflow,
        expected_revision=workflow.draft_revision,
        actor=actor,
        fixtures=(
            FixtureSpec(
                entry.key,
                FixtureRole.OUTPUT,
                JsonPresence(True, {"planned": None}),
            ),
        ),
    )

    assert plan.source_step_id is None
    assert plan.snapshot_step_id is None
    assert plan.diagnostics == ()
    assert plan.requires_map_item is False
    assert len(plan.operations) == 1
    assert plan.operations[0].key == entry.key
    assert plan.operations[0].replaced_by_output is True
    assert plan.operations[0].effect.value == "unknown"


def test_setup_plan_reports_required_map_item_without_rejecting_incomplete_setup(
    workflow_engine_tables: None,
) -> None:
    actor = get_user_model().objects.create_user(username="test-plan-map-item")
    workflow, controller = _draft(owner=actor)
    with system_context(reason="test plan Map body"):
        controller.step_class = "map"
        controller.config = {"target_step": "body", "items": [None]}
        controller.save(update_fields={"step_class", "config"})
        body = Step.objects.create(
            workflow=workflow,
            key="body",
            name="Body",
            step_class="fixture",
        )
        workflow.refresh_from_db()

    plan = WorkflowRun.objects.test_setup_plan(
        workflow,
        expected_revision=workflow.draft_revision,
        actor=actor,
        scope=WorkflowTestScope.NODE,
        selected_step=body,
    )

    assert plan.requires_map_item is True
    assert len(plan.required_fixtures) == 1
    assert plan.required_fixtures[0].role == "map_item"
    assert plan.required_fixtures[0].step_key == "body"
    assert plan.required_fixtures[0].item_index_required is True
    assert plan.required_fixtures[0].satisfied is False


def test_test_snapshot_freshness_ignores_label_but_detects_exact_config_types(
    workflow_engine_tables: None,
) -> None:
    actor = get_user_model().objects.create_user(username="test-plan-freshness")
    workflow, entry = _draft(owner=actor)
    snapshot = Workflow.objects.test_snapshot(
        workflow,
        expected_revision=workflow.draft_revision,
        require_readiness=False,
    )
    with system_context(reason="test snapshot selected step"):
        snapshot_entry = snapshot.steps.get(key=entry.key)

    with system_context(reason="presentation-only test change"):
        entry.name = "Renamed entry"
        entry.save(update_fields={"name"})
        workflow.refresh_from_db()
    label_plan = WorkflowRun.objects.test_setup_plan(
        snapshot,
        expected_revision=snapshot.draft_revision,
        actor=actor,
        scope=WorkflowTestScope.NODE,
        selected_step=snapshot_entry,
    )
    assert label_plan.is_current is True

    with system_context(reason="semantic test change"):
        entry.config = {"value": True}
        entry.save(update_fields={"config"})
        workflow.refresh_from_db()
    changed = WorkflowRun.objects.test_setup_plan(
        snapshot,
        expected_revision=snapshot.draft_revision,
        actor=actor,
        scope=WorkflowTestScope.NODE,
        selected_step=snapshot_entry,
    )
    assert [(reason.code, reason.step_key) for reason in changed.freshness] == [
        ("config_changed", entry.key)
    ]

    with system_context(reason="exact JSON type test change"):
        entry.config = {"value": 1}
        entry.save(update_fields={"config"})
    exact_type = WorkflowRun.objects.test_setup_plan(
        snapshot,
        expected_revision=snapshot.draft_revision,
        actor=actor,
        scope=WorkflowTestScope.NODE,
        selected_step=snapshot_entry,
    )
    assert [reason.code for reason in exact_type.freshness] == ["config_changed"]


def test_draft_retest_compares_previous_snapshot_semantics_and_source_identity(
    workflow_engine_tables: None,
) -> None:
    actor = get_user_model().objects.create_user(username="test-plan-previous-run")
    workflow, entry = _draft(owner=actor)
    run = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=workflow.draft_revision,
        request_key="test-plan-previous-run",
        subject=None,
        actor=actor,
        scope=WorkflowTestScope.NODE,
        selected_step=entry,
    )
    original_source_id = entry.sqid
    with system_context(reason="change previous test semantics"):
        entry.config = {"changed": True}
        entry.save(update_fields={"config"})
        workflow.refresh_from_db()

    changed = WorkflowRun.objects.test_setup_plan(
        workflow,
        expected_revision=workflow.draft_revision,
        actor=actor,
        scope=WorkflowTestScope.NODE,
        selected_step=entry,
        previous_run=run,
    )
    assert ("config_changed", entry.key) in [
        (reason.code, reason.step_key) for reason in changed.freshness
    ]

    with system_context(reason="rename original selected source"):
        entry.key = "renamed-entry"
        entry.save(update_fields={"key"})
        workflow.refresh_from_db()
    with system_context(reason="read pinned selected step"):
        pinned = run.workflow.steps.get(pk=run.test_step_id)
    historical = WorkflowRun.objects.test_setup_plan(
        run.workflow,
        expected_revision=run.workflow.draft_revision,
        actor=actor,
        scope=WorkflowTestScope.NODE,
        selected_step=pinned,
        previous_run=run,
    )
    assert historical.source_step_id == original_source_id
    assert historical.snapshot_step_id == pinned.sqid


def test_captured_fixture_sources_are_bounded_and_payload_is_selected_separately(
    workflow_engine_tables: None,
) -> None:
    actor = get_user_model().objects.create_user(username="captured-source-owner")
    workflow, entry = _draft(owner=actor)
    with system_context(reason="captured source target"):
        selected = Step.objects.create(
            workflow=workflow,
            key="selected",
            name="Selected",
            step_class="agent_session",
        )
        Edge.objects.create(workflow=workflow, source=entry, target=selected)
        workflow.refresh_from_db()
    first = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=workflow.draft_revision,
        request_key="captured-source-first",
        subject=None,
        actor=actor,
        scope=WorkflowTestScope.NODE,
        selected_step=selected,
        fixtures=(
            FixtureSpec(
                entry.key,
                FixtureRole.OUTPUT,
                JsonPresence(True, {"retained": None}),
                outcome="accepted",
            ),
        ),
    )
    with system_context(reason="captured source identity"):
        source_attempt = StepAttempt.objects.get(test_fixture__run=first)

    with CaptureQueriesContext(connection) as summary_queries:
        page = StepAttempt.objects.eligible_test_fixture_sources(
            workflow,
            actor=actor,
            role=FixtureRole.OUTPUT,
            step_key=entry.key,
            first=1,
        )
    summary_sql = " ".join(query["sql"] for query in summary_queries.captured_queries)
    assert '."output"' not in summary_sql
    assert '."input"' not in summary_sql
    assert '."checkpoint"' not in summary_sql
    assert '."map_item"' not in summary_sql
    selected_source = StepAttempt.objects.test_fixture_source(
        workflow,
        actor=actor,
        attempt_id=source_attempt.sqid,
        role=FixtureRole.OUTPUT,
        step_key=entry.key,
    )

    assert [item.attempt_id for item in page.items] == [source_attempt.sqid]
    assert not hasattr(page.items[0], "value")
    assert selected_source is not None
    assert selected_source.value == JsonPresence(True, {"retained": None})
    assert selected_source.summary.outcome == "accepted"
    unrelated, _ = _draft("Unrelated captured lineage", owner=actor)
    assert StepAttempt.objects.test_fixture_source(
        unrelated,
        actor=actor,
        attempt_id=source_attempt.sqid,
        role=FixtureRole.OUTPUT,
        step_key=entry.key,
    ) is None

    second = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=workflow.draft_revision,
        request_key="captured-source-second",
        subject=None,
        actor=actor,
        scope=WorkflowTestScope.NODE,
        selected_step=selected,
        fixtures=(
            FixtureSpec(
                entry.key,
                FixtureRole.OUTPUT,
                captured_attempt_id=source_attempt.sqid,
            ),
        ),
    )
    with system_context(reason="captured source retained fixture"):
        fixture = WorkflowTestFixture.objects.get(run=second)
    assert fixture.captured_attempt_id == source_attempt.pk
    assert (fixture.value_present, fixture.value, fixture.outcome) == (
        True,
        {"retained": None},
        "accepted",
    )


def test_freshness_propagates_upstream_semantics_to_selected_evidence(
    workflow_engine_tables: None,
) -> None:
    actor = get_user_model().objects.create_user(username="test-plan-propagation")
    workflow, entry = _draft(owner=actor)
    with system_context(reason="freshness dependency graph"):
        selected = Step.objects.create(
            workflow=workflow,
            key="dependent",
            name="Dependent",
            step_class="agent_session",
        )
        Edge.objects.create(workflow=workflow, source=entry, target=selected)
        workflow.refresh_from_db()
    fixture = FixtureSpec(
        entry.key,
        FixtureRole.OUTPUT,
        JsonPresence(True, {"source": True}),
    )
    run = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=workflow.draft_revision,
        request_key="test-plan-propagation",
        subject=None,
        actor=actor,
        scope=WorkflowTestScope.NODE,
        selected_step=selected,
        fixtures=(fixture,),
    )
    with system_context(reason="change upstream semantics"):
        entry.config = {"semantic": True}
        entry.save(update_fields={"config"})
        workflow.refresh_from_db()
    plan = WorkflowRun.objects.test_setup_plan(
        workflow,
        expected_revision=workflow.draft_revision,
        actor=actor,
        scope=WorkflowTestScope.NODE,
        selected_step=selected,
        fixtures=(fixture,),
        previous_run=run,
    )
    assert ("config_changed", entry.key) in [
        (reason.code, reason.step_key) for reason in plan.freshness
    ]
    assert ("dependency_changed", selected.key) in [
        (reason.code, reason.step_key) for reason in plan.freshness
    ]


def test_output_fixture_can_replace_whole_test_entry_without_duplicate_slot(
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    actor = get_user_model().objects.create_user(username="fixture-entry")
    workflow, entry = _draft(owner=actor)
    run = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=workflow.draft_revision,
        request_key="fixture-entry",
        subject=None,
        actor=actor,
        fixtures=(
            FixtureSpec(
                entry.key,
                FixtureRole.OUTPUT,
                JsonPresence(True, {"entry": "substituted"}),
            ),
        ),
    )
    with system_context(reason="apply due entry fixture"):
        advance_once(run)
    with system_context(reason="inspect fixture entry"):
        rows = list(run.step_runs.all())
        assert len(rows) == 1
        assert rows[0].status == "succeeded"
        assert rows[0].current_attempt.cause == AttemptCause.TEST_FIXTURE


def test_whole_output_fixture_waits_for_graph_reachability(
    workflow_engine_tables: None,
) -> None:
    actor = get_user_model().objects.create_user(username="fixture-reachability")
    workflow, entry = _draft(owner=actor)
    with system_context(reason="fixture reachability graph"):
        fixture_step = Step.objects.create(
            workflow=workflow, key="fixture", name="Fixture", step_class="agent_session"
        )
        successor = Step.objects.create(
            workflow=workflow, key="side-effect", name="Side effect", step_class="agent_session"
        )
        Edge.objects.create(workflow=workflow, source=entry, target=fixture_step)
        Edge.objects.create(workflow=workflow, source=fixture_step, target=successor)
        workflow.refresh_from_db()
    run = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=workflow.draft_revision,
        request_key="fixture-reachability",
        subject=None,
        actor=actor,
        fixtures=(
            FixtureSpec(
                fixture_step.key,
                FixtureRole.OUTPUT,
                JsonPresence(True, {"fixture": True}),
            ),
        ),
    )
    with system_context(reason="inspect fixture reachability"):
        assert not StepAttempt.objects.filter(test_fixture__run=run).exists()
        assert not run.step_runs.filter(step__key__in=["fixture", "side-effect"]).exists()


def test_whole_map_body_output_fixture_stays_bound_to_expansion(
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    actor = get_user_model().objects.create_user(username="fixture-map-output")
    workflow, controller = _draft(owner=actor)
    with system_context(reason="whole Map fixture graph"):
        controller.step_class = "map"
        controller.config = {"target_step": "body", "items": ["raw"]}
        controller.save(update_fields={"step_class", "config"})
        body = Step.objects.create(
            workflow=workflow, key="body", name="Body", step_class="fixture"
        )
        workflow.max_steps = 1
        workflow.save(update_fields={"max_steps"})
        workflow.refresh_from_db()
    run = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=workflow.draft_revision,
        request_key="fixture-map-output",
        subject=None,
        actor=actor,
        fixtures=(
            FixtureSpec(
                body.key,
                FixtureRole.OUTPUT,
                JsonPresence(True, {"fixture": "body"}),
                item_index=0,
            ),
        ),
    )
    advance_once(run)
    run.refresh_from_db()
    with system_context(reason="inspect whole Map fixture"):
        body_run = run.step_runs.get(step__key="body", map_index=0)
        fixture_attempt = StepAttempt.objects.get(test_fixture__run=run, cause=AttemptCause.TEST_FIXTURE)
        assert body_run.current_attempt_id == fixture_attempt.pk
        assert body_run.current_map_expansion_id is not None
        assert fixture_attempt.started_at is None
        assert run.steps_taken == 1


def test_whole_map_output_fixture_skips_expansion_when_reached(
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    actor = get_user_model().objects.create_user(username="fixture-map-controller")
    workflow, controller = _draft(owner=actor)
    with system_context(reason="Map controller fixture graph"):
        controller.step_class = "map"
        controller.config = {"target_step": "body", "items": [1, 2]}
        controller.save(update_fields={"step_class", "config"})
        Step.objects.create(workflow=workflow, key="body", name="Body", step_class="fixture")
        workflow.refresh_from_db()
    run = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=workflow.draft_revision,
        request_key="fixture-map-controller",
        subject=None,
        actor=actor,
        fixtures=(
            FixtureSpec(
                controller.key,
                FixtureRole.OUTPUT,
                JsonPresence(True, {"results": []}),
                outcome="succeeded",
            ),
        ),
    )
    advance_once(run)
    run.refresh_from_db()
    with system_context(reason="inspect Map controller fixture"):
        controller_run = run.step_runs.get(step__key=controller.key)
        assert controller_run.current_attempt.cause == AttemptCause.TEST_FIXTURE
        assert not run.step_runs.filter(step__key="body").exists()
        assert run.steps_taken == 0


def test_test_fixture_retry_requires_exact_json_presence_and_facts(
    workflow_engine_tables: None,
) -> None:
    actor = get_user_model().objects.create_user(username="fixture-retry")
    workflow, selected = _draft(owner=actor)
    with system_context(reason="fixture retry source setup"):
        fixture_step = Step.objects.create(
            workflow=workflow, key="fixture-source", name="Fixture source", step_class="agent_session"
        )
        Edge.objects.create(workflow=workflow, source=selected, target=fixture_step)
        workflow.refresh_from_db()
    fixture = FixtureSpec(
        fixture_step.key,
        FixtureRole.OUTPUT,
        JsonPresence(True, 1),
    )
    first = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=workflow.draft_revision,
        request_key="fixture-retry",
        subject=None,
        actor=actor,
        fixtures=(fixture,),
    )
    duplicate = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=workflow.draft_revision,
        request_key="fixture-retry",
        subject=None,
        actor=actor,
        fixtures=(fixture,),
    )
    assert duplicate.pk == first.pk
    with pytest.raises(ValidationError, match="fixture facts"):
        WorkflowRun.objects.start_test(
            workflow,
            expected_revision=workflow.draft_revision,
            request_key="fixture-retry",
            subject=None,
            actor=actor,
            fixtures=(FixtureSpec(fixture_step.key, FixtureRole.OUTPUT, JsonPresence(True, True)),),
        )


def test_fixture_generic_writes_require_the_run_owner(
    workflow_engine_tables: None,
) -> None:
    actor = get_user_model().objects.create_user(username="fixture-owner-guard")
    workflow, selected = _draft(owner=actor)
    run = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=workflow.draft_revision,
        request_key="fixture-owner-guard",
        subject=None,
        actor=actor,
        scope=WorkflowTestScope.NODE,
        selected_step=selected,
    )
    row = WorkflowTestFixture(
        run=run,
        step=run.test_step,
        role=FixtureRole.OUTPUT,
        value_present=True,
        value={"forged": True},
    )
    with pytest.raises(TypeError, match="WorkflowRunManager"):
        row.save()
    with pytest.raises(TypeError, match="WorkflowRunManager"):
        WorkflowTestFixture.objects.bulk_create([row])


def test_fixture_slot_uniqueness_rejects_signal_reentry(
    workflow_engine_tables: None,
) -> None:
    actor = get_user_model().objects.create_user(username="fixture-signal-guard")
    workflow, entry = _draft(owner=actor)

    def reenter(sender: object, instance: WorkflowTestFixture, created: bool, **kwargs: object) -> None:
        del sender, created, kwargs
        forged = WorkflowTestFixture(
            step=instance.step,
            role=FixtureRole.OUTPUT,
            value_present=True,
            value={"forged": True},
        )
        WorkflowTestFixture.objects._create_batch(instance.run, (forged,), alias="default")

    post_save.connect(reenter, sender=WorkflowTestFixture, weak=False)
    try:
        with pytest.raises(ValidationError) as error:
            WorkflowRun.objects.start_test(
                workflow,
                expected_revision=workflow.draft_revision,
                request_key="fixture-signal-guard",
                subject=None,
                actor=actor,
                fixtures=(
                    FixtureSpec(
                        entry.key,
                        FixtureRole.OUTPUT,
                        JsonPresence(True, {"safe": True}),
                    ),
                ),
            )
        assert error.value.error_dict["__all__"][0].code == "unique_together"
    finally:
        post_save.disconnect(reenter, sender=WorkflowTestFixture)
    with system_context(reason="verify fixture signal rollback"):
        assert not WorkflowRun.objects.filter(dedup_key__contains="fixture-signal-guard").exists()
        assert not WorkflowTestFixture.objects.exists()


def test_node_snapshot_reuse_revalidates_whole_readiness(
    workflow_engine_tables: None,
) -> None:
    actor = get_user_model().objects.create_user(username="scope-readiness")
    workflow, selected = _draft(owner=actor)
    with system_context(reason="unrelated incomplete node"):
        Step.objects.create(
            workflow=workflow,
            key="incomplete",
            name="Incomplete",
            step_class="fixture",
        )
        workflow.refresh_from_db()
    node = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=workflow.draft_revision,
        request_key="node-ready",
        subject=None,
        actor=actor,
        scope=WorkflowTestScope.NODE,
        selected_step=selected,
    )
    assert node.workflow.status == WorkflowStatus.TEST
    with pytest.raises(ValidationError) as error:
        WorkflowRun.objects.start_test(
            workflow,
            expected_revision=workflow.draft_revision,
            request_key="whole-not-ready",
            subject=None,
            actor=actor,
        )
    incomplete = Step.system_queryset().get(workflow=node.workflow, key="incomplete")
    assert error.value.message_dict == {f"node.{incomplete.pk}.key": ["Step is not reachable from the entry step."]}


@pytest.mark.parametrize("item", [None, "raw", {"item": "mapping"}])
@pytest.mark.parametrize("explicit", [False, True])
def test_map_item_fixture_is_captured_on_real_body_attempt(
    workflow_engine_tables: None,
    item: object,
    explicit: bool,
) -> None:
    actor = get_user_model().objects.create_user(username=f"map-fixture-{type(item).__name__}")
    workflow, controller = _draft(owner=actor)
    with system_context(reason="map fixture graph"):
        controller.step_class = "map"
        controller.config = {"target_step": "body", "items": ["draft-only"]}
        controller.save(update_fields={"step_class", "config"})
        body = Step.objects.create(
            workflow=workflow,
            key="body",
            name="Body",
            step_class="fixture",
            input_binding={"kind": "map_item", "path": []} if explicit else None,
        )
        workflow.refresh_from_db()
    run = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=workflow.draft_revision,
            request_key=f"map-fixture-{type(item).__name__}-{explicit}",
        subject=None,
        actor=actor,
        scope=WorkflowTestScope.NODE,
        selected_step=body,
        fixtures=(
            FixtureSpec(
                "body", FixtureRole.MAP_ITEM, JsonPresence(True, item), item_index=0
            ),
        ),
    )
    with system_context(reason="claim node body fixture"):
        with transaction.atomic():
            locked = WorkflowRun.objects.select_for_update().get(pk=run.pk)
            claimed = engine._claim_due_steps(locked, timestamp=timezone.now(), alias="default")
        assert len(claimed) == 1
        attempt = StepAttempt.objects.get(step_run__run=run, cause=AttemptCause.INITIAL)
        assert attempt.test_fixture.role == FixtureRole.MAP_ITEM
        assert attempt.input_present is True
        expected = item if explicit or isinstance(item, dict) else {"item": item}
        assert attempt.input == expected
        assert attempt.started_at is None
        assert StepAttempt.objects.filter(test_fixture=run.test_fixtures.get()).count() == 1


def test_selected_map_body_requires_one_raw_item_fixture(
    workflow_engine_tables: None,
) -> None:
    actor = get_user_model().objects.create_user(username="map-fixture-required")
    workflow, controller = _draft(owner=actor)
    with system_context(reason="required Map fixture graph"):
        controller.step_class = "map"
        controller.config = {"target_step": "body", "items": [1]}
        controller.save(update_fields={"step_class", "config"})
        body = Step.objects.create(
            workflow=workflow, key="body", name="Body", step_class="fixture"
        )
        workflow.refresh_from_db()
    with pytest.raises(ValidationError, match="requires exactly one Map item"):
        WorkflowRun.objects.start_test(
            workflow,
            expected_revision=workflow.draft_revision,
            request_key="map-fixture-required",
            subject=None,
            actor=actor,
            scope=WorkflowTestScope.NODE,
            selected_step=body,
        )


def _failed_child_handoff(
    *, actor: object, dedup_key: str,
) -> tuple[WorkflowRun, StepRun, StepAttempt, Workflow, WorkflowRun]:
    """Retain a child created immediately before its parent operation failed."""

    child_head = _published_wait_workflow(actor=actor)
    with system_context(reason="failed child handoff fixture"):
        source_workflow, source_step = _draft(name="Recovery parent", owner=actor)
        source_run = WorkflowRun.objects.create(
            workflow=source_workflow, status="running",
            admitted_actor_ref=str(to_subject_ref(actor)), created_by=actor,
            input_present=True, input={"parent": "retained"},
        )
        source_step_run = StepRun.objects.create(
            run=source_run, step=source_step, status="scheduled",
        )
    source_attempt = StepAttempt.objects.claim(
        source_step_run,
        input=AttemptInput(True, {"parent": "retained"}, {"kind": "run_input"}),
        claimed_at=timezone.now(),
    ).attempt
    StepAttempt.objects.admit_invocation(
        source_attempt.pk, lease_token=source_attempt.lease_token, at=timezone.now(),
    )
    child = engine.start(
        child_head, subject=None, actor=actor,
        parent_step_run=source_step_run, parent_relation="continuation", dedup_key=dedup_key,
        origin=RunOrigin.WORKFLOW,
        input=JsonPresence(True, {"child": "retained"}),
    )
    StepAttempt.objects.finalize(
        source_attempt.pk,
        lease_token=source_attempt.lease_token,
        result=AttemptResult(AttemptResultKind.ERROR, error="uncertain child handoff"),
        recorded_at=timezone.now(),
    )
    source_step_run.refresh_from_db()
    assert source_step_run.status == "failed"
    return source_run, source_step_run, source_attempt, child_head, child


def _published_wait_workflow(*, actor: object) -> Workflow:
    with system_context(reason="recovery child workflow fixture"):
        workflow = Workflow.objects.create(
            key=f"recovery-child-{uuid.uuid4().hex}",
            name="Recovery child",
            created_by=actor,
        )
        Step.objects.create(
            workflow=workflow,
            key="entry",
            name="Entry",
            step_class="wait",
            config={"until": (timezone.now() + timedelta(hours=1)).isoformat()},
            input_binding={"kind": "workflow_input", "path": []},
            is_entry=True,
        )
        workflow.publish()
    return workflow


def _execute_recovery(
    source_attempt: StepAttempt, *, actor: object, request_key: str,
) -> tuple[WorkflowRun, StepAttempt]:
    recovery = WorkflowRun.objects.start_recovery(
        source_attempt, request_key=request_key, actor=actor,
    )
    with system_context(reason="child handoff recovery dispatch"):
        advance = WorkflowDispatch.objects.get(
            run=recovery, kind=WorkflowDispatchKind.ADVANCE,
        )
    assert engine.advance_dispatch(advance.pk)["claimed"] == 1
    with system_context(reason="child handoff recovery invocation"):
        recovered_step = StepRun.objects.get(run=recovery)
        attempt = recovered_step.current_attempt
        execute = WorkflowDispatch.objects.get(step_attempt=attempt)
    assert engine.execute_dispatch(
        execute.pk, attempt.pk, attempt.lease_token,
    )["executed"] == 1
    with system_context(reason="child handoff recovery result"):
        attempt.refresh_from_db()
    return recovery, attempt


def test_fresh_recovery_continues_from_retained_success_blocked_by_skipped_merge(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retained successful recovery resumes after its exact skipped sibling."""

    del workflow_engine_tables, no_workflow_queue
    actor = get_user_model().objects.create_user(username="recovery-merge-continuation-owner")

    class RecoverableReview(StepImpl):
        calls = 0

        @classmethod
        def recovery_capability(cls, *, attempt: object) -> RecoveryCapability:
            del attempt
            return RecoveryCapability(RecoveryMode.FRESH)

        def run(self, step_run: object, *, now: object) -> StepResult:
            del step_run, now
            type(self).calls += 1
            return StepResult.done({"reviewed": True}, outcome="completed")

    class RecoverableApply(StepImpl):
        calls = 0

        @classmethod
        def recovery_capability(cls, *, attempt: object) -> RecoveryCapability:
            del attempt
            return RecoveryCapability(RecoveryMode.FRESH)

        def run(self, step_run: object, *, now: object) -> StepResult:
            del step_run, now
            type(self).calls += 1
            return StepResult.done({"source": "revised"}, outcome="continued")

    class ContinueStep(StepImpl):
        def run(self, step_run: object, *, now: object) -> StepResult:
            del now
            return StepResult.done(step_run.input, outcome="done")

    with system_context(reason="recovery merge continuation fixture"):
        workflow = Workflow.objects.create(
            name="Recovery merge continuation",
            created_by=actor,
            output_schema={"type": "object"},
            result_rules=[{
                "outcome": "completed",
                "producer": "terminal",
                "when_outcome": "done",
                "binding": {"kind": "step_output", "step_key": "terminal", "path": []},
            }],
        )
        direct = Step.objects.create(
            workflow=workflow, key="direct", name="Direct", step_class="fixture", is_entry=True,
        )
        review = Step.objects.create(
            workflow=workflow, key="review", name="Review", step_class="fixture",
        )
        apply = Step.objects.create(
            workflow=workflow, key="apply", name="Apply", step_class="fixture",
        )
        merge = Step.objects.create(
            workflow=workflow, key="merge", name="Merge", step_class="fixture",
            join_rule="none_failed_min_one_success",
            input_binding={"kind": "step_output", "step_key": "apply", "path": []},
        )
        terminal = Step.objects.create(
            workflow=workflow, key="terminal", name="Terminal", step_class="fixture",
            input_binding={"kind": "step_output", "step_key": "merge", "path": []},
        )
        Edge.objects.create(workflow=workflow, source=direct, target=merge, condition="done")
        Edge.objects.create(workflow=workflow, source=review, target=apply, condition="completed")
        Edge.objects.create(workflow=workflow, source=apply, target=merge, condition="continued")
        Edge.objects.create(workflow=workflow, source=merge, target=terminal, condition="done")
        source_run = WorkflowRun.objects.create(
            workflow=workflow, status="running", admitted_actor_ref=str(to_subject_ref(actor)),
            created_by=actor,
        )
        StepRun.objects.create(run=source_run, step=direct, status="skipped")
        failed_review = StepRun.objects.create(run=source_run, step=review, status="scheduled")
    failed_attempt = StepAttempt.objects.claim(failed_review, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(
        failed_attempt.pk, lease_token=failed_attempt.lease_token, at=timezone.now(),
    )
    StepAttempt.objects.finalize(
        failed_attempt.pk, lease_token=failed_attempt.lease_token,
        result=AttemptResult(AttemptResultKind.ERROR, error="initial apply failure"),
        recorded_at=timezone.now(),
    )
    original_resolve = type(apply).resolve_impl
    monkeypatch.setattr(
        type(apply), "resolve_impl",
        lambda self, field: (
            RecoverableReview if self.pk == review.pk else RecoverableApply
            if self.pk == apply.pk else ContinueStep
            if self.pk in {merge.pk, terminal.pk} else original_resolve(self, field)
        ),
    )
    ordinary = WorkflowRun.objects.start_recovery(
        failed_attempt, request_key="ordinary-retained-merge", actor=actor,
    )
    with system_context(reason="ordinary retained merge admission"):
        ordinary_rows = {
            row.step.key: row for row in ordinary.step_runs.select_related("step")
        }
    assert ordinary_rows["direct"].status == "skipped"
    assert ordinary_rows["review"].status == "scheduled"

    with system_context(reason="failed recovery result fixture"):
        prior = WorkflowRun.objects.create(
            workflow=workflow, origin=RunOrigin.RECOVERY, status="running",
            recovery_source_attempt=failed_attempt, recovery_request_actor_ref=str(to_subject_ref(actor)),
            recovery_mode=RecoveryMode.FRESH, admitted_actor_ref=str(to_subject_ref(actor)),
            created_by=actor,
        )
        prior_review = StepRun.objects.create(run=prior, step=review, status="scheduled")
    prior_review_attempt = StepAttempt.objects.claim(
        prior_review, claimed_at=timezone.now(),
    ).attempt
    StepAttempt.objects.admit_invocation(
        prior_review_attempt.pk,
        lease_token=prior_review_attempt.lease_token,
        at=timezone.now(),
    )
    StepAttempt.objects.finalize(
        prior_review_attempt.pk,
        lease_token=prior_review_attempt.lease_token,
        result=AttemptResult(
            AttemptResultKind.DONE, output_present=True,
            output={"reviewed": True}, outcome="completed",
        ),
        recorded_at=timezone.now(),
    )
    with system_context(reason="retained Apply success fixture"):
        prior_apply = StepRun.objects.create(run=prior, step=apply, status="scheduled")
    prior_apply_attempt = StepAttempt.objects.claim(
        prior_apply, claimed_at=timezone.now(),
    ).attempt
    StepAttempt.objects.admit_invocation(
        prior_apply_attempt.pk,
        lease_token=prior_apply_attempt.lease_token,
        at=timezone.now(),
    )
    StepAttempt.objects.finalize(
        prior_apply_attempt.pk,
        lease_token=prior_apply_attempt.lease_token,
        result=AttemptResult(
            AttemptResultKind.DONE, output_present=True,
            output={"source": "revised"}, outcome="continued",
        ),
        recorded_at=timezone.now(),
    )
    with system_context(reason="retain failed recovery finalization"):
        prior.refresh_from_db()
        prior.mark_failed(workflow_result_terminal_match_error(0))

    continuation = WorkflowRun.objects.start_recovery(
        failed_attempt, request_key="continue-retained-merge", actor=actor,
        prior_recovery=prior,
    )
    with system_context(reason="retained merge continuation admission"):
        rows = {row.step.key: row for row in continuation.step_runs.select_related("step")}
        assert rows["direct"].status == "skipped"
        assert rows["merge"].status == "scheduled"
        assert (
            continuation.recovery_evidence.get(step=apply).source_attempt_id
            == prior_apply_attempt.pk
        )
    assert RecoverableReview.calls == 0
    assert RecoverableApply.calls == 0
    assert len(advance_once(continuation)) == 1
    execute_started(continuation)
    assert len(advance_once(continuation)) == 1
    execute_started(continuation)
    assert advance_once(continuation) == []
    continuation.refresh_from_db()
    assert continuation.status == "succeeded"
    assert continuation.result["output"] == {"source": "revised"}
    assert RecoverableReview.calls == 0
    assert RecoverableApply.calls == 0


@pytest.mark.parametrize("child_finishes_before_recovery", [False, True])
def test_native_call_recovery_retains_child_and_consumes_exact_completion(
    workflow_engine_tables: None,
    child_finishes_before_recovery: bool,
) -> None:
    """An early or late child finish wakes the same child slot through FRESH recovery."""

    actor = get_user_model().objects.create_user(username=f"call-recovery-{child_finishes_before_recovery}")
    child_head = _published_wait_workflow(actor=actor)
    with system_context(reason="native call recovery source fixture"):
        child_version = child_head.published_versions.get(status=WorkflowStatus.PUBLISHED)
        source_workflow, source_step = _draft(name="Native call recovery parent", owner=actor)
        source_step.step_class = "call_workflow"
        source_step.config = {
            "workflow_key": child_head.key,
            "expected_input_schema": child_version.input_schema,
            "expected_output_schema": child_version.output_schema,
            "expected_subject": child_version.subject_declaration,
            "expected_outcomes": ["completed"],
        }
        source_step.save(update_fields={"step_class", "config"})
        payload = {"input": {"child": "retained"}}
        source_run = WorkflowRun.objects.create(
            workflow=source_workflow,
            status="running",
            admitted_actor_ref=str(to_subject_ref(actor)),
            created_by=actor,
            input_present=True,
            input=payload,
        )
        source_step_run = StepRun.objects.create(
            run=source_run,
            step=source_step,
            status="scheduled",
            input=payload,
        )
    source_attempt = StepAttempt.objects.claim(
        source_step_run,
        input=AttemptInput(True, payload, {"kind": "run_input"}),
        claimed_at=timezone.now(),
    ).attempt
    StepAttempt.objects.admit_invocation(
        source_attempt.pk,
        lease_token=source_attempt.lease_token,
        at=timezone.now(),
    )
    child = engine.start(
        child_version,
        subject=None,
        actor=actor,
        parent_step_run=source_step_run,
        parent_relation="owned_call",
        origin=RunOrigin.WORKFLOW,
        input=JsonPresence(True, payload["input"]),
    )
    StepAttempt.objects.finalize(
        source_attempt.pk,
        lease_token=source_attempt.lease_token,
        result=AttemptResult(AttemptResultKind.ERROR, error="uncertain child handoff"),
        recorded_at=timezone.now(),
    )
    with system_context(reason="publish a newer keyed child before call recovery"):
        child_head.name = "Recovery child republished"
        child_head.save(update_fields={"name", "updated_at"})
        newer_child_version = child_head.publish()
    assert newer_child_version.pk != child.workflow_id

    def finish_child() -> None:
        with system_context(reason="native call child dispatch fixture"):
            initial = WorkflowDispatch.objects.get(run=child, kind=WorkflowDispatchKind.ADVANCE)
        assert engine.advance_dispatch(initial.pk)["claimed"] == 1
        with system_context(reason="native call child execute fixture"):
            current = StepRun.objects.get(run=child)
            attempt = current.current_attempt
            execute = WorkflowDispatch.objects.get(step_attempt=attempt)
            if not child_finishes_before_recovery:
                assert child.input == {"child": "retained"}
                assert child.parent_step_run_id == source_step_run.pk
                assert source_attempt.input_provenance == {"kind": "run_input"}
        assert engine.execute_dispatch(execute.pk, attempt.pk, attempt.lease_token)["executed"] == 1
        future = timezone.now() + timedelta(hours=2)
        assert engine.advance(child.pk, now=future)["claimed"] == 1
        with system_context(reason="native call child completion fixture"):
            current = StepRun.objects.get(run=child)
            attempt = current.current_attempt
            execute = WorkflowDispatch.objects.get(step_attempt=attempt)
        assert engine.execute_dispatch(execute.pk, attempt.pk, attempt.lease_token, now=future)["executed"] == 1
        assert engine.advance(child.pk, now=future)["claimed"] == 0
        with system_context(reason="native call child completion assertion"):
            child.refresh_from_db()
        assert child.result == {
            "status": "succeeded",
            "outcome": "completed",
            "output": {},
            "error": None,
        }

    if child_finishes_before_recovery:
        finish_child()
    stranger = get_user_model().objects.create_user(username=f"call-recovery-stranger-{child_finishes_before_recovery}")
    with pytest.raises(PermissionDenied, match="Recovery source evidence is unavailable"):
        WorkflowRun.objects.start_recovery(
            source_attempt,
            request_key=f"native-call-denied-{child_finishes_before_recovery}",
            actor=stranger,
        )
    recovery, attempt = _execute_recovery(
        source_attempt,
        actor=actor,
        request_key=f"native-call-{child_finishes_before_recovery}",
    )
    with system_context(reason="native call retained identity assertion"):
        assert WorkflowRun.objects.filter(parent_step_run=source_step_run).count() == 1
        recovered_step = StepRun.objects.get(run=recovery)
        assert recovered_step.current_attempt.external_object_id == child.pk
    if not child_finishes_before_recovery:
        assert recovered_step.status == "waiting"
        finish_child()
        with system_context(reason="native call completion dispatch"):
            delivery = WorkflowDispatch.objects.get(
                kind=WorkflowDispatchKind.ARTIFACT_DELIVERY,
                artifact_object_id=child.pk,
            )
        assert engine.deliver_artifact_dispatch(delivery.pk)["woken"] == 1
        with system_context(reason="native call wake dispatch"):
            advance = (
                WorkflowDispatch.objects.filter(
                    run=recovery,
                    kind=WorkflowDispatchKind.ADVANCE,
                    consumed_at__isnull=True,
                )
                .order_by("pk")
                .first()
            )
            assert advance is not None
        assert engine.advance_dispatch(advance.pk)["claimed"] == 1
        with system_context(reason="native call resumed execution"):
            recovered_step.refresh_from_db()
            resumed = recovered_step.current_attempt
            execute = WorkflowDispatch.objects.get(step_attempt=resumed)
        assert engine.execute_dispatch(execute.pk, resumed.pk, resumed.lease_token)["executed"] == 1
    with system_context(reason="native call recovered result"):
        recovered_step.refresh_from_db()
        assert recovered_step.status == "succeeded", recovered_step.error
        assert recovered_step.outcome == "completed"
        assert recovered_step.output_present is True
        assert recovered_step.output == {}


def _draft(name: str = "Testable draft", *, owner: object | None = None) -> tuple[Workflow, Step]:
    with system_context(reason="workflow test snapshot setup"):
        workflow = Workflow.objects.create(name=name, created_by=owner, updated_by=owner)
        step = Step.objects.create(
            workflow=workflow,
            key="entry",
            name="Entry",
            step_class="agent_session",
            is_entry=True,
        )
        workflow.refresh_from_db()
    return workflow, step


def test_snapshot_uses_exact_revision_reuses_identity_and_does_not_change_currency(
    workflow_engine_tables: None,
) -> None:
    workflow, _ = _draft()
    revision = workflow.draft_revision

    first = Workflow.objects.test_snapshot(workflow, expected_revision=revision)
    second = Workflow.objects.test_snapshot(workflow, expected_revision=revision)

    assert second.pk == first.pk
    assert first.status == WorkflowStatus.TEST
    assert first.version == 0
    assert first.draft_revision == revision
    assert first.published_from_id == workflow.pk
    with system_context(reason="test snapshot currency"):
        assert Workflow.objects.current_published_for(workflow) is None

    with system_context(reason="publish after test snapshot"):
        published = workflow.publish()
    assert published.version == 1
    with system_context(reason="test snapshot publication currency"):
        current = Workflow.objects.current_published_for(workflow)
        assert current is not None
        assert current.pk == published.pk
        assert Workflow.objects.filter(
            published_from=workflow,
            status=WorkflowStatus.TEST,
        ).count() == 1


def test_snapshot_rejects_stale_head_revision_and_preserves_old_snapshot(
    workflow_engine_tables: None,
) -> None:
    workflow, step = _draft()
    old_revision = workflow.draft_revision
    old = Workflow.objects.test_snapshot(workflow, expected_revision=old_revision)
    with system_context(reason="advance workflow draft after test snapshot"):
        step.name = "Changed entry"
        step.save(update_fields={"name"})
        workflow.refresh_from_db()

    with pytest.raises(StaleDefinitionError):
        Workflow.objects.test_snapshot(workflow, expected_revision=old_revision)

    old.refresh_from_db()
    assert old.status == WorkflowStatus.TEST
    with system_context(reason="read preserved test snapshot"):
        assert old.steps.get().name == "Entry"
    assert workflow.draft_revision > old_revision


def test_snapshot_workflow_and_definition_rows_are_immutable(
    workflow_engine_tables: None,
) -> None:
    workflow, _ = _draft()
    snapshot = Workflow.objects.test_snapshot(workflow, expected_revision=workflow.draft_revision)
    with system_context(reason="read test snapshot definition"):
        copied_step = snapshot.steps.get()

    snapshot.name = "Mutated snapshot"
    with pytest.raises(ValidationError, match="immutable"):
        snapshot.save()
    copied_step.name = "Mutated step"
    with pytest.raises(ValidationError, match="immutable"):
        copied_step.save(update_fields={"name"})
    with pytest.raises(ValidationError, match="immutable"):
        snapshot.delete()


def test_old_snapshot_starts_after_head_edit_and_ambiguous_retry_returns_same_run(
    workflow_engine_tables: None,
) -> None:
    actor = get_user_model().objects.create_user(username="snapshot-runner")
    workflow, step = _draft(owner=actor)
    revision = workflow.draft_revision
    snapshot = Workflow.objects.test_snapshot(workflow, expected_revision=revision)
    first = WorkflowRun.objects.start_test(
        snapshot,
        expected_revision=revision,
        request_key="same-request",
        subject=None,
        actor=actor,
        input=JsonPresence(True, {"value": 1}),
    )
    with system_context(reason="edit head after test launch"):
        step.name = "Later edit"
        step.save(update_fields={"name"})

    retried = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=revision,
        request_key="same-request",
        subject=None,
        actor=actor,
        input=JsonPresence(True, {"value": 1}),
    )
    another = WorkflowRun.objects.start_test(
        snapshot,
        expected_revision=revision,
        request_key="old-snapshot-new-request",
        subject=None,
        actor=actor,
    )

    assert retried.pk == first.pk
    assert another.workflow_id == snapshot.pk
    assert first.workflow_id == snapshot.pk
    assert first.origin == RunOrigin.TEST
    assert first.input_present is True
    assert first.input == {"value": 1}
    with system_context(reason="read test run projection"):
        assert first.step_runs.count() == 1
        assert WorkflowDispatch.objects.filter(run=first).count() == 1


def test_node_test_pins_selected_copied_step_and_reuses_exact_scope(
    workflow_engine_tables: None,
) -> None:
    actor = get_user_model().objects.create_user(username="node-test-runner")
    workflow, selected = _draft(owner=actor)
    revision = workflow.draft_revision

    run = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=revision,
        request_key="node-scope",
        subject=None,
        actor=actor,
        scope=WorkflowTestScope.NODE,
        selected_step=selected,
    )

    assert run.test_scope == WorkflowTestScope.NODE
    assert run.test_step.workflow_id == run.workflow_id
    assert run.test_step.key == selected.key
    with system_context(reason="node test initial journal"):
        assert list(run.step_runs.values_list("step_id", flat=True)) == [run.test_step_id]

    retried = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=revision,
        request_key="node-scope",
        subject=None,
        actor=actor,
        scope=WorkflowTestScope.NODE,
        selected_step=selected,
    )
    assert retried.pk == run.pk

    with pytest.raises(ValidationError, match="request"):
        WorkflowRun.objects.start_test(
            workflow,
            expected_revision=revision,
            request_key="node-scope",
            subject=None,
            actor=actor,
            scope=WorkflowTestScope.WHOLE,
        )


def test_legacy_test_scope_blank_is_read_as_whole_but_new_writes_are_explicit(
    workflow_engine_tables: None,
) -> None:
    actor = get_user_model().objects.create_user(username="legacy-test-runner")
    workflow, step = _draft(owner=actor)
    run = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=workflow.draft_revision,
        request_key="explicit-whole",
        subject=None,
        actor=actor,
    )
    assert run.test_scope == WorkflowTestScope.WHOLE
    with system_context(reason="whole test scope"):
        copied_step = run.workflow.steps.get(key=step.key)
    assert run.allows_test_step(copied_step) is True

    models.QuerySet.update(WorkflowRun.objects.filter(pk=run.pk), test_scope="")
    with system_context(reason="load legacy test scope"):
        run = WorkflowRun.objects.get(pk=run.pk)
    assert run.test_scope == ""
    assert run.allows_test_step(copied_step) is True
    run.error = "legacy row remains writable through unrelated owners"
    with system_context(reason="legacy test unrelated update"):
        run.save(update_fields=["error", "updated_at"])
    run.test_scope = WorkflowTestScope.WHOLE
    with system_context(reason="legacy test identity rejection"):
        with pytest.raises(ValidationError, match="identity"):
            run.save(update_fields=["test_scope", "updated_at"])
    with pytest.raises(TypeError, match="identity"):
        WorkflowRun.objects.filter(pk=run.pk).update(test_scope=WorkflowTestScope.WHOLE)


def test_node_selector_is_reloaded_under_lineage_lock_and_scope_rejects_foreign_steps(
    workflow_engine_tables: None,
) -> None:
    actor = get_user_model().objects.create_user(username="node-selector-owner")
    workflow, selected = _draft(owner=actor)
    other, foreign = _draft("Other workflow", owner=actor)
    del other
    forged = copy.copy(selected)
    forged.key = "caller-stale-key"

    run = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=workflow.draft_revision,
        request_key="locked-selector",
        subject=None,
        actor=actor,
        scope=WorkflowTestScope.NODE,
        selected_step=forged,
    )

    assert run.test_step.key == "entry"
    assert run.allows_test_step(run.test_step) is True
    assert run.allows_test_step(foreign) is False
    with pytest.raises(ValidationError, match="exact workflow revision"):
        WorkflowRun.objects.start_test(
            workflow,
            expected_revision=workflow.draft_revision,
            request_key="foreign-selector",
            subject=None,
            actor=actor,
            scope=WorkflowTestScope.NODE,
            selected_step=foreign,
        )


def test_test_request_rejects_changed_facts_and_cross_actor_replay(
    workflow_engine_tables: None,
) -> None:
    owner = get_user_model().objects.create_user(username="snapshot-owner")
    stranger = get_user_model().objects.create_user(username="snapshot-stranger")
    workflow, _ = _draft(owner=owner)
    revision = workflow.draft_revision
    WorkflowRun.objects.start_test(
        workflow,
        expected_revision=revision,
        request_key="private-request",
        subject=None,
        actor=owner,
        input=JsonPresence(False, None),
    )

    with pytest.raises(ValidationError, match="request"):
        WorkflowRun.objects.start_test(
            workflow,
            expected_revision=revision,
            request_key="private-request",
            subject=None,
            actor=owner,
            input=JsonPresence(True, None),
        )
    typed = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=revision,
        request_key="typed-json-request",
        subject=None,
        actor=owner,
        input=JsonPresence(True, {"enabled": True}),
    )
    with pytest.raises(ValidationError, match="request"):
        WorkflowRun.objects.start_test(
            workflow,
            expected_revision=revision,
            request_key="typed-json-request",
            subject=None,
            actor=owner,
            input=JsonPresence(True, {"enabled": 1}),
        )
    assert typed.input == {"enabled": True}
    with pytest.raises(PermissionDenied):
        WorkflowRun.objects.start_test(
            workflow,
            expected_revision=revision,
            request_key="private-request",
            subject=None,
            actor=stranger,
            input=JsonPresence(False, None),
        )


def test_test_launch_rejects_unauthorized_first_request_and_published_retry(
    workflow_engine_tables: None,
) -> None:
    owner = get_user_model().objects.create_user(username="snapshot-access-owner")
    outsider = get_user_model().objects.create_user(username="snapshot-access-outsider")
    workflow, _ = _draft(owner=owner)
    revision = workflow.draft_revision

    with pytest.raises(PermissionDenied):
        WorkflowRun.objects.start_test(
            workflow,
            expected_revision=revision,
            request_key="unauthorized-first",
            subject=None,
            actor=outsider,
        )
    run = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=revision,
        request_key="kind-checked-retry",
        subject=None,
        actor=owner,
    )
    with system_context(reason="publish after retained test request"):
        publication = workflow.publish()
    with pytest.raises(ValidationError, match="draft head or test snapshot"):
        WorkflowRun.objects.start_test(
            publication,
            expected_revision=revision,
            request_key="kind-checked-retry",
            subject=None,
            actor=owner,
        )

    assert run.workflow.status == WorkflowStatus.TEST
    with system_context(reason="verify rejected test requests"):
        assert WorkflowRun.objects.count() == 1


def test_test_request_identity_is_immutable_and_survives_actor_deletion(
    workflow_engine_tables: None,
) -> None:
    actor = get_user_model().objects.create_user(username="snapshot-identity")
    workflow, _ = _draft(owner=actor)
    run = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=workflow.draft_revision,
        request_key="immutable-identity",
        subject=None,
        actor=actor,
    )
    actor_ref = run.test_request_actor_ref

    run.origin = RunOrigin.MANUAL
    with pytest.raises(ValidationError, match="identity is immutable"):
        run.save(update_fields={"origin"})
    with pytest.raises(TypeError, match="identity and terminal facts are immutable"):
        WorkflowRun.objects.filter(pk=run.pk).update(subject_object_id=1)
    run.refresh_from_db()
    run.workflow = workflow
    with pytest.raises(TypeError, match="invocation identity and terminal facts are immutable"):
        WorkflowRun.objects.bulk_update([run], ["workflow"])
    with system_context(reason="load deferred test run"):
        deferred = WorkflowRun.objects.only("pk").get(pk=run.pk)
    deferred.workflow = workflow
    with pytest.raises(ValidationError, match="identity is immutable"):
        deferred.save()

    with system_context(reason="delete test requester"):
        actor.delete()
    run.refresh_from_db()
    assert run.created_by_id is None
    assert run.test_request_actor_ref == actor_ref


def test_test_request_retry_rechecks_revoked_workflow_access(
    workflow_engine_tables: None,
) -> None:
    actor = get_user_model().objects.create_user(username="snapshot-revoked")
    workflow, _ = _draft(owner=actor)
    run = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=workflow.draft_revision,
        request_key="revoked-retry",
        subject=None,
        actor=actor,
    )
    with system_context(reason="revoke test workflow owner"):
        workflow.created_by = None
        workflow.save(update_fields={"created_by", "updated_at"})

    with pytest.raises(PermissionDenied):
        WorkflowRun.objects.start_test(
            workflow,
            expected_revision=workflow.draft_revision,
            request_key="revoked-retry",
            subject=None,
            actor=actor,
        )
    with system_context(reason="verify revoked retry kept original run"):
        assert WorkflowRun.objects.get(pk=run.pk).test_request_actor_ref == str(to_subject_ref(actor))


def test_test_launch_preserves_absent_null_and_value_input_envelopes(
    workflow_engine_tables: None,
) -> None:
    actor = get_user_model().objects.create_user(username="snapshot-inputs")
    workflow, _ = _draft(owner=actor)
    revision = workflow.draft_revision

    absent = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=revision,
        request_key="input-absent",
        subject=None,
        actor=actor,
        input=JsonPresence(False, None),
    )
    null = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=revision,
        request_key="input-null",
        subject=None,
        actor=actor,
        input=JsonPresence(True, None),
    )
    value = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=revision,
        request_key="input-value",
        subject=None,
        actor=actor,
        input=JsonPresence(True, {"value": []}),
    )

    assert (absent.input_present, absent.input) == (False, None)
    assert (null.input_present, null.input) == (True, None)
    assert (value.input_present, value.input) == (True, {"value": []})


def test_failed_test_launch_rolls_back_snapshot_run_entry_and_dispatch(
    workflow_engine_tables: None,
) -> None:
    actor = get_user_model().objects.create_user(username="snapshot-invalid-subject")
    workflow, _ = _draft(owner=actor)
    workflow.subject_declaration = get_user_model()._meta.label_lower
    with system_context(reason="declare test subject"):
        workflow.save(update_fields={"subject_declaration"})
        workflow.refresh_from_db()

    with pytest.raises(ValidationError, match="subject"):
        WorkflowRun.objects.start_test(
            workflow,
            expected_revision=workflow.draft_revision,
            request_key="invalid-launch",
            subject=None,
            actor=actor,
        )

    with system_context(reason="verify failed test launch rollback"):
        assert not Workflow.objects.filter(
            published_from=workflow,
            status=WorkflowStatus.TEST,
        ).exists()
        assert not WorkflowRun.objects.exists()
        assert not WorkflowDispatch.objects.exists()


def test_test_mutation_preserves_omitted_and_explicit_null_input(
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    from tests.conftest import execute_schema, result_data
    from tests.test_workflows import _console_schema

    actor = get_user_model().objects.create_user(username="snapshot-graphql")
    workflow, _ = _draft(owner=actor)
    mutation = """
      mutation TestWorkflow(
        $workflow: ID!
        $revision: Int!
        $requestKey: String!
        $input: JSON
      ) {
        start_workflow_test(
          workflow: $workflow
          expected_revision: $revision
          request_key: $requestKey
          input: $input
        ) { ok id validation_errors }
      }
    """
    variables = {
        "workflow": workflow.sqid,
        "revision": workflow.draft_revision,
        "requestKey": "graphql-absent",
    }
    absent = result_data(execute_schema(_console_schema(), mutation, variables, user=actor))[
        "start_workflow_test"
    ]
    variables.update(requestKey="graphql-null", input=None)
    null = result_data(execute_schema(_console_schema(), mutation, variables, user=actor))["start_workflow_test"]

    assert absent["ok"] is True
    assert null["ok"] is True
    with system_context(reason="read GraphQL test launches"):
        absent_run = WorkflowRun.objects.get(sqid=absent["id"])
        null_run = WorkflowRun.objects.get(sqid=null["id"])
    assert (absent_run.input_present, absent_run.input) == (False, None)
    assert (null_run.input_present, null_run.input) == (True, None)


def test_test_plan_and_launch_share_node_scope_source_and_fixture_transport(
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    from tests.conftest import execute_schema, result_data
    from tests.test_workflows import _console_schema

    actor = get_user_model().objects.create_user(username="snapshot-node-graphql")
    workflow, selected = _draft(owner=actor)
    plan_query = """
      query Plan($workflow: ID!, $revision: Int!, $source: ID!) {
        workflow_test_plan(
          workflow: $workflow expected_revision: $revision scope: NODE selected_step: $source
        ) {
          revision scope source_step_id snapshot_step_id is_current
          operations { step_id key effect replaced_by_output outcomes { key label description } }
          required_fixtures { role step_id step_key item_index_required satisfied }
          diagnostics { code field }
        }
      }
    """
    variables = {"workflow": workflow.sqid, "revision": workflow.draft_revision, "source": selected.sqid}
    plan = result_data(execute_schema(_console_schema(), plan_query, variables, user=actor))["workflow_test_plan"]
    assert plan["scope"] == "NODE"
    assert plan["source_step_id"] == selected.sqid
    assert plan["snapshot_step_id"] is None
    assert plan["operations"][0]["outcomes"] == []

    mutation = """
      mutation Launch($workflow: ID!, $revision: Int!, $source: ID!) {
        start_workflow_test(
          workflow: $workflow expected_revision: $revision request_key: "node-graphql"
          scope: NODE source_step: $source fixtures: []
        ) { ok id validation_errors }
      }
    """
    launch = result_data(execute_schema(_console_schema(), mutation, variables, user=actor))["start_workflow_test"]
    assert launch["ok"] is True
    with system_context(reason="verify GraphQL node launch"):
        run = WorkflowRun.objects.get(sqid=launch["id"])
    assert run.test_scope == WorkflowTestScope.NODE
    assert run.test_source_step_id == selected.pk


@pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL test-launch serialization contract")
def test_concurrent_test_request_reuses_one_snapshot_run_and_dispatch(
    workflow_engine_tables: None,
) -> None:
    actor = get_user_model().objects.create_user(username="snapshot-concurrent")
    workflow, _ = _draft(owner=actor)
    revision = workflow.draft_revision
    starting = Barrier(2)

    def launch() -> int:
        close_old_connections()
        try:
            starting.wait(timeout=5)
            return WorkflowRun.objects.start_test(
                workflow,
                expected_revision=revision,
                request_key="same-concurrent-request",
                subject=None,
                actor=actor,
            ).pk
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (pool.submit(launch), pool.submit(launch))
        run_ids = [future.result(timeout=10) for future in futures]

    assert run_ids[0] == run_ids[1]
    with system_context(reason="verify serialized test launch"):
        assert Workflow.objects.filter(
            published_from=workflow,
            status=WorkflowStatus.TEST,
        ).count() == 1
        assert WorkflowRun.objects.filter(origin=RunOrigin.TEST).count() == 1
        assert WorkflowDispatch.objects.count() == 1
