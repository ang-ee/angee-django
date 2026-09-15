"""Exact-version workflow launch and definition-digest contracts."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from rebac import system_context

from angee.workflows import engine
from angee.workflows.attempts import JsonPresence
from angee.workflows.models import RunOrigin, WorkflowPurpose
from tests.workflows import Edge, Step, StepRun, Workflow, WorkflowDispatch, WorkflowRun


def _published_versions(*, owner: Any = None) -> tuple[Workflow, Workflow, Workflow]:
    """Create one lineage with two distinct immutable versions."""

    with system_context(reason="test exact workflow versions"):
        head = Workflow.objects.create(
            key="exact-pinned",
            name="Exact pinned",
            description="Pinned definition",
            purpose=WorkflowPurpose.INTEGRATION_SYNC,
            max_steps=42,
            budget={"tokens": 1000},
            created_by=owner,
            updated_by=owner,
        )
        entry = Step.objects.create(
            workflow=head,
            key="start",
            name="Start",
            step_class="wait",
            config={"until": "2099-01-01T00:00:00+00:00"},
            is_entry=True,
            position={"x": 1, "y": 2},
        )
        finish = Step.objects.create(
            workflow=head,
            key="finish",
            name="Finish",
            step_class="wait",
            config={"until": "2099-01-02T00:00:00+00:00"},
        )
        Edge.objects.create(workflow=head, source=entry, target=finish, condition="timer")
        first = head.publish()
        entry.name = "Start updated"
        entry.save()
        second = head.publish()
    return head, first, second


@pytest.mark.django_db(transaction=True)
def test_definition_digest_is_stable_and_exact_version_sensitive(
    workflow_engine_tables: None,
) -> None:
    """The owner hashes canonical persisted definition content, not row identity."""

    del workflow_engine_tables
    _head, first, second = _published_versions()
    digest = first.definition_digest()
    with system_context(reason="inspect exact workflow digest signature"):
        signature = first._definition_signature()
    assert set(signature["workflow"]) == {
        "name",
        "description",
        "purpose",
        "subject_declaration",
        "error_workflow_id",
        "max_steps",
        "budget",
    }
    assert set(signature["steps"][0]) == {
        "key",
        "name",
        "step_class",
        "config",
        "input_binding",
        "join_rule",
        "is_entry",
        "position",
    }
    assert signature["edges"] == [{"source": "start", "target": "finish", "condition": "timer"}]
    expected = hashlib.sha256(
        json.dumps(signature, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    assert digest == expected
    first.name = "Unpersisted stale name"
    assert first.definition_digest() == digest
    first.refresh_from_db()

    assert len(digest) == 64
    assert first.definition_digest() == digest
    assert second.definition_digest() != digest


@pytest.mark.django_db(transaction=True)
def test_pinned_start_requires_exact_published_version_and_digest_without_writes(
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    """Untrusted or mutable definitions fail before any runtime row is created."""

    del workflow_engine_tables, no_workflow_queue
    head, first, second = _published_versions()
    with system_context(reason="test archived exact workflow start"):
        first.archive()
    requests = (
        (head, head.definition_digest(), "pin:head", "head"),
        (first, first.definition_digest(), "pin:archived", "archived"),
        (second, "0" * 64, "pin:digest", "digest"),
    )

    for version, digest, dedup_key, occurrence_id in requests:
        with pytest.raises(ValidationError):
            engine.start_pinned(
                version,
                subject=None,
                actor=None,
                expected_definition_digest=digest,
                dedup_key=dedup_key,
                occurrence_id=occurrence_id,
            )

    with system_context(reason="test rejected exact workflow starts"):
        assert WorkflowRun.objects.count() == 0
        assert StepRun.objects.count() == 0
        assert WorkflowDispatch.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_pinned_start_retain_is_exact_and_always_runs_launch_hook(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exact retry is retained only after the cooperative launch guard runs."""

    del workflow_engine_tables, no_workflow_queue
    _head, first, second = _published_versions()
    calls: list[tuple[int, int | None, bool, str | None]] = []

    def validate_run_launch(self: Workflow, **facts: Any) -> None:
        retained = facts["retained_run"]
        calls.append(
            (self.pk, None if retained is None else retained.pk, facts["exact_version"], facts["occurrence_id"])
        )

    monkeypatch.setattr(Workflow, "validate_run_launch", validate_run_launch)
    kwargs = {
        "subject": None,
        "actor": None,
        "expected_definition_digest": first.definition_digest(),
        "dedup_key": "pin:retained",
        "occurrence_id": "window:1",
        "input": JsonPresence(True, {"cutoff": 7}),
    }
    run = engine.start_pinned(first, **kwargs)
    retained = engine.start_pinned(first, **kwargs)

    assert retained.pk == run.pk
    assert calls == [
        (first.pk, None, True, "window:1"),
        (first.pk, run.pk, True, "window:1"),
    ]
    with pytest.raises(ValidationError, match="different immutable facts"):
        engine.start_pinned(first, **{**kwargs, "occurrence_id": "window:2"})
    with pytest.raises(ValidationError, match="different immutable facts"):
        engine.start_pinned(
            second,
            **{**kwargs, "expected_definition_digest": second.definition_digest()},
        )


@pytest.mark.django_db(transaction=True)
def test_launch_hook_denial_rolls_back_new_and_rejects_retained(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The always-run hook gates both creation and duplicate delivery."""

    del workflow_engine_tables, no_workflow_queue
    _head, first, _second = _published_versions()
    kwargs = {
        "subject": None,
        "actor": None,
        "expected_definition_digest": first.definition_digest(),
        "dedup_key": "pin:guarded",
        "occurrence_id": "request:1",
    }
    original = Workflow.validate_run_launch

    def deny(self: Workflow, **facts: Any) -> None:
        del self, facts
        raise ValidationError("launch denied")

    monkeypatch.setattr(Workflow, "validate_run_launch", deny)
    with pytest.raises(ValidationError, match="launch denied"):
        engine.start_pinned(first, **kwargs)
    with system_context(reason="test denied pinned launch"):
        assert not WorkflowRun.objects.filter(dedup_key="pin:guarded").exists()

    monkeypatch.setattr(Workflow, "validate_run_launch", original)
    run = engine.start_pinned(first, **kwargs)
    monkeypatch.setattr(Workflow, "validate_run_launch", deny)
    with pytest.raises(ValidationError, match="launch denied"):
        engine.start_pinned(first, **kwargs)
    with system_context(reason="test denied retained pinned launch"):
        assert WorkflowRun.objects.get(dedup_key="pin:guarded").pk == run.pk
        assert WorkflowRun.objects.filter(dedup_key="pin:guarded").count() == 1


@pytest.mark.django_db(transaction=True)
def test_current_version_starts_also_run_launch_hook_before_create_and_retain(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All manager start routes share the cooperative hook, not only pinned calls."""

    del workflow_engine_tables, no_workflow_queue
    head, _first, second = _published_versions()
    calls: list[int | None] = []

    def validate_run_launch(self: Workflow, **facts: Any) -> None:
        assert self.pk == second.pk
        retained = facts["retained_run"]
        calls.append(None if retained is None else retained.pk)

    monkeypatch.setattr(Workflow, "validate_run_launch", validate_run_launch)
    run = engine.start(head, subject=None, actor=None, dedup_key="current:guarded")
    retained = engine.start(head, subject=None, actor=None, dedup_key="current:guarded")

    assert retained.pk == run.pk
    assert calls == [None, run.pk]


@pytest.mark.django_db(transaction=True)
def test_reprocess_retry_runs_launch_hook_before_outer_retained_return(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reprocessing keeps authorization ahead of its route-specific retry return."""

    del workflow_engine_tables, no_workflow_queue
    actor = get_user_model().objects.create_user(username="pinned-reprocess-owner")
    head, _first, _second = _published_versions(owner=actor)
    source = engine.start(head, subject=None, actor=actor, dedup_key="source:reprocess")
    with system_context(reason="make reprocess source terminal"):
        source.mark_canceled()
    calls: list[int | None] = []

    def validate_run_launch(self: Workflow, **facts: Any) -> None:
        del self
        retained = facts["retained_run"]
        calls.append(None if retained is None else retained.pk)

    monkeypatch.setattr(Workflow, "validate_run_launch", validate_run_launch)
    run = WorkflowRun.objects.reprocess(source, actor=actor, request_key="retry-1")
    retained = WorkflowRun.objects.reprocess(source, actor=actor, request_key="retry-1")

    assert retained.pk == run.pk
    assert calls == [None, run.pk]
    with system_context(reason="count reprocess launch rows"):
        before = (WorkflowRun.objects.count(), WorkflowDispatch.objects.count())

    def deny(self: Workflow, **facts: Any) -> None:
        del self, facts
        raise ValidationError("launch denied")

    monkeypatch.setattr(Workflow, "validate_run_launch", deny)
    with pytest.raises(ValidationError, match="launch denied"):
        WorkflowRun.objects.reprocess(source, actor=actor, request_key="retry-1")
    with system_context(reason="verify denied reprocess retry"):
        assert (WorkflowRun.objects.count(), WorkflowDispatch.objects.count()) == before


@pytest.mark.django_db(transaction=True)
def test_linked_and_error_origins_share_launch_hook(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Linked workflow and error starts cannot bypass version launch policy."""

    del workflow_engine_tables, no_workflow_queue
    head, _first, second = _published_versions()
    source = engine.start(head, subject=None, actor=None, dedup_key="source:linked")
    with system_context(reason="read linked workflow parent step"):
        parent_step = StepRun.objects.get(run=source)
    origins: list[str] = []

    def validate_run_launch(self: Workflow, **facts: Any) -> None:
        del self
        origins.append(str(facts["origin"]))

    monkeypatch.setattr(Workflow, "validate_run_launch", validate_run_launch)
    engine.start(
        second,
        subject=source,
        actor=None,
        dedup_key="linked:workflow",
        origin=RunOrigin.WORKFLOW,
    )
    engine.start(
        second,
        subject=source,
        actor=None,
        parent_step_run=parent_step,
        origin=RunOrigin.ERROR_WORKFLOW,
    )

    assert origins == [RunOrigin.WORKFLOW, RunOrigin.ERROR_WORKFLOW]
