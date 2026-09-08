"""Immutable draft snapshot and idempotent test-launch contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection, connections
from rebac import system_context, to_subject_ref

from angee.workflows.attempts import JsonPresence
from angee.workflows.definitions import StaleDefinitionError
from angee.workflows.models import RunOrigin, WorkflowStatus
from tests.workflows import Step, Workflow, WorkflowDispatch, WorkflowRun

pytest_plugins = ("tests.workflows",)
pytestmark = pytest.mark.django_db(transaction=True)


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
