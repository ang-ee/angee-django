"""Tests for the parties dedupe workflow (scan → gate → prepare → map → unit)."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from rebac import system_context

from angee.workflows import engine
from angee.workflows import models as workflow_models
from angee.workflows_parties.autoconfig import SETTINGS as WORKFLOWS_PARTIES_SETTINGS
from angee.workflows_parties.steps import DedupeExecuteStepImpl
from tests.test_messaging import (
    MESSAGING_TEST_MODELS,
    Handle,
    MergeVeto,
    Party,
)
from tests.workflows import (
    WORKFLOW_RUNTIME_MODELS,
    Decision,
    advance_once,
    execute_started,
    run_to_terminal,
    step_run_for,
    workflow_table_setup,
    workflow_with_steps,
)

User = get_user_model()
pytest_plugins = ("tests.workflows",)


@pytest.fixture
def workflows_parties_tables(transactional_db: Any) -> Iterator[None]:
    """Create workflow and parties tables for dedupe-flow tests."""

    del transactional_db
    models = MESSAGING_TEST_MODELS + WORKFLOW_RUNTIME_MODELS
    with workflow_table_setup(models):
        yield


def _dedupe_workflow() -> Any:
    """Return the scan -> gate -> prepare -> stock map -> unit workflow."""

    return workflow_with_steps(
        name="Deduplicate contacts",
        steps=(
            {"key": "scan", "step_class": "parties_dedupe_scan", "config": {"limit": 50}},
            {"key": "gate", "step_class": "parties_dedupe_gate", "config": {}},
            {
                "key": "prepare",
                "step_class": "parties_dedupe_execute",
                "config": {"mode": "prepare"},
            },
            {
                "key": "map",
                "step_class": "map",
                "config": {"target_step": "apply_unit", "items": "input"},
            },
            {
                "key": "apply_unit",
                "step_class": "parties_dedupe_execute",
                "config": {"mode": "unit"},
            },
        ),
        edges=(
            ("scan", "gate", "found"),
            ("gate", "prepare", "completed"),
            ("prepare", "map", "prepared"),
        ),
    )


def _duplicate_pair(owner: Any, *, named: str, digits: str, spaced: str) -> tuple[Any, Any]:
    """Create two parties whose phone handles share one E.164 on one platform.

    Same-platform, same-normalized-value is the ``duplicate_candidates``
    contract; ``Handle.save`` derives ``normalized_value`` itself. The named
    party also carries the richer handle count, so the survivor heuristic
    proposes it deterministically.
    """

    named_party = Party._base_manager.create(display_name=named, handle_count=2, created_by=owner)
    numeric_party = Party._base_manager.create(display_name=digits, handle_count=1, created_by=owner)
    Handle._base_manager.create(
        platform=Handle.Platform.PHONE,
        value=digits,
        party=named_party,
        created_by=owner,
    )
    Handle._base_manager.create(
        platform=Handle.Platform.PHONE,
        value=spaced,
        party=numeric_party,
        created_by=owner,
    )
    return named_party, numeric_party


@pytest.mark.django_db(transaction=True)
def test_dedupe_scan_gate_map_apply_end_to_end(
    workflows_parties_tables: None,
    no_workflow_queue: None,
) -> None:
    """Scan proposes pairs, the decision batch edits them, and verbs apply."""

    del workflows_parties_tables, no_workflow_queue
    operator = User.objects.create_user(username="dedupe-operator")
    with system_context(reason="test dedupe fixture"):
        keep_a, drop_a = _duplicate_pair(
            operator, named="Sofia Khomutova", digits="+79213846620", spaced="+7 921 384 6620"
        )
        keep_b, sep_b = _duplicate_pair(
            operator, named="Ed MacLaughlin", digits="+14155550101", spaced="+1 415 555 0101"
        )
    workflow = _dedupe_workflow()

    run = engine.start(workflow, None, operator)
    advance_once(run)
    execute_started(run)
    advance_once(run)
    execute_started(run)

    scan = step_run_for(run, "scan")
    assert scan.outcome == "found"
    pairs = scan.output["pairs"]
    assert len(pairs) == 2
    for pair in pairs:
        # The named, handle-rich party wins the survivor proposal.
        assert pair["action"] == "merge"
        winner = pair["left_name"] if pair["survivor"] == "left" else pair["right_name"]
        assert winner in ("Sofia Khomutova", "Ed MacLaughlin")

    with system_context(reason="test dedupe decision"):
        decision = Decision.objects.select_related("step_run").get(step_run__run=run)
    assert decision.form_schema["properties"]["pairs"]["widget"] == "rows"
    assert decision.payload == {"pairs": pairs}

    # The human keeps the first merge, flips the second pair to keep-separate.
    resolved = [dict(pair) for pair in pairs]
    second = next(row for row in resolved if "MacLaughlin" in (row["left_name"] + row["right_name"]))
    second["action"] = "keep_separate"
    attempted = engine.decide(decision, "complete", payload={"pairs": resolved}, actor=operator)
    assert attempted.validation_error is None

    run_to_terminal(run)
    run.refresh_from_db()
    assert run.status == workflow_models.RunStatus.SUCCEEDED

    prepare = step_run_for(run, "prepare")
    assert sorted(row["action"] for row in prepare.output) == ["keep_separate", "merge"]

    with system_context(reason="test dedupe assertions"):
        drop_a.refresh_from_db()
        assert drop_a.merged_into_id == keep_a.pk
        assert MergeVeto._base_manager.count() == 1
        veto = MergeVeto._base_manager.get()
        assert {veto.party_a_id, veto.party_b_id} == {keep_b.pk, sep_b.pk}
        # A second scan proposes nothing: one pair merged, the other vetoed.
        assert Party.objects.duplicate_candidates(limit=50) == []


@pytest.mark.django_db(transaction=True)
def test_dedupe_scan_without_candidates_routes_empty(
    workflows_parties_tables: None,
    no_workflow_queue: None,
) -> None:
    """An empty directory ends the run after the scan with no decision."""

    del workflows_parties_tables, no_workflow_queue
    operator = User.objects.create_user(username="dedupe-empty")
    workflow = _dedupe_workflow()

    run = engine.start(workflow, None, operator)
    run_to_terminal(run)
    run.refresh_from_db()
    assert run.status == workflow_models.RunStatus.SUCCEEDED
    assert step_run_for(run, "scan").outcome == "empty"
    with system_context(reason="test dedupe empty"):
        assert Decision.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_prepare_rejects_a_tampered_pair_identity(
    workflows_parties_tables: None,
    no_workflow_queue: None,
) -> None:
    """A resolution that rewrites a read-only identity cell never applies."""

    del workflows_parties_tables, no_workflow_queue
    operator = User.objects.create_user(username="dedupe-tamper")
    with system_context(reason="test dedupe fixture"):
        _duplicate_pair(operator, named="Kent Rothwell", digits="+4915112345678", spaced="+49 151 1234 5678")
    workflow = _dedupe_workflow()

    run = engine.start(workflow, None, operator)
    advance_once(run)
    execute_started(run)
    advance_once(run)
    execute_started(run)
    with system_context(reason="test dedupe decision"):
        decision = Decision.objects.get(step_run__run=run)
    tampered = [dict(pair) for pair in decision.payload["pairs"]]
    tampered[0]["left"] = "pty_forged00"
    attempted = engine.decide(decision, "complete", payload={"pairs": tampered}, actor=operator)
    assert attempted.validation_error is None

    run_to_terminal(run)
    run.refresh_from_db()
    assert run.status == workflow_models.RunStatus.FAILED
    prepare = step_run_for(run, "prepare")
    assert "changed a proposed pair" in str(prepare.error)


@pytest.mark.django_db(transaction=True)
def test_apply_unit_is_idempotent_on_retry(workflows_parties_tables: None) -> None:
    """A retried merge unit reports already_merged instead of failing."""

    del workflows_parties_tables
    operator = User.objects.create_user(username="dedupe-retry")
    with system_context(reason="test dedupe fixture"):
        keep, drop = _duplicate_pair(operator, named="Brian Bourgerie", digits="+16175550100", spaced="+1 617 555 0100")
    unit = DedupeExecuteStepImpl()
    step_run = SimpleNamespace(
        step=SimpleNamespace(config={"mode": "unit"}),
        input={"left": str(keep.sqid), "right": str(drop.sqid), "survivor": "left", "action": "merge"},
        run=SimpleNamespace(created_by_id=operator.pk),
    )

    first = unit.run(step_run, now=None)  # type: ignore[arg-type]
    assert first.output == {"action": "merge", "result": "merged"}
    second = unit.run(step_run, now=None)  # type: ignore[arg-type]
    assert second.output == {"action": "merge", "result": "already_merged"}


def test_autoconfig_registers_the_three_step_keys() -> None:
    """The autoconfig contributes exactly the dedupe step registry keys."""

    assert WORKFLOWS_PARTIES_SETTINGS == {
        "ANGEE_WORKFLOW_STEP_CLASSES.parties_dedupe_scan": ("angee.workflows_parties.steps.DedupeScanStepImpl"),
        "ANGEE_WORKFLOW_STEP_CLASSES.parties_dedupe_gate": ("angee.workflows_parties.steps.DedupeGateStepImpl"),
        "ANGEE_WORKFLOW_STEP_CLASSES.parties_dedupe_execute": ("angee.workflows_parties.steps.DedupeExecuteStepImpl"),
    }
