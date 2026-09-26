"""Decision admission requires standing reviewer read on every evidence record."""

from __future__ import annotations

from io import StringIO
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from rebac import (
    ObjectRef,
    RelationshipTuple,
    SubjectRef,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.models import Relationship, RelationshipRegistry
from rebac.relationships import delete_relationship

from angee.base.identity import public_subject_ref
from angee.workflows import managers
from angee.workflows.attempts import DecisionSpec, deserialize_decision_specs
from angee.workflows.decision_actions import (
    ReviewAction,
    ReviewFact,
    ReviewRecordReference,
    build_decision_action,
    decision_evidence_refs,
)
from angee.workflows.steps import StepResult
from angee.workflows.testing.drivers import advance_once, execute_started
from angee.workflows.testing.models import Decision, StepRun
from tests.conftest import create_platform_admin
from tests.messaging_models import Party
from tests.workflows import FixtureStep, start_run, workflow_with_steps

User = get_user_model()
_APPROVE = (ReviewAction(value="approve", label="Approve", verdict="COMPLETE"),)


@pytest.fixture(autouse=True)
def quiet_decision_publication(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(managers, "enqueue_dispatch_publisher", lambda **kwargs: None)


def _reference(record: Any, label: str = "") -> ReviewRecordReference:
    return ReviewRecordReference(model=record._meta.label, id=str(record.sqid), label=label)


def _party(owner: Any, name: str) -> Any:
    with system_context(reason="test decision evidence party"):
        return Party.objects.create(display_name=name, created_by=owner)


def _grant_party_read(party: Any, subject: SubjectRef) -> None:
    with system_context(reason="test decision evidence grant"):
        write_relationships([RelationshipTuple(to_object_ref(party), "reader", subject)])


def test_evidence_refs_cover_references_fact_subjects_and_evidence_once_in_order() -> None:
    first = ReviewRecordReference(model="parties.Party", id="pty_b", label="Second")
    second = ReviewRecordReference(model="parties.Party", id="pty_a", label="First")
    source = ReviewRecordReference(model="storage.File", id="fil_a")
    authored = build_decision_action(
        actions=_APPROVE,
        facts=(
            ReviewFact(pointer="/name", label="Name", value="A", subject=first, authority="source", evidence=(source,)),
            ReviewFact(pointer="/other", label="Other", value="B", authority="unverified", evidence=(second, source)),
        ),
        references=(first,),
    )

    refs = decision_evidence_refs(authored.decision_schema, authored.payload)

    assert [(ref.model, ref.id) for ref in refs] == [
        ("parties.Party", "pty_a"),
        ("parties.Party", "pty_b"),
        ("storage.File", "fil_a"),
    ]
    assert decision_evidence_refs({}, {}) == ()
    single = build_decision_action(actions=_APPROVE, references=source)
    assert decision_evidence_refs(single.decision_schema, single.payload) == (source,)


@pytest.mark.parametrize("field_name", ("assignees", "escalation"))
def test_reviewer_without_read_on_evidence_is_named_in_the_admission_error(
    composed_tables: None,
    field_name: str,
) -> None:
    del composed_tables
    issuer = create_platform_admin(f"evidence-issuer-{field_name}")
    reviewer = User.objects.create_user(username=f"evidence-reviewer-{field_name}")
    party = _party(issuer, "Reviewed counterparty")
    evidence = (_reference(party, "Counterparty"),)
    subject = to_subject_ref(reviewer)
    readers: dict[str, tuple[SubjectRef, ...]] = {"assignees": (), "escalation": ()}
    readers[field_name] = (subject,)

    with pytest.raises(ValidationError) as denied:
        Decision.objects._require_evidence_readers(evidence, actor=issuer, readers=readers)

    assert denied.value.error_dict[field_name][0].code == "decision_evidence_unreadable"
    assert denied.value.message_dict == {
        field_name: [f"{public_subject_ref(subject)} cannot read Counterparty {party.sqid}."]
    }

    _grant_party_read(party, subject)
    Decision.objects._require_evidence_readers(evidence, actor=issuer, readers=readers)


def test_issuer_must_read_evidence_and_unlabelled_records_use_the_model_name(composed_tables: None) -> None:
    del composed_tables
    owner = create_platform_admin("evidence-owner")
    issuer = User.objects.create_user(username="evidence-plain-issuer")
    party = _party(owner, "Hidden counterparty")

    with pytest.raises(ValidationError) as denied:
        Decision.objects._require_evidence_readers((_reference(party),), actor=issuer, readers={})

    assert denied.value.error_dict["issuer"][0].code == "decision_evidence_unreadable"
    assert str(party._meta.verbose_name) in denied.value.message_dict["issuer"][0]


def test_group_assignee_passes_only_when_its_userset_holds_the_grant(composed_tables: None) -> None:
    del composed_tables
    issuer = create_platform_admin("evidence-group-issuer")
    member = User.objects.create_user(username="evidence-group-member")
    party = _party(issuer, "Group reviewed counterparty")
    group = SubjectRef.of("auth/group", "41", "member")
    evidence = (_reference(party),)
    _grant_party_read(party, to_subject_ref(member))

    with pytest.raises(ValidationError, match=f"{public_subject_ref(group)} cannot read"):
        Decision.objects._require_evidence_readers(evidence, actor=issuer, readers={"assignees": (group,)})

    _grant_party_read(party, group)
    Decision.objects._require_evidence_readers(evidence, actor=issuer, readers={"assignees": (group,)})


def test_suspension_admits_a_decision_only_for_reviewers_who_read_its_evidence(
    composed_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del composed_tables, no_workflow_queue
    issuer = create_platform_admin("evidence-suspension-issuer")
    reviewer = User.objects.create_user(username="evidence-suspension-reviewer")
    party = _party(issuer, "Suspension counterparty")
    review = build_decision_action(actions=_APPROVE, references=_reference(party))

    def suspend(self: FixtureStep, step_run: Any, *, now: Any) -> StepResult:
        del self, step_run, now
        return StepResult.suspend(
            decisions=(
                DecisionSpec(
                    action="review",
                    assignees=(str(to_subject_ref(reviewer)),),
                    payload=review.payload,
                    decision_schema=review.decision_schema,
                ),
            ),
        )

    monkeypatch.setattr(FixtureStep, "run", suspend)
    workflow = workflow_with_steps(
        name="Evidence admission",
        steps=({"key": "gate", "step_class": "fixture", "config": {}},),
        edges=(),
    )

    denied = start_run(workflow, actor=issuer)
    advance_once(denied)
    with pytest.raises(ValidationError, match="cannot read"):
        execute_started(denied)
    with system_context(reason="test evidence denied admission"):
        assert not Decision.objects.filter(step_run__run=denied).exists()

    _grant_party_read(party, to_subject_ref(reviewer))
    admitted = start_run(workflow, actor=issuer)
    advance_once(admitted)
    execute_started(admitted)
    with system_context(reason="test evidence admitted decision"):
        decision = Decision.objects.get(step_run__run=admitted)
        assert StepRun.objects.get(pk=decision.step_run_id).status == "waiting"
    assert decision_evidence_refs(review.decision_schema, decision.payload) == (_reference(party),)
    assert Decision.objects.pending_evidence_failures() == ()

    with system_context(reason="test evidence revoked read"):
        delete_relationship(RelationshipTuple(to_object_ref(party), "reader", to_subject_ref(reviewer)))
    ((stale, error),) = Decision.objects.pending_evidence_failures()
    assert stale.pk == decision.pk
    assert error.error_dict["assignees"][0].code == "decision_evidence_unreadable"


def test_purge_removes_only_retired_decision_access_from_both_stores_and_declarations(
    composed_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del composed_tables, no_workflow_queue
    issuer = create_platform_admin("evidence-purge-issuer")
    reviewer = User.objects.create_user(username="evidence-purge-reviewer")

    def suspend(self: FixtureStep, step_run: Any, *, now: Any) -> StepResult:
        del self, step_run, now
        return StepResult.suspend(
            decisions=(DecisionSpec(action="review", assignees=(str(to_subject_ref(reviewer)),)),),
        )

    monkeypatch.setattr(FixtureStep, "run", suspend)
    run = start_run(
        workflow_with_steps(
            name="Retired evidence access",
            steps=({"key": "gate", "step_class": "fixture", "config": {}},),
            edges=(),
        ),
        actor=issuer,
    )
    advance_once(run)
    execute_started(run)
    retired = {"relation": "pending_decision", "subject_type": "workflows/decision"}
    kept = RelationshipTuple(ObjectRef("storage/file", "7"), "viewer", SubjectRef.of("auth/user", "9"))
    with system_context(reason="test decision access purge fixtures"):
        attempt = Decision.objects.get(step_run__run=run).suspension_attempt
        declarations = [
            {**item, "record_access": [{"model": "storage.File", "id": "fil_7"}]} for item in attempt.result_decisions
        ]
        type(attempt).objects.filter(pk=attempt.pk).owner_update(result_decisions=declarations)
        for store in (Relationship, RelationshipRegistry):
            store.objects.create(resource_type="storage/file", resource_id="7", subject_id="3", **retired)
            store.objects.create(
                resource_type="storage/file",
                resource_id="7",
                relation="viewer",
                subject_type="workflows/decision",
                subject_id="3",
            )
        write_relationships([kept])
        before = {store: store.objects.count() for store in (Relationship, RelationshipRegistry)}

    preview = StringIO()
    call_command("purge_decision_record_access", stdout=preview)
    assert "would delete 1 denormalized and 1 registry" in preview.getvalue()
    with system_context(reason="test decision access purge preview"):
        assert all(store.objects.filter(**retired).count() == 1 for store in (Relationship, RelationshipRegistry))

    applied = StringIO()
    call_command("purge_decision_record_access", "--apply", "--check-pending", stdout=applied)
    assert "0 pending Decision(s) need attention" in applied.getvalue()
    assert "deleted 1 denormalized and 1 registry" in applied.getvalue()
    with system_context(reason="test decision access purge applied"):
        for store in (Relationship, RelationshipRegistry):
            assert not store.objects.filter(**retired).exists()
            assert store.objects.count() == before[store] - 1
        attempt.refresh_from_db()
        assert attempt.result_decisions == declarations
        (declaration,) = deserialize_decision_specs(attempt.result_decisions)
    assert declaration.assignees == (str(to_subject_ref(reviewer)),)
