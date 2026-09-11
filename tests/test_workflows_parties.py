"""Tests for the parties dedupe workflow (scan → gate → prepare → map → unit)."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from rebac import system_context, to_subject_ref

from angee.workflows import engine
from angee.workflows import models as workflow_models
from angee.workflows.attempts import JsonPresence, RecoveryMode
from angee.workflows_parties.autoconfig import SETTINGS as WORKFLOWS_PARTIES_SETTINGS
from angee.workflows_parties.steps import DedupeExecuteStepImpl, IdentityApplyStepImpl, IdentityReviewStepImpl
from tests.test_messaging import (
    MESSAGING_TEST_MODELS,
    Address,
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
        name="Deduplicate people",
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


def _identity_workflow() -> Any:
    return workflow_with_steps(
        name="Review identity",
        steps=(
            {
                "key": "review", "step_class": "parties_identity_review", "config": {},
                "input_binding": {"kind": "workflow_input", "path": []},
            },
            {"key": "apply", "step_class": "parties_identity_apply", "config": {}},
        ),
        edges=(("review", "apply", "completed"), ("review", "apply", "unchanged")),
    )


@pytest.mark.django_db(transaction=True)
def test_identity_review_freezes_context_and_applies_name_and_address(
    workflows_parties_tables: None,
    no_workflow_queue: None,
) -> None:
    del workflows_parties_tables, no_workflow_queue
    operator = User.objects.create_user(username="identity-reviewer")
    with system_context(reason="test identity fixture"):
        party = Party._base_manager.create(display_name="Old Supplier", created_by=operator)
    proposal = {
        "party_id": str(party.sqid),
        "proposed": {
            "name": "Example Supplier",
            "address": {"street": "10 Example Road", "city": "Exampleton", "country": "GB"},
            "handle": {},
        },
        "evidence": [{"label": "Printed supplier", "source_model": "storage.File", "source_id": "fil_example"}],
        "context": {"invoice_id": "inv_example", "draft_revision": 2},
    }
    run = engine.start(_identity_workflow(), party, operator, input=JsonPresence(True, proposal))
    advance_once(run)
    execute_started(run)
    with system_context(reason="test identity decision"):
        assert step_run_for(run, "review").error == ""
        decision = Decision._base_manager.get(step_run__run=run)
    assert (decision.target_model, decision.target_id) == ("parties.Party", str(party.sqid))
    delegated = {**proposal, "selection_decision_id": str(decision.sqid)}
    with pytest.raises(ValidationError, match="not completed in this run"):
        IdentityReviewStepImpl().run(
            SimpleNamespace(input=delegated, run=run, step=SimpleNamespace(config={})), now=timezone.now(),
        )
    resolution = {**decision.payload, "name_action": "replace", "address_action": "add", "handle_action": "keep"}
    assert engine.decide(decision, "complete", payload=resolution, actor=operator).validation_error is None
    with system_context(reason="fixture completed supplier selection"):
        decision.refresh_from_db()
        models.QuerySet.update(
            Decision._base_manager.filter(pk=decision.pk),
            resolution={**decision.resolution, "party_id": str(party.sqid)},
        )
        decision.refresh_from_db()
    delegated_review = IdentityReviewStepImpl().run(
        SimpleNamespace(input=delegated, run=run, step=SimpleNamespace(config={})), now=timezone.now(),
    )
    assert delegated_review.decisions[0].target_authority_decision_id == str(decision.sqid)
    with system_context(reason="test mismatched selection party"):
        other_party = Party._base_manager.create(display_name="Other Supplier", created_by=operator)
    with pytest.raises(ValidationError, match="chose a different Party"):
        IdentityReviewStepImpl().run(
            SimpleNamespace(
                input={**delegated, "party_id": str(other_party.sqid)},
                run=run,
                step=SimpleNamespace(config={}),
            ),
            now=timezone.now(),
        )
    stranger = User.objects.create_user(username="identity-selection-revoked")
    with system_context(reason="fixture selection resolver without current Party access"):
        models.QuerySet.update(
            Decision._base_manager.filter(pk=decision.pk), resolved_by=str(to_subject_ref(stranger)),
        )
    with pytest.raises(ValidationError, match="Party was not found"):
        IdentityReviewStepImpl().run(
            SimpleNamespace(input=delegated, run=run, step=SimpleNamespace(config={})), now=timezone.now(),
        )
    with system_context(reason="restore completed supplier selection resolver"):
        models.QuerySet.update(
            Decision._base_manager.filter(pk=decision.pk), resolved_by=str(to_subject_ref(operator)),
        )
    foreign_run = engine.start(_identity_workflow(), party, operator, input=JsonPresence(True, proposal))
    with pytest.raises(ValidationError, match="not completed in this run"):
        IdentityReviewStepImpl().run(
            SimpleNamespace(input=delegated, run=foreign_run, step=SimpleNamespace(config={})),
            now=timezone.now(),
        )
    with pytest.raises(ValidationError, match="not completed"):
        IdentityApplyStepImpl().run(
            SimpleNamespace(input={"decisions": [str(decision.sqid)]}, run=foreign_run),
            now=timezone.now(),
        )
    run_to_terminal(run)
    with system_context(reason="test identity result"):
        assert step_run_for(run, "apply").error == "", step_run_for(run, "apply").input
        party.refresh_from_db()
        address = Address._base_manager.get(party=party)
    assert party.display_name == "Example Supplier"
    assert (address.street, address.city, address.country) == ("10 Example Road", "Exampleton", "GB")
    assert step_run_for(run, "apply").output["context"] == proposal["context"]

    replay = IdentityApplyStepImpl().run(
        SimpleNamespace(input={"decisions": [str(decision.sqid)]}, run=run), now=timezone.now(),
    )
    assert replay.outcome == "applied"
    assert replay.output["address_result"] == "already_applied"
    with system_context(reason="test identity replay remains singular"):
        assert Address._base_manager.filter(party=party).count() == 1

    unchanged_proposal = {**proposal, "proposed": {"name": "Example Supplier", "address": {
        "street": "10 Example Road", "city": "Exampleton", "country": "GB",
    }, "handle": {}}}
    unchanged_review = IdentityReviewStepImpl().run(
        SimpleNamespace(input=unchanged_proposal, run=run, step=SimpleNamespace(config={})), now=timezone.now(),
    )
    unchanged_apply = IdentityApplyStepImpl().run(
        SimpleNamespace(input={"review": unchanged_review.output}, run=run), now=timezone.now(),
    )
    assert unchanged_review.outcome == "unchanged"
    assert unchanged_apply.output == {
        "party_id": str(party.sqid), "context": proposal["context"],
        "name_result": "kept", "address_result": "kept", "handle_result": "kept",
    }


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
        assert step_run_for(run, "gate").error == ""
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
    capability = unit.recovery_capability(
        attempt=SimpleNamespace(step_run=SimpleNamespace(step=step_run.step))
    )
    assert capability.mode == RecoveryMode.FRESH

    prepare = unit.recovery_capability(
        attempt=SimpleNamespace(
            step_run=SimpleNamespace(step=SimpleNamespace(config={"mode": "prepare"}))
        )
    )
    assert prepare.available is False


def test_autoconfig_registers_the_party_governance_step_keys() -> None:
    """The autoconfig contributes exactly the dedupe and identity step keys."""

    assert WORKFLOWS_PARTIES_SETTINGS == {
        "ANGEE_WORKFLOW_STEP_CLASSES.parties_dedupe_scan": ("angee.workflows_parties.steps.DedupeScanStepImpl"),
        "ANGEE_WORKFLOW_STEP_CLASSES.parties_dedupe_gate": ("angee.workflows_parties.steps.DedupeGateStepImpl"),
        "ANGEE_WORKFLOW_STEP_CLASSES.parties_dedupe_execute": ("angee.workflows_parties.steps.DedupeExecuteStepImpl"),
        "ANGEE_WORKFLOW_STEP_CLASSES.parties_identity_review": (
            "angee.workflows_parties.steps.IdentityReviewStepImpl"
        ),
        "ANGEE_WORKFLOW_STEP_CLASSES.parties_identity_apply": (
            "angee.workflows_parties.steps.IdentityApplyStepImpl"
        ),
    }
