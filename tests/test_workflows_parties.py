"""Tests for the parties dedupe workflow (scan → gate → prepare → map → unit)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import close_old_connections, connection
from django.utils import timezone
from rebac import PermissionDenied, system_context

from angee.base.refs import canonical_record_target
from angee.base.serialization import canonical_json_sha256
from angee.compose.permissions import apply_schema_paths, extension_source_map
from angee.fs import write_atomic
from angee.testing.models import Decision, StepAttempt, StepRun, WorkflowDispatch
from angee.workflows import engine
from angee.workflows import models as workflow_models
from angee.workflows.attempts import (
    ArtifactSpec,
    AttemptResult,
    AttemptResultKind,
    JsonPresence,
    RecoveryMode,
)
from angee.workflows.dispatch import WorkflowDispatchKind
from angee.workflows_parties.autoconfig import SETTINGS as WORKFLOWS_PARTIES_SETTINGS
from angee.workflows_parties.steps import DedupeExecuteStepImpl, IdentityApplyStepImpl, IdentityReviewStepImpl
from tests.test_messaging import (
    Address,
    Handle,
    MergeVeto,
    Party,
    PartyHandle,
)
from tests.workflows import (
    admit_workflow_actor,
    advance_once,
    execute_started,
    run_to_terminal,
    step_run_for,
    workflow_with_steps,
)

POSTGRES_IDENTITY = pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="PostgreSQL Party/Handle serialization contract",
)


User = get_user_model()


@pytest.fixture
def workflows_parties_tables(transactional_db: Any, tmp_path: Path) -> None:
    """Sync workflow and parties permissions from the composed schema sources."""

    del transactional_db
    app_configs = list(apps.get_app_configs())
    runtime_dir = tmp_path / "permissions"
    source_map = extension_source_map(app_configs)
    for relpath, text in source_map.items():
        write_atomic(runtime_dir / relpath, text)
    apply_schema_paths(app_configs, runtime_dir, sources=source_map)
    call_command("rebac", "sync", verbosity=0)


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
                "input_binding": {"kind": "step_output", "step_key": "gate", "path": []},
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


def _identity_workflow(*, config: dict[str, Any] | None = None) -> Any:
    return workflow_with_steps(
        name="Review identity",
        steps=(
            {
                "key": "review",
                "step_class": "parties_identity_review",
                "config": config or {},
                "input_binding": {"kind": "workflow_input", "path": []},
            },
            {
                "key": "apply",
                "step_class": "parties_identity_apply",
                "config": {},
                "input_binding": {"kind": "step_output", "step_key": "review", "path": []},
            },
        ),
        edges=(("review", "apply", "completed"), ("review", "apply", "unchanged")),
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("disposition", ("confirm", "dismiss"))
def test_party_handle_review_delivers_exact_nonterminal_artifact_runs(
    workflows_parties_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
    disposition: str,
) -> None:
    """One identity disposition wakes exact-link and stable-handle subscribers."""

    del workflows_parties_tables, no_workflow_queue
    operator = User.objects.create_user(username="handle-reviewer")
    with system_context(reason="test handle review fixtures"):
        party = Party._base_manager.create(display_name="Claimed counterparty", created_by=operator)
        handle = Handle._base_manager.create(platform="email", value="contact@example.test", created_by=operator)
        link = PartyHandle._base_manager.create(
            party=party,
            handle=handle,
            confidence=0.4,
            source="email_match",
            created_by=operator,
        )
    workflow = workflow_with_steps(
        name="Wait for handle review",
        steps=({"key": "hold", "step_class": "parties_dedupe_scan", "config": {"limit": 50}},),
        edges=(),
    )

    def retain(target: Any, *, terminal: bool = False) -> Any:
        run = engine.start(workflow, party, admit_workflow_actor(workflow, operator))
        advance_once(run)
        with system_context(reason="test retain handle artifact"):
            step_run = StepRun.objects.get(run=run)
            attempt = step_run.current_attempt
        StepAttempt.objects.admit_invocation(
            attempt.pk,
            lease_token=attempt.lease_token,
            at=timezone.now(),
        )
        StepAttempt.objects.finalize(
            attempt.pk,
            lease_token=attempt.lease_token,
            result=AttemptResult(
                AttemptResultKind.DONE if terminal else AttemptResultKind.WAIT,
                artifacts_present=True,
                artifacts=(ArtifactSpec(target, "Identity evidence"),),
                requested_until=None if terminal else timezone.now(),
            ),
            recorded_at=timezone.now(),
        )
        if terminal:
            engine.advance(run.pk)
        run.refresh_from_db()
        return run

    first = retain(link)
    second = retain(link)
    stable = retain(handle)
    terminal = retain(link, terminal=True)
    unrelated = retain(workflow)

    def fail_broad_deliver(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("PartyHandle review must wake exact artifact waits, not the whole run")

    monkeypatch.setattr(engine, "deliver", fail_broad_deliver)

    getattr(link.with_actor(operator), disposition)()

    with system_context(reason="inspect retained handle delivery intent"):
        delivery = WorkflowDispatch.objects.get(
            kind=WorkflowDispatchKind.ARTIFACT_DELIVERY,
            artifact_content_type=canonical_record_target(link).content_type,
            artifact_object_id=link.pk,
        )
        handle_delivery = WorkflowDispatch.objects.get(
            kind=WorkflowDispatchKind.ARTIFACT_DELIVERY,
            artifact_content_type=canonical_record_target(handle).content_type,
            artifact_object_id=handle.pk,
        )
    assert delivery.consumed_at is None
    assert WorkflowDispatch.objects.deliver(
        delivery.pk, expected_kind=WorkflowDispatchKind.ARTIFACT_DELIVERY
    ) == {"runs": 2, "woken": 2}
    assert WorkflowDispatch.objects.deliver(
        handle_delivery.pk, expected_kind=WorkflowDispatchKind.ARTIFACT_DELIVERY
    ) == {"runs": 1, "woken": 1}

    # The durable artifact delivery bumps the run-scoped generation only for the exact
    # external waits retaining this link; terminal and unrelated holds are left
    # parked, and no unrelated approval or timer row in those runs is touched.
    for woken in (first, second, stable):
        woken.refresh_from_db()
        assert woken.deliveries == 1
    for parked in (terminal, unrelated):
        parked.refresh_from_db()
        assert parked.deliveries == 0


@pytest.mark.django_db(transaction=True)
def test_party_handle_delete_notifies_stable_handle_after_resolution(
    workflows_parties_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The supported delete path publishes its surviving collection owner."""

    del workflows_parties_tables, no_workflow_queue
    operator = User.objects.create_user(username="handle-delete-reviewer")
    with system_context(reason="test handle delete fixture"):
        party = Party._base_manager.create(display_name="Deleted counterparty", created_by=operator)
        handle = Handle._base_manager.create(platform="email", value="delete@example.test", created_by=operator)
        link = PartyHandle.objects.link(
            party,
            handle,
            is_confirmed=True,
            created_by_id=operator.pk,
        )
    delivered: list[tuple[str, int | None, int | None]] = []

    def record_delivery(resource: Any) -> None:
        if isinstance(resource, Handle):
            resource.refresh_from_db()
            delivered.append((resource._meta.label, resource.pk, resource.party_id))
        else:
            delivered.append((resource._meta.label, resource.pk, None))

    monkeypatch.setattr(engine, "schedule_artifact_delivery", record_delivery)
    with system_context(reason="test handle delete"):
        link.delete()

    assert delivered == [(handle._meta.label, handle.pk, None)]
    with pytest.raises(TypeError, match="must use link"):
        PartyHandle.objects.filter(pk=link.pk).update(is_confirmed=True)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("config", "party_label", "address_label"),
    (
        ({}, "Party", "Primary"),
        ({"party_label": "Member", "default_address_label": "Office"}, "Member", "Office"),
        ({"party_label": "VIP", "default_address_label": "Office"}, "VIP", "Office"),
    ),
)
def test_identity_review_freezes_context_and_applies_name_and_address(
    workflows_parties_tables: None,
    no_workflow_queue: None,
    config: dict[str, Any],
    party_label: str,
    address_label: str,
) -> None:
    del workflows_parties_tables, no_workflow_queue
    operator = User.objects.create_user(username="identity-reviewer")
    with system_context(reason="test identity fixture"):
        party = Party._base_manager.create(display_name="Old Counterparty", created_by=operator)
    proposal = {
        "party_id": str(party.sqid),
        "proposed": {
            "name": "Example Counterparty",
            "address": {"street": "10 Example Road", "city": "Exampleton", "country": "GB"},
            "handle": {},
        },
        "evidence": [{"label": "Printed counterparty", "source_model": "storage.File", "source_id": "fil_example"}],
        "context": {"document_id": "doc_example", "draft_revision": 2},
    }
    workflow = _identity_workflow(config=config)
    run = engine.start(workflow, party, admit_workflow_actor(workflow, operator), input=JsonPresence(True, proposal))
    advance_once(run)
    execute_started(run)
    with system_context(reason="test identity decision"):
        assert step_run_for(run, "review").error == ""
        decision = Decision._base_manager.get(step_run__run=run)
    assert (decision.target_model, decision.target_id) == ("parties.Party", str(party.sqid))
    fields = decision.form_schema["properties"]
    assert fields["name_action"] == {
        "type": "string",
        "enum": ["keep", "replace"],
        "default": "keep",
        "label": f"{party_label} name",
        "description": "Keep the current canonical name or use the proposed name from this source.",
        "options": [
            {"value": "keep", "label": f"Keep current {party_label} name"},
            {"value": "replace", "label": f"Use proposed {party_label} name"},
        ],
    }
    assert fields["address_action"]["label"] == f"{party_label} address"
    assert fields["address_action"]["options"][0] == {
        "value": "keep",
        "label": f"Keep current {party_label} addresses",
    }
    assert fields["handle_action"]["label"] == f"{party_label} contact"
    assert fields["handle_action"]["options"][0] == {
        "value": "keep",
        "label": "Keep current contact status",
    }
    resolution = {
        "action": "apply_identity",
        "name_action": "replace",
        "address_action": "add",
        "handle_action": "keep",
    }
    assert engine.decide(decision, "complete", payload=resolution, actor=operator).validation_error is None
    with system_context(reason="fixture completed counterparty selection"):
        decision.refresh_from_db()
    run_to_terminal(run)
    with system_context(reason="test identity result"):
        assert step_run_for(run, "apply").error == "", step_run_for(run, "apply").input
        party.refresh_from_db()
        address = Address._base_manager.get(party=party)
    assert party.display_name == "Example Counterparty"
    assert address.label == address_label
    assert (address.street, address.city, address.country) == ("10 Example Road", "Exampleton", "GB")
    assert step_run_for(run, "apply").output["context"] == proposal["context"]

    with system_context(reason="test identity retained result replay"):
        applied = step_run_for(run, "apply")
        attempt = StepAttempt.objects.get(pk=applied.current_attempt_id)
        dispatch = WorkflowDispatch.objects.get(step_attempt=attempt)
        assert attempt.applied_at is not None
        retained_output = attempt.output
    assert engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token)["executed"] == 0
    with system_context(reason="test identity retained result unchanged"):
        attempt.refresh_from_db()
    assert attempt.output == retained_output
    with system_context(reason="test identity replay remains singular"):
        assert Address._base_manager.filter(party=party).count() == 1

    unchanged_proposal = {
        **proposal,
        "proposed": {
            "name": "Example Counterparty",
            "address": {
                "street": "10 Example Road",
                "city": "Exampleton",
                "country": "GB",
            },
            "handle": {},
        },
    }
    reviewed = step_run_for(run, "review")
    reviewed.input = unchanged_proposal
    unchanged_review = IdentityReviewStepImpl().run(reviewed, now=timezone.now())
    applied.input = {"review": unchanged_review.output}
    unchanged_apply = IdentityApplyStepImpl().run(applied, now=timezone.now())
    assert unchanged_review.outcome == "unchanged"
    assert unchanged_apply.output == {
        "party_id": str(party.sqid),
        "context": proposal["context"],
        "name_result": "kept",
        "address_result": "kept",
        "handle_result": "kept",
    }

    equivalent_country = {
        **proposal,
        "proposed": {
            "name": "Example Counterparty",
            "address": {
                "street": "10 Example Road",
                "city": "Exampleton",
                "country": "United Kingdom",
            },
            "handle": {},
        },
    }
    reviewed.input = equivalent_country
    assert IdentityReviewStepImpl().run(reviewed, now=timezone.now()).outcome == "unchanged"

    changed_country = {
        **proposal,
        "proposed": {
            "name": "Example Counterparty",
            "address": {
                "street": "10 Example Road",
                "city": "Exampleton",
                "country": "CA",
            },
            "handle": {},
        },
    }
    reviewed.input = changed_country
    assert IdentityReviewStepImpl().run(reviewed, now=timezone.now()).kind == "suspend"


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

    run = engine.start(workflow, None, admit_workflow_actor(workflow, operator))
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
    attempted = engine.decide(
        decision,
        "complete",
        payload={"action": "apply_pairs", "pairs": resolved},
        actor=operator,
    )
    assert attempted.validation_error is None

    run_to_terminal(run)
    run.refresh_from_db()
    assert run.status == workflow_models.RunStatus.SUCCEEDED

    prepare = step_run_for(run, "prepare")
    assert prepare.output == [
        {"decision_id": decision.pk, "pair_index": 0},
        {"decision_id": decision.pk, "pair_index": 1},
    ]

    with system_context(reason="test dedupe assertions"):
        drop_a.refresh_from_db()
        assert drop_a.merged_into_id == keep_a.pk
        assert MergeVeto._base_manager.count() == 1
        veto = MergeVeto._base_manager.get()
        assert {veto.party_a_id, veto.party_b_id} == {keep_b.pk, sep_b.pk}
        # A second scan proposes nothing: one pair merged, the other vetoed.
        assert Party.objects.duplicate_candidates(limit=50) == []

        units = list(StepRun.objects.filter(run=run, step__key="apply_unit").order_by("pk"))
        assert len(units) == 2
        retained = [
            (
                StepAttempt.objects.get(pk=unit.current_attempt_id),
                WorkflowDispatch.objects.get(step_attempt_id=unit.current_attempt_id),
            )
            for unit in units
        ]
        assert all(attempt.applied_at is not None for attempt, _ in retained)
    for attempt, dispatch in retained:
        assert engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token)["executed"] == 0
    with system_context(reason="test dedupe retained replay"):
        drop_a.refresh_from_db()
        assert drop_a.merged_into_id == keep_a.pk
        assert MergeVeto._base_manager.count() == 1


@pytest.mark.django_db(transaction=True)
def test_dedupe_scan_without_candidates_routes_empty(
    workflows_parties_tables: None,
    no_workflow_queue: None,
) -> None:
    """An empty directory ends the run after the scan with no decision."""

    del workflows_parties_tables, no_workflow_queue
    operator = User.objects.create_user(username="dedupe-empty")
    workflow = _dedupe_workflow()

    run = engine.start(workflow, None, admit_workflow_actor(workflow, operator))
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

    run = engine.start(workflow, None, admit_workflow_actor(workflow, operator))
    advance_once(run)
    execute_started(run)
    advance_once(run)
    execute_started(run)
    with system_context(reason="test dedupe decision"):
        decision = Decision.objects.get(step_run__run=run)
    tampered = [dict(pair) for pair in decision.payload["pairs"]]
    tampered[0]["left"] = "pty_forged00"
    attempted = engine.decide(
        decision,
        "complete",
        payload={"action": "apply_pairs", "pairs": tampered},
        actor=operator,
    )
    assert attempted.validation_error is None

    run_to_terminal(run, allow_failed={run.pk})
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

    def apply() -> str:
        return Party.objects.apply_duplicate_pair(
            left_id=str(keep.sqid),
            right_id=str(drop.sqid),
            survivor="left",
            action="merge",
            actor=operator,
        )

    assert apply() == "merged"
    assert apply() == "already_merged"
    unit = DedupeExecuteStepImpl()
    step_run = SimpleNamespace(step=SimpleNamespace(config={"mode": "unit"}))

    capability = unit.recovery_capability(attempt=SimpleNamespace(step_run=SimpleNamespace(step=step_run.step)))
    assert capability.mode == RecoveryMode.FRESH

    prepare = unit.recovery_capability(
        attempt=SimpleNamespace(step_run=SimpleNamespace(step=SimpleNamespace(config={"mode": "prepare"})))
    )
    assert prepare.available is False


@pytest.mark.django_db(transaction=True)
def test_apply_unit_authorizes_and_attributes_merge_and_veto_to_decision_resolver(
    workflows_parties_tables: None,
) -> None:
    """The run owner cannot replace the distinct accountable Decision resolver."""

    del workflows_parties_tables
    run_owner = User.objects.create_user(username="dedupe-run-owner")
    resolver = User.objects.create_user(username="dedupe-decision-resolver")
    with system_context(reason="test resolver-owned dedupe fixtures"):
        merge_into, merge_source = _duplicate_pair(
            resolver,
            named="Resolver merge target",
            digits="+442071838750",
            spaced="+44 20 7183 8750",
        )
        veto_left, veto_right = _duplicate_pair(
            resolver,
            named="Resolver veto target",
            digits="+33142345678",
            spaced="+33 1 42 34 56 78",
        )
    for party in (merge_into, merge_source, veto_left, veto_right):
        assert party.with_actor(resolver).has_access("write")
        assert not party.with_actor(run_owner).has_access("write")

    def apply(left: Any, right: Any, action: str, *, actor: Any = resolver) -> str:
        return Party.objects.apply_duplicate_pair(
            left_id=str(left.sqid),
            right_id=str(right.sqid),
            survivor="left",
            action=action,
            actor=actor,
        )

    with system_context(reason="test explicit actor cannot inherit engine elevation"):
        with pytest.raises((PermissionDenied, ValidationError)):
            apply(merge_into, merge_source, "merge", actor=run_owner)
        assert apply(merge_into, merge_source, "merge") == "merged"
        assert apply(veto_left, veto_right, "keep_separate") == "vetoed"

    with system_context(reason="test resolver dedupe attribution"):
        merge_source.refresh_from_db()
        veto = MergeVeto._base_manager.get()
    assert merge_source.updated_by_id == resolver.pk
    assert veto.created_by_id == resolver.pk
    assert veto.updated_by_id == resolver.pk
    assert veto.with_actor(resolver).has_access("write")
    assert not veto.with_actor(run_owner).has_access("write")


def test_autoconfig_registers_the_party_governance_step_keys() -> None:
    """The autoconfig contributes exactly the dedupe and identity step keys."""

    assert WORKFLOWS_PARTIES_SETTINGS == {
        "ANGEE_WORKFLOW_STEP_CLASSES.parties_dedupe_scan": ("angee.workflows_parties.steps.DedupeScanStepImpl"),
        "ANGEE_WORKFLOW_STEP_CLASSES.parties_dedupe_gate": ("angee.workflows_parties.steps.DedupeGateStepImpl"),
        "ANGEE_WORKFLOW_STEP_CLASSES.parties_dedupe_execute": ("angee.workflows_parties.steps.DedupeExecuteStepImpl"),
        "ANGEE_WORKFLOW_STEP_CLASSES.parties_identity_review": ("angee.workflows_parties.steps.IdentityReviewStepImpl"),
        "ANGEE_WORKFLOW_STEP_CLASSES.parties_identity_apply": ("angee.workflows_parties.steps.IdentityApplyStepImpl"),
    }


@pytest.mark.django_db(transaction=True)
def test_identity_owner_checks_basis_and_rolls_back_all_changes(
    workflows_parties_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflows_parties_tables
    actor = User.objects.create_user(username="identity-atomic-owner")
    with system_context(reason="identity operation fixture"):
        party = Party.objects.create(display_name="Original", created_by=actor)
    _, current = Party.objects.identity_snapshot(str(party.sqid), actor=actor)
    proposed = {"name": "Replacement", "address": {"label": "Contact", "street": "Main 1"}, "handle": {}}
    choices = {"name_action": "replace", "address_action": "add", "handle_action": "keep"}
    outcome, _ = Party.objects.apply_identity(
        party_id=str(party.sqid),
        expected_facts_hash="stale",
        proposed=proposed,
        choices=choices,
        actor=actor,
    )
    assert outcome == "conflict"

    def fail_address(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("address failure")

    monkeypatch.setattr(type(Address.objects), "attach_exact", fail_address)
    with pytest.raises(RuntimeError, match="address failure"):
        Party.objects.apply_identity(
            party_id=str(party.sqid),
            expected_facts_hash=canonical_json_sha256(current),
            proposed=proposed,
            choices=choices,
            actor=actor,
        )
    with system_context(reason="identity rollback assertion"):
        party.refresh_from_db()
    assert party.display_name == "Original"


@POSTGRES_IDENTITY
@pytest.mark.django_db(transaction=True)
def test_identity_confirmation_and_competing_admission_share_total_lock_order(
    workflows_parties_tables: None,
) -> None:
    """Concurrent Handle claims serialize without the former Party/Handle deadlock cycle."""

    del workflows_parties_tables
    actor = User.objects.create_user(username="identity-lock-owner")
    with system_context(reason="identity lock-order fixture"):
        first = Party.objects.create(display_name="First", created_by=actor)
        second = Party.objects.create(display_name="Second", created_by=actor)
        handle = Handle.objects.create(
            platform=Handle.Platform.EMAIL,
            value="identity-lock@example.test",
            created_by=actor,
        )
        link = PartyHandle.objects.link(first, handle, confidence=0.4, created_by_id=actor.pk)
    start = Barrier(2)

    def confirm() -> None:
        close_old_connections()
        start.wait(timeout=5)
        PartyHandle.objects._transition(link, action="confirm", actor=actor)
        close_old_connections()

    def admit_competitor() -> None:
        close_old_connections()
        start.wait(timeout=5)
        PartyHandle.objects.link(second, handle, confidence=0.3, created_by_id=actor.pk)
        close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_result = pool.submit(confirm)
        second_result = pool.submit(admit_competitor)
        first_result.result(timeout=10)
        second_result.result(timeout=10)

    with system_context(reason="identity lock-order assertion"):
        handle.refresh_from_db()
    assert handle.party_id == first.pk
    assert handle.party_link_confirmed is True


@POSTGRES_IDENTITY
@pytest.mark.django_db(transaction=True)
def test_identity_suggestion_and_transition_share_total_lock_order(
    workflows_parties_tables: None,
) -> None:
    """Rule insertion and review serialize through the same complete identity lock set."""

    del workflows_parties_tables
    actor = User.objects.create_user(username="identity-suggest-race")
    with system_context(reason="identity suggestion race fixture"):
        first = Party.objects.create(display_name="First", created_by=actor)
        second = Party.objects.create(display_name="Second", created_by=actor)
        handle = Handle.objects.create(
            platform=Handle.Platform.EMAIL,
            value="suggest-race@example.test",
            created_by=actor,
        )
        link = PartyHandle.objects.link(first, handle, confidence=0.4, created_by_id=actor.pk)
    start = Barrier(2)

    def confirm() -> None:
        close_old_connections()
        start.wait(timeout=5)
        PartyHandle.objects._transition(link, action="confirm", actor=actor)
        close_old_connections()

    def suggest() -> None:
        close_old_connections()
        start.wait(timeout=5)
        PartyHandle.objects._suggest(
            second,
            handle,
            confidence=0.3,
            metadata={"evidence": {"kind": "race"}},
            created_by_id=actor.pk,
        )
        close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        confirmed = pool.submit(confirm)
        suggested = pool.submit(suggest)
        confirmed.result(timeout=10)
        suggested.result(timeout=10)

    with system_context(reason="identity suggestion race assertion"):
        handle.refresh_from_db()
    assert handle.party_id == first.pk
    assert handle.party_link_confirmed is True
    assert PartyHandle.objects.filter(handle=handle, party=second).exists()


@POSTGRES_IDENTITY
@pytest.mark.django_db(transaction=True)
def test_identity_delete_repair_and_transition_do_not_reverse_lock_order(
    workflows_parties_tables: None,
) -> None:
    """Delete repair waits for commit before competing with an identity transition."""

    del workflows_parties_tables
    actor = User.objects.create_user(username="identity-delete-race")
    with system_context(reason="identity delete race fixture"):
        first = Party.objects.create(display_name="First", created_by=actor)
        second = Party.objects.create(display_name="Second", created_by=actor)
        handle = Handle.objects.create(
            platform=Handle.Platform.EMAIL,
            value="delete-race@example.test",
            created_by=actor,
        )
        winner = PartyHandle.objects.link(first, handle, confidence=0.9, created_by_id=actor.pk)
        remaining = PartyHandle.objects.link(second, handle, confidence=0.4, created_by_id=actor.pk)
    start = Barrier(2)

    def delete_winner() -> None:
        close_old_connections()
        start.wait(timeout=5)
        PartyHandle._base_manager.get(pk=winner.pk).delete()
        close_old_connections()

    def confirm_remaining() -> None:
        close_old_connections()
        start.wait(timeout=5)
        PartyHandle.objects._transition(remaining, action="confirm", actor=actor)
        close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        deleted = pool.submit(delete_winner)
        confirmed = pool.submit(confirm_remaining)
        deleted.result(timeout=10)
        confirmed.result(timeout=10)

    with system_context(reason="identity delete race assertion"):
        handle.refresh_from_db()
    assert handle.party_id == second.pk
    assert handle.party_link_confirmed is True


@pytest.mark.django_db(transaction=True)
def test_identity_snapshot_hides_private_handles_even_when_the_link_is_readable(
    workflows_parties_tables: None,
    no_workflow_queue: None,
) -> None:
    del workflows_parties_tables, no_workflow_queue
    actor = User.objects.create_user(username="identity-visible-link-owner")
    other = User.objects.create_user(username="identity-private-handle-owner")
    with system_context(reason="identity snapshot relation-read fixture"):
        party = Party.objects.create(display_name="Visible Party", created_by=actor)
        visible_handle = Handle.objects.create(
            platform=Handle.Platform.EMAIL,
            value="visible@example.test",
            created_by=actor,
        )
        hidden_handle = Handle.objects.create(
            platform=Handle.Platform.EMAIL,
            value="private@example.test",
            created_by=other,
        )
        visible_link = PartyHandle.objects.link(party, visible_handle, created_by_id=actor.pk)
        hidden_link = PartyHandle.objects.link(party, hidden_handle, created_by_id=actor.pk)
        assert hidden_link.with_actor(actor).has_access("read")
        assert not hidden_handle.with_actor(actor).has_access("read")
        _, snapshot = Party.objects.identity_snapshot(str(party.sqid), actor=actor)
    assert [row["id"] for row in snapshot["handles"]] == [str(visible_link.sqid)]
    assert snapshot["handles"][0]["value"] == "visible@example.test"
