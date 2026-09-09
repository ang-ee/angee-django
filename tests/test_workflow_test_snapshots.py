"""Immutable draft snapshot and idempotent test-launch contracts."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection, connections, models, transaction
from django.db.models.signals import post_save
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rebac import system_context, to_subject_ref

from angee.workflows import engine
from angee.workflows.attempts import AttemptCause, AttemptResultKind, JsonPresence
from angee.workflows.definitions import StaleDefinitionError
from angee.workflows.models import RunOrigin, WorkflowStatus
from angee.workflows.test_contracts import TestFixtureRole, TestFixtureSpec
from angee.workflows.test_contracts import TestScope as WorkflowTestScope
from tests.workflows import (
    Edge,
    Step,
    StepAttempt,
    Workflow,
    WorkflowDispatch,
    WorkflowRun,
    WorkflowTestFixture,
    advance_once,
)

pytest_plugins = ("tests.workflows",)
pytestmark = pytest.mark.django_db(transaction=True)


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
            TestFixtureSpec(
                step_key=selected.key,
                role=TestFixtureRole.OUTPUT,
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
            TestFixtureSpec(
                entry.key,
                TestFixtureRole.OUTPUT,
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
            step_class="handler",
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
            TestFixtureSpec(
                entry.key,
                TestFixtureRole.OUTPUT,
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
            role=TestFixtureRole.OUTPUT,
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
        role=TestFixtureRole.OUTPUT,
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
        role=TestFixtureRole.OUTPUT,
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
            TestFixtureSpec(
                entry.key,
                TestFixtureRole.OUTPUT,
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
    fixture = TestFixtureSpec(
        entry.key,
        TestFixtureRole.OUTPUT,
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
            TestFixtureSpec(
                entry.key,
                TestFixtureRole.OUTPUT,
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
            TestFixtureSpec(
                fixture_step.key,
                TestFixtureRole.OUTPUT,
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
            workflow=workflow, key="body", name="Body", step_class="handler"
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
            TestFixtureSpec(
                body.key,
                TestFixtureRole.OUTPUT,
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
        Step.objects.create(workflow=workflow, key="body", name="Body", step_class="handler")
        workflow.refresh_from_db()
    run = WorkflowRun.objects.start_test(
        workflow,
        expected_revision=workflow.draft_revision,
        request_key="fixture-map-controller",
        subject=None,
        actor=actor,
        fixtures=(
            TestFixtureSpec(
                controller.key,
                TestFixtureRole.OUTPUT,
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
    fixture = TestFixtureSpec(
        fixture_step.key,
        TestFixtureRole.OUTPUT,
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
            fixtures=(TestFixtureSpec(fixture_step.key, TestFixtureRole.OUTPUT, JsonPresence(True, True)),),
        )


def test_fixture_creation_requires_run_admission_owner(
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
        role=TestFixtureRole.OUTPUT,
        value_present=True,
        value={"forged": True},
    )
    with pytest.raises(TypeError, match="WorkflowRunManager"):
        row.save()
    with pytest.raises(RuntimeError, match="owning run transaction"):
        WorkflowTestFixture.objects._create_batch(run, (row,))


def test_fixture_batch_authority_rejects_signal_reentry(
    workflow_engine_tables: None,
) -> None:
    actor = get_user_model().objects.create_user(username="fixture-signal-guard")
    workflow, entry = _draft(owner=actor)

    def reenter(sender: object, instance: WorkflowTestFixture, created: bool, **kwargs: object) -> None:
        del sender, created, kwargs
        forged = WorkflowTestFixture(
            step=instance.step,
            role=TestFixtureRole.OUTPUT,
            value_present=True,
            value={"forged": True},
        )
        WorkflowTestFixture.objects._create_batch(instance.run, (forged,))

    post_save.connect(reenter, sender=WorkflowTestFixture, weak=False)
    try:
        with pytest.raises(RuntimeError, match="exact prepared batch"):
            WorkflowRun.objects.start_test(
                workflow,
                expected_revision=workflow.draft_revision,
                request_key="fixture-signal-guard",
                subject=None,
                actor=actor,
                fixtures=(
                    TestFixtureSpec(
                        entry.key,
                        TestFixtureRole.OUTPUT,
                        JsonPresence(True, {"safe": True}),
                    ),
                ),
            )
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
            step_class="handler",
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
    with pytest.raises(ValidationError, match="cannot be executed"):
        WorkflowRun.objects.start_test(
            workflow,
            expected_revision=workflow.draft_revision,
            request_key="whole-not-ready",
            subject=None,
            actor=actor,
        )


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
            step_class="handler",
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
            TestFixtureSpec(
                "body", TestFixtureRole.MAP_ITEM, JsonPresence(True, item), item_index=0
            ),
        ),
    )
    with system_context(reason="claim node body fixture"):
        with transaction.atomic():
            locked = WorkflowRun.objects.select_for_update().get(pk=run.pk)
            claimed = engine._claim_due_steps(locked, timestamp=timezone.now(), retained=True)
        assert len(claimed) == 1
        attempt = StepAttempt.objects.get(step_run__run=run, cause=AttemptCause.INITIAL)
        assert attempt.test_fixture.role == TestFixtureRole.MAP_ITEM
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
            workflow=workflow, key="body", name="Body", step_class="handler"
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
    with pytest.raises(TypeError, match="identity is immutable"):
        WorkflowRun.objects.filter(pk=run.pk).update(subject_object_id=1)
    run.refresh_from_db()
    run.workflow = workflow
    with pytest.raises(TypeError, match="identity is immutable"):
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
