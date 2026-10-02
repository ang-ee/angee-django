"""A complete, permissioned human decision lifecycle with no execution dependency."""

from datetime import timedelta
from typing import Annotated

import pytest
from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.db.models.deletion import ProtectedError
from django.db.models.functions import Now
from django.utils import timezone
from pydantic import Field
from rebac import (
    RelationshipTuple,
    actor_context,
    delete_relationship,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.errors import NoActorResolvedError

from angee.base.mixins import StaleRevisionError
from angee.base.scoping import system_queryset
from angee.decisions import managers as decision_managers
from angee.decisions import schema as decision_schema
from angee.decisions.contracts import DecisionContext, DecisionFact, DecisionRecordReference, DecisionRequest
from angee.decisions.forms import Action, Relation
from angee.decisions.policies import DecisionPolicy
from angee.decisions.signals import decision_group_settled
from angee.decisions.states import Verdict
from angee.decisions.testing.models import Decision, DecisionEvidence, DecisionGroup
from tests.conftest import addon_schema, create_platform_admin, create_user, execute_schema, result_data, vault_for


class Complete(Action, key="complete", label="Complete", verdict=Verdict.COMPLETED):
    """Record a bounded note as the answer."""

    note: str = Field(min_length=2)


class RetainClosed(DecisionPolicy):
    key = "all"
    """A registered strategy may retain a group after an unanswered closure."""

    @classmethod
    def settled(cls, decisions):
        return False


class Decline(Action, key="decline", label="Decline", verdict=Verdict.REJECTED):
    """Decline with a reason."""

    reason: str


class ChooseDocument(Action, key="choose", label="Choose document", verdict=Verdict.COMPLETED):
    """Select one readable record from a frozen picker."""

    document_id: Annotated[str, Relation("knowledge.Vault")]


class EditDocument(Action, key="edit", label="Edit document", verdict=Verdict.COMPLETED):
    """A picker requiring the selected record's write permission."""

    document_id: Annotated[str, Relation("knowledge.Vault", permission="write")]


@pytest.fixture
def people(composed_tables):
    """Plain people with standing access to a shared document reference."""

    issuer, reviewer, outsider = (create_user(name) for name in ("issuer", "reviewer", "outsider"))
    subject = vault_for(issuer, name="Document notes")
    write_relationships([
        RelationshipTuple(resource=to_object_ref(subject), relation="viewer", subject=to_subject_ref(reviewer)),
    ])
    return issuer, reviewer, outsider, subject


def request_for(people, **changes):
    """Declare one ordinary seat, leaving admission to its real owner."""

    issuer, reviewer, _outsider, subject = people
    return DecisionRequest(
        **{"kind": "note_review", "subject": subject, "assignees": [reviewer],
           "actions": [Complete, Decline], "basis": {"paragraph": 2}, **changes},
    )


@pytest.fixture
def delegated_request(people, composed_permissions):
    """Exercise intake's domain authority without assigning an explicit seat."""
    issuer, _reviewer, _outsider, _subject = people
    with system_context(reason="test.delegated_domain"):
        project = apps.get_model("projects", "Project").objects.create(title="Delegated review", owner=issuer)
        need = apps.get_model("intake", "Need").objects.create(project=project)
    return request_for(people, kind="intake.access", subject=need, assignees=None, requester=None)


def seat(group, index=0):
    """Read committed state without carrying a principal into another call."""

    return system_queryset(Decision).get(group=group, index=index)


def elapse_deadline(group, index=0):
    """Move an admitted deadline into the past using the database clock."""
    with system_context(reason="test.elapse_decision_deadline"):
        Decision.objects.filter(group=group, index=index).owner_update(expires_at=Now() - timedelta(seconds=1))


def answer(decision, reviewer, **changes):
    """Call the deciding verb with the form revision visible to the person."""

    return Decision.objects.decide(
        decision.pk, actor=reviewer,
        **{"revision": decision.revision, "action": "complete", "values": {"note": "Read"}, **changes},
    )


def test_complete_lifecycle_without_any_run(people):
    """Admission, an answer, settlement and consumption need only the decision addon."""

    issuer, reviewer, _outsider, subject = people
    group = Decision.objects.admit_group([request_for(people)], actor=issuer)
    decision = seat(group)
    assert decision.is_open and group.settled_at is None
    assert decision.requester_id == issuer.pk
    assert decision.subject_object_id == subject.pk
    assert decision.record_model_label == subject._meta.label
    assert decision.record_public_id == subject.sqid
    assert decision.basis == {"paragraph": 2}
    result = answer(decision, reviewer)
    assert result.verdict == "completed" and result.closed_reason == "resolved"
    assert result.resolved_by_id == reviewer.pk and result.resolved_at is not None
    assert result.resolution == {"action": "complete", "note": "Read"}
    assert result.revision == decision.revision + 1
    group.refresh_from_db()
    assert group.settled_at is not None and group.is_deletable
    resolved = Decision.objects.resolutions(group.pk, actor=issuer, actions=[Complete, Decline])
    assert len(resolved) == 1


def test_delegated_system_admission_requires_a_current_domain_actor(people, delegated_request):
    issuer, _reviewer, _outsider, _subject = people
    request = request_for(people, assignees=None, requester=None)
    with pytest.raises(PermissionDenied):
        Decision.objects.admit_group([request], actor=None)
    with pytest.raises(ValidationError, match="Delegated assignment"):
        Decision.objects.admit_group([request], actor=issuer)
    with system_context(reason="test.delegated_question"):
        with pytest.raises(ValidationError, match="current actor"):
            Decision.objects.admit_group([request], actor=None)
    admin = create_platform_admin("delegated-decision-admin")
    with system_context(reason="test.delegated_question"):
        with pytest.raises(ValidationError, match="current actor"):
            Decision.objects.admit_group([request], actor=None)
        group = Decision.objects.admit_group([delegated_request], actor=None)
        decision = seat(group)
        assert not decision.assignees.exists()
    assert group.issuer_id is None and decision.requester_id is None
    assert decision.revision == 1
    document = "query { decisions(where: {can_act: {_eq: true}}) { id } }"
    visible = result_data(execute_schema(addon_schema(decision_schema.schemas, "console"), document, user=issuer))
    assert str(decision.sqid) in {row["id"] for row in visible["decisions"]}
    assert answer(decision, admin).resolved_by_id == admin.pk


def test_reask_preserves_a_system_delegated_seat(people, delegated_request):
    issuer, _reviewer, _outsider, _subject = people
    with system_context(reason="test.delegated_reask"):
        first = Decision.objects.admit_group([delegated_request], actor=None)
    answer(seat(first), issuer, action="decline", values={"reason": "Try again"})
    with system_context(reason="test.delegated_reask"):
        second = Decision.objects.reask(first.pk, actor=None, actions=(Complete, Decline), errors={})
        assert not seat(second).assignees.exists()
    assert second.issuer_id is None and second.reasked_from_id == first.pk
    assert seat(first).superseded_by_id is None


@pytest.mark.parametrize("system_actor", [False, True])
def test_reask_requires_system_admission_for_a_delegated_seat(people, delegated_request, system_actor):
    admin = create_platform_admin("delegated-reask-admission-admin")
    with system_context(reason="test.delegated_reask_admission"):
        group = Decision.objects.admit_group([delegated_request], actor=None)
    answer(seat(group), admin, action="decline", values={"reason": "Try again"})
    error = PermissionDenied if system_actor else ValidationError
    with pytest.raises(error, match="system context" if system_actor else "system admission"):
        Decision.objects.reask(group.pk, actor=None if system_actor else admin,
                               actions=(Complete, Decline), errors={})
    assert system_queryset(DecisionGroup).count() == 2


def test_delegated_predicate_reuses_prefetched_assignment(delegated_request, django_assert_num_queries):
    with system_context(reason="test.delegated_predicate"):
        group = Decision.objects.admit_group([delegated_request], actor=None)
        decision = Decision.objects.select_related("group").prefetch_related("assignees").get(group=group)
        with django_assert_num_queries(0):
            assert decision.is_delegated
            assert tuple(decision.assignees.all()) == ()


def test_delegated_relation_choices_require_explicit_participants(people):
    _issuer, _reviewer, _outsider, subject = people
    request = request_for(people, assignees=None, requester=None, actions=(ChooseDocument,),
                          refine={"choose": {"document_id": {"options": [
                              {"value": str(subject.sqid), "label": "Document"},
                          ]}}})
    with system_context(reason="test.delegated_relation_choices"):
        with pytest.raises(ValidationError, match="Relation choices require explicit participants"):
            Decision.objects.admit_group([request], actor=None)
    assert not system_queryset(DecisionGroup).exists()


def test_non_admin_person_without_seat_cannot_read_or_decide(people):
    issuer, reviewer, outsider, _subject = people
    group = Decision.objects.admit_group([request_for(people)], actor=issuer)
    decision = seat(group)
    assert not outsider.is_superuser and not reviewer.is_superuser
    with actor_context(reviewer):
        assert Decision.objects.filter(pk=decision.pk).exists()
        assert DecisionGroup.objects.filter(pk=group.pk).exists()
    with actor_context(outsider):
        assert not Decision.objects.filter(pk=decision.pk).exists()
        assert not DecisionGroup.objects.filter(pk=group.pk).exists()
    with pytest.raises(PermissionDenied):
        answer(decision, outsider)
    assert seat(group).is_open


def test_explicit_requester_reads_the_requested_question_without_an_assignment(people):
    issuer, _reviewer, outsider, subject = people
    write_relationships([
        RelationshipTuple(resource=to_object_ref(subject), relation="viewer", subject=to_subject_ref(outsider)),
    ])
    group = Decision.objects.admit_group([request_for(people, requester=outsider)], actor=issuer)
    decision = seat(group)
    assert decision.requester_id == outsider.pk
    with actor_context(outsider):
        assert Decision.objects.filter(pk=decision.pk).exists()
        assert DecisionGroup.objects.filter(pk=group.pk).exists()
        assert not decision.with_actor(outsider).has_access("act")


@pytest.mark.parametrize("position", ["subject", "reference", "fact_subject", "fact_evidence", "picker"])
def test_explicit_requester_requires_standing_access_to_every_evidence_record(people, position):
    issuer, _reviewer, requester, subject = people
    assert not issuer.is_superuser and not requester.is_superuser
    ref = reference(subject)
    changes = {"requester": requester, "subject": None}
    if position == "subject":
        changes["subject"] = subject
    elif position == "picker":
        changes.update(actions=(ChooseDocument,), refine={"choose": {"document_id": {
            "options": [{"value": str(subject.sqid), "label": "Document"}],
        }}})
    else:
        changes["context"] = DecisionContext(
            references=(ref,) if position == "reference" else (),
            facts=() if position == "reference" else (
                DecisionFact(pointer="/note", label="Note", value="Retained", authority="source",
                             subject=ref if position == "fact_subject" else None,
                             evidence=(ref,) if position == "fact_evidence" else ()),
            ),
        )
    with pytest.raises(PermissionDenied, match="Every participant"):
        Decision.objects.admit_group([request_for(people, **changes)], actor=issuer)
    assert not system_queryset(DecisionGroup).exists()
    assert not system_queryset(Decision).exists()
    assert not system_queryset(DecisionEvidence).exists()
    write_relationships([
        RelationshipTuple(resource=to_object_ref(subject), relation="viewer", subject=to_subject_ref(requester)),
    ])
    group = Decision.objects.admit_group([request_for(people, **changes)], actor=issuer)
    with actor_context(requester):
        assert Decision.objects.filter(group=group).exists()
        assert DecisionGroup.objects.filter(pk=group.pk).exists()


def test_reask_rechecks_the_explicit_requesters_standing_evidence_access(people):
    issuer, reviewer, requester, subject = people
    grant = RelationshipTuple(resource=to_object_ref(subject), relation="viewer", subject=to_subject_ref(requester))
    write_relationships([grant])
    group = Decision.objects.admit_group([request_for(people, requester=requester)], actor=issuer)
    answer(seat(group), reviewer)
    delete_relationship(grant)
    with pytest.raises(PermissionDenied, match="Every participant"):
        Decision.objects.reask(group.pk, actor=issuer, actions=(Complete, Decline), errors={})
    assert system_queryset(DecisionGroup).count() == 1
    assert system_queryset(Decision).count() == 1


@pytest.mark.parametrize("model_name", ["group", "decision"])
def test_ordinary_users_cannot_bypass_admission_with_direct_inserts(people, model_name):
    issuer, _reviewer, _outsider, _subject = people
    group = Decision.objects.admit_group([request_for(people)], actor=issuer)
    with pytest.raises(PermissionDenied), actor_context(issuer):
        if model_name == "group":
            DecisionGroup.objects.create(issuer=issuer)
        else:
            Decision.objects.create(group=group, index=1, kind="direct_note", max_attempts=3,
                                    form_schema=seat(group).form_schema)
    assert system_queryset(DecisionGroup).count() == 1
    assert system_queryset(Decision).count() == 1


def test_requester_defaults_to_issuer_and_must_be_explicitly_opted_out(people):
    issuer, _reviewer, _outsider, _subject = people
    with pytest.raises(ValidationError, match="assignee who can act"):
        Decision.objects.admit_group([request_for(people, assignees=[issuer])], actor=issuer)
    assert not system_queryset(DecisionGroup).exists()
    other = Decision.objects.admit_group([request_for(people, assignees=[issuer], requester=None)], actor=issuer)
    assert seat(other).requester_id is None
    assert answer(seat(other), issuer).verdict == "completed"


@pytest.mark.parametrize("attribute,value", [("is_active", False), ("kind", "service")])
def test_only_active_people_can_decide_even_after_admission(people, attribute, value):
    issuer, reviewer, _outsider, _subject = people
    group = Decision.objects.admit_group([request_for(people)], actor=issuer)
    with system_context(reason="test.change_person_status"):
        type(reviewer).objects.filter(pk=reviewer.pk).update(**{attribute: value})
    reviewer.refresh_from_db()
    with pytest.raises(PermissionDenied):
        answer(seat(group), reviewer)
    assert seat(group).is_open


def test_administrator_exception_still_requires_an_active_person(people):
    issuer, _reviewer, _outsider, _subject = people
    admin = create_platform_admin("decision-admin")
    group = Decision.objects.admit_group([request_for(people)], actor=issuer)
    assert answer(seat(group), admin).resolved_by_id == admin.pk
    other = Decision.objects.admit_group([request_for(people)], actor=issuer)
    with system_context(reason="test.deactivate_admin"):
        type(admin).objects.filter(pk=admin.pk).update(is_active=False)
    admin.refresh_from_db()
    with pytest.raises(PermissionDenied):
        answer(seat(other), admin)


@pytest.mark.parametrize("policy", ["first", "all"])
def test_each_policy_settles_at_its_defined_boundary(people, policy):
    issuer, reviewer, _outsider, _subject = people
    group = Decision.objects.admit_group([request_for(people), request_for(people)], actor=issuer, policy=policy)
    answer(seat(group), reviewer)
    group.refresh_from_db()
    if policy == "first":
        assert group.settled_at is not None
        assert seat(group, 1).closed_reason == "sibling_settled"
        assert seat(group, 1).verdict == "pending"
    else:
        assert group.settled_at is None and seat(group, 1).is_open
        answer(seat(group, 1), reviewer, action="decline", values={"reason": "Needs revision"})
        group.refresh_from_db()
        assert group.settled_at is not None
        assert seat(group, 1).verdict == "rejected"


@pytest.mark.parametrize("reason", ["expired", "invalid_attempts"])
def test_all_policy_closes_siblings_when_a_seat_has_no_answer(people, reason):
    issuer, reviewer, _outsider, _subject = people
    changes = {"expires_at": timezone.now() + timedelta(minutes=5)} if reason == "expired" else {"max_attempts": 1}
    group = Decision.objects.admit_group([
        request_for(people, **changes), request_for(people),
    ], actor=issuer, policy="all")
    if reason == "expired":
        elapse_deadline(group)
        assert Decision.objects.expire_due() == 1
    else:
        with pytest.raises(ValidationError):
            answer(seat(group), reviewer, values={"note": ""})
    group.refresh_from_db()
    assert group.settled_at is not None
    assert seat(group).closed_reason == reason
    assert seat(group, 1).closed_reason == "sibling_settled"


def test_custom_policy_owns_unanswered_settlement(people, settings):
    """The model does not preempt a registered policy's closure decision."""
    issuer, _reviewer, _outsider, _subject = people
    settings.ANGEE_DECISION_POLICY_CLASSES = {
        **settings.ANGEE_DECISION_POLICY_CLASSES,
        "all": "tests.test_decisions_lifecycle.RetainClosed",
    }
    group = Decision.objects.admit_group([
        request_for(people, expires_at=timezone.now() + timedelta(minutes=5)), request_for(people),
    ], actor=issuer, policy="all")
    elapse_deadline(group)
    assert Decision.objects.expire_due() == 1
    group.refresh_from_db()
    assert group.settled_at is None
    assert seat(group).closed_reason == "expired" and seat(group, 1).is_open


def test_settlement_signal_waits_for_outer_commit_and_fires_once(people):
    issuer, reviewer, _outsider, _subject = people
    group = Decision.objects.admit_group([request_for(people)], actor=issuer)
    settled = []

    def received(sender, group, outcome, **kwargs):
        settled.append((group.pk, outcome))

    decision_group_settled.connect(received, weak=False)
    try:
        with transaction.atomic():
            answer(seat(group), reviewer)
            assert settled == []
        assert settled == [(group.pk, None)]
        Decision.objects.cancel_group(group.pk)
        assert settled == [(group.pk, None)]
    finally:
        decision_group_settled.disconnect(received)


def test_stale_revision_and_final_answer_are_immutable(people):
    issuer, reviewer, _outsider, _subject = people
    group = Decision.objects.admit_group([request_for(people)], actor=issuer)
    decision = seat(group)
    with pytest.raises(StaleRevisionError):
        answer(decision, reviewer, revision=decision.revision + 1)
    assert seat(group).is_open
    answered = answer(decision, reviewer)
    with pytest.raises(StaleRevisionError):
        answer(decision, reviewer, action="decline", values={"reason": "Changed mind"})
    Decision.objects.cancel_group(group.pk)
    retained = seat(group)
    assert retained.resolution == answered.resolution
    assert retained.closed_reason == "resolved" and retained.resolved_by_id == reviewer.pk


def test_invalid_values_persist_attempts_and_per_field_issues(people):
    issuer, reviewer, _outsider, _subject = people
    group = Decision.objects.admit_group([request_for(people, max_attempts=2)], actor=issuer)
    decision = seat(group)
    with pytest.raises(ValidationError) as error:
        answer(decision, reviewer, values={"note": ""})
    assert "note" in error.value.message_dict
    invalid = seat(group)
    assert invalid.invalid_attempts == 1 and invalid.is_open
    assert invalid.revision == decision.revision + 1
    with pytest.raises(ValidationError):
        answer(invalid, reviewer, values={"note": ""})
    closed = seat(group)
    assert closed.invalid_attempts == 2 and closed.closed_reason == "invalid_attempts"
    assert closed.verdict == "pending" and closed.resolved_by_id is None
    group.refresh_from_db()
    assert group.settled_at is not None


def test_unknown_action_is_rejected_by_the_frozen_form(people):
    issuer, reviewer, _outsider, _subject = people
    group = Decision.objects.admit_group([request_for(people)], actor=issuer)
    with pytest.raises(ValidationError) as error:
        answer(seat(group), reviewer, action="missing", values={})
    assert "action" in error.value.message_dict
    assert seat(group).is_open


def test_runtime_read_only_values_are_frozen_and_cannot_be_changed(people):
    issuer, reviewer, _outsider, _subject = people
    group = Decision.objects.admit_group([request_for(
        people, initial={"complete": {"note": "Retained note"}},
        refine={"complete": {"note": {"readOnly": True}}},
    )], actor=issuer)
    decision = seat(group)
    with pytest.raises(ValidationError) as error:
        answer(decision, reviewer, values={"note": "Changed note"})
    assert "note" in error.value.message_dict
    assert answer(seat(group), reviewer, values={"note": "Retained note"}).verdict == "completed"


def test_omitted_values_use_stored_initial_defaults_before_recording_the_answer(people):
    issuer, reviewer, _outsider, _subject = people
    group = Decision.objects.admit_group([
        request_for(people, initial={"complete": {"note": "Frozen initial"}}),
    ], actor=issuer)
    completed = answer(seat(group), reviewer, values={})
    assert completed.resolution == {"action": "complete", "note": "Frozen initial"}

    class CurrentComplete(Action, key="complete", label="Complete", verdict=Verdict.COMPLETED):
        note: str = "A different current default"

    resolved = Decision.objects.resolution(completed.pk, actor=issuer, actions=(CurrentComplete,))
    assert resolved.action.note == "Frozen initial"


def test_frozen_picker_candidates_must_be_readable_by_every_assignee(people):
    issuer, _reviewer, _outsider, _subject = people
    hidden = vault_for(issuer, name="Private candidate")
    with pytest.raises(PermissionDenied):
        Decision.objects.admit_group([request_for(
            people, actions=(ChooseDocument,), refine={"choose": {"document_id": {
                "options": [{"value": str(hidden.sqid), "label": "Candidate"}],
            }}},
        )], actor=issuer)
    assert not system_queryset(DecisionGroup).exists()


def test_picker_admission_requires_its_declared_permission_not_only_read(people):
    issuer, reviewer, _outsider, subject = people
    request = request_for(people, actions=(EditDocument,), refine={"edit": {"document_id": {
        "options": [{"value": str(subject.sqid), "label": "Document"}],
    }}})
    with pytest.raises(PermissionDenied):
        Decision.objects.admit_group([request], actor=issuer)
    write_relationships([
        RelationshipTuple(resource=to_object_ref(subject), relation="editor", subject=to_subject_ref(reviewer)),
    ])
    group = Decision.objects.admit_group([request], actor=issuer)
    assert seat(group).is_open


def test_selected_relation_permission_is_rechecked_at_deciding(people):
    issuer, reviewer, _outsider, subject = people
    group = Decision.objects.admit_group([request_for(
        people, actions=(ChooseDocument,), refine={"choose": {"document_id": {
            "options": [{"value": str(subject.sqid), "label": "Document"}],
        }}},
    )], actor=issuer)
    delete_relationship(RelationshipTuple(
        resource=to_object_ref(subject), relation="viewer", subject=to_subject_ref(reviewer),
    ))
    with pytest.raises(ValidationError) as error:
        answer(seat(group), reviewer, action="choose", values={"document_id": str(subject.sqid)})
    assert "document_id" in error.value.message_dict
    assert seat(group).is_open


def test_expiry_closes_due_seats_and_settles_group(people):
    issuer, reviewer, _outsider, _subject = people
    group = Decision.objects.admit_group([
        request_for(people, expires_at=timezone.now() + timedelta(minutes=5)),
    ], actor=issuer)
    elapse_deadline(group)
    assert Decision.objects.expire_due() == 1
    assert Decision.objects.expire_due() == 0
    closed = seat(group)
    assert closed.closed_reason == "expired" and not closed.is_open
    assert closed.resolved_by_id is None and closed.verdict == "pending"
    group.refresh_from_db()
    assert group.settled_at is not None
    with pytest.raises(ValidationError):
        answer(closed, reviewer)


def test_supersession_closes_only_matching_open_kind_and_subject(people):
    issuer, _reviewer, _outsider, _subject = people
    old = Decision.objects.admit_group([request_for(people)], actor=issuer)
    other_kind = Decision.objects.admit_group([request_for(people, kind="note_check")], actor=issuer)
    new = Decision.objects.admit_group([request_for(people, supersede=True)], actor=issuer)
    replaced = seat(old)
    assert replaced.closed_reason == "superseded" and replaced.superseded_by_id == seat(new).pk
    old.refresh_from_db()
    assert old.settled_at is not None
    assert seat(new).is_open and seat(other_kind).is_open


def test_supersession_settles_an_all_group_with_other_open_seats(people):
    issuer, _reviewer, _outsider, _subject = people
    old = Decision.objects.admit_group([
        request_for(people), request_for(people, kind="other_note"),
    ], actor=issuer, policy="all")
    new = Decision.objects.admit_group([request_for(people, supersede=True)], actor=issuer)
    old.refresh_from_db()
    assert old.settled_at is not None
    assert seat(old).closed_reason == "superseded"
    assert seat(old).superseded_by_id == seat(new).pk
    assert seat(old, 1).closed_reason == "sibling_settled"


def test_mixed_supersession_cannot_admit_duplicate_seats_for_one_question(people):
    issuer, _reviewer, _outsider, _subject = people
    with pytest.raises(ValidationError):
        Decision.objects.admit_group([
            request_for(people), request_for(people, supersede=True),
        ], actor=issuer)
    assert not system_queryset(DecisionGroup).exists()
    assert not system_queryset(Decision).exists()


def test_cancel_closes_open_seats_and_is_idempotent(people):
    issuer, reviewer, _outsider, _subject = people
    group = Decision.objects.admit_group([request_for(people), request_for(people)], actor=issuer, policy="all")
    answer(seat(group), reviewer)
    Decision.objects.cancel_group(group.pk)
    group.refresh_from_db()
    assert group.settled_at is not None
    assert seat(group).closed_reason == "resolved"
    closed = seat(group, 1)
    assert closed.closed_reason == "canceled" and closed.resolved_by_id is None
    Decision.objects.cancel_group(group.pk)
    assert seat(group, 1).revision == closed.revision


def test_settled_resolution_rechecks_current_authority(people):
    issuer, reviewer, _outsider, _subject = people
    group = Decision.objects.admit_group([request_for(people)], actor=issuer)
    answer(seat(group), reviewer)
    with system_context(reason="test.revoke_seat"):
        seat(group).assignees.remove(reviewer)
    with pytest.raises(PermissionDenied):
        Decision.objects.resolutions(group.pk, actor=issuer, actions=[Complete, Decline])
    assert seat(group).closed_reason == "resolved"


def test_unsettled_group_cannot_supply_resolutions_or_be_deleted(people):
    issuer, _reviewer, _outsider, _subject = people
    group = Decision.objects.admit_group([request_for(people)], actor=issuer)
    assert not group.is_deletable
    with pytest.raises(ValidationError):
        Decision.objects.resolutions(group.pk, actor=issuer, actions=[Complete, Decline])


@pytest.mark.parametrize("hidden_from", ["issuer", "reviewer"])
def test_admission_requires_subject_readability_for_every_participant(people, hidden_from):
    issuer, _reviewer, outsider, _subject = people
    hidden = vault_for(outsider, name="Private document") if hidden_from == "issuer" else vault_for(issuer)
    with pytest.raises(PermissionDenied):
        Decision.objects.admit_group([request_for(people, subject=hidden)], actor=issuer)
    assert not system_queryset(DecisionGroup).exists()
    assert not system_queryset(DecisionEvidence).exists()


def reference(record):
    """Use the public record identity accepted by the context contract."""

    return DecisionRecordReference(model=record._meta.label, id=str(record.sqid), label=str(record))


def test_context_projects_deduplicated_protected_evidence(people):
    issuer, reviewer, _outsider, subject = people
    ref = reference(subject)
    context = DecisionContext(
        references=(ref,),
        facts=(DecisionFact(pointer="/note", label="Document note", value="Read", subject=ref,
                            authority="source", evidence=(ref,)),),
    )
    group = Decision.objects.admit_group([request_for(people, context=context)], actor=issuer)
    decision = seat(group)
    assert system_queryset(DecisionEvidence).filter(decision=decision).count() == 1
    assert decision.context == context.model_dump(mode="json")
    with pytest.raises(ProtectedError), actor_context(issuer):
        subject.delete()
    answer(decision, reviewer)
    with pytest.raises(ProtectedError), actor_context(issuer):
        type(subject).objects.filter(pk=subject.pk).delete()


def test_unreferenced_subject_is_not_delete_protected(people):
    issuer, _reviewer, _outsider, subject = people
    group = Decision.objects.admit_group([request_for(people)], actor=issuer)
    assert not system_queryset(DecisionEvidence).filter(decision__group=group).exists()
    with actor_context(issuer):
        subject.delete()
    assert seat(group).is_open


@pytest.mark.parametrize("position", ["reference", "fact_subject", "fact_evidence"])
def test_every_context_reference_requires_standing_read_access(people, position):
    issuer, _reviewer, _outsider, _subject = people
    hidden = reference(vault_for(issuer, name="Restricted notes"))
    context = DecisionContext(
        references=(hidden,) if position == "reference" else (),
        facts=() if position == "reference" else (
            DecisionFact(pointer="/note", label="Note", value="Retained", authority="source",
                         subject=hidden if position == "fact_subject" else None,
                         evidence=(hidden,) if position == "fact_evidence" else ()),
        ),
    )
    with pytest.raises(PermissionDenied):
        Decision.objects.admit_group([request_for(people, context=context)], actor=issuer)
    assert not system_queryset(DecisionGroup).exists()


def test_multi_seat_admission_rolls_back_if_any_seat_is_unreadable(people):
    issuer, _reviewer, outsider, _subject = people
    hidden = vault_for(outsider, name="Hidden document")
    with pytest.raises(PermissionDenied):
        Decision.objects.admit_group([request_for(people), request_for(people, subject=hidden)], actor=issuer)
    assert not system_queryset(DecisionGroup).exists()
    assert not system_queryset(Decision).exists()


def test_settled_resolution_is_parsed_as_the_declared_action_model(people):
    issuer, reviewer, _outsider, _subject = people
    group = Decision.objects.admit_group([request_for(people)], actor=issuer)
    completed = answer(seat(group), reviewer)
    resolved = Decision.objects.resolution(completed.pk, actor=issuer, actions=[Complete, Decline])
    assert isinstance(resolved.action, Complete)
    assert resolved.action.note == "Read" and resolved.resolver.pk == reviewer.pk


@pytest.mark.parametrize("plural", [False, True])
@pytest.mark.parametrize("requester", ["missing", "inactive"])
def test_answer_consumption_rejects_missing_or_inactive_requester_before_elevation(people, plural, requester):
    """The lock owner's system scope never supplies a missing requester's authority."""
    issuer, reviewer, _outsider, _subject = people
    group = Decision.objects.admit_group([request_for(people)], actor=issuer)
    completed = answer(seat(group), reviewer)
    if requester == "inactive":
        with system_context(reason="test inactive answer reader"):
            type(issuer).objects.filter(pk=issuer.pk).update(is_active=False)
    operation = Decision.objects.resolutions if plural else Decision.objects.resolution
    with pytest.raises(NoActorResolvedError if requester == "missing" else PermissionDenied):
        operation(
            group.pk if plural else completed.pk,
            actor=None if requester == "missing" else issuer,
            actions=[Complete, Decline],
        )


def test_answer_consumption_resolves_the_existing_ambient_requester(people):
    """Consuming one answer or a group uses the same ambient identity owner as deciding."""
    issuer, reviewer, _outsider, _subject = people
    group = Decision.objects.admit_group([request_for(people)], actor=issuer)
    completed = answer(seat(group), reviewer)
    with actor_context(issuer):
        singular = Decision.objects.resolution(completed.pk, actor=None, actions=[Complete, Decline])
        plural = Decision.objects.resolutions(group.pk, actor=None, actions=[Complete, Decline])
    assert singular.decision.pk == completed.pk == plural[0].decision.pk


def test_singular_resolution_does_not_require_access_to_private_sibling(people):
    issuer, reviewer, outsider, _subject = people
    group = Decision.objects.admit_group([
        request_for(people), request_for(people, subject=None, assignees=(outsider,)),
    ], actor=issuer, policy="all")
    own = answer(seat(group), reviewer)
    answer(seat(group, 1), outsider)
    resolved = Decision.objects.resolution(own.pk, actor=reviewer, actions=(Complete, Decline))
    assert resolved.decision.pk == own.pk and resolved.resolver.pk == reviewer.pk
    with pytest.raises(PermissionDenied):
        Decision.objects.resolutions(group.pk, actor=reviewer, actions=(Complete, Decline))


def test_reask_cannot_copy_a_private_sibling_for_a_group_reader(people):
    """Reading one seat's group does not authorize copying its private siblings."""
    issuer, reviewer, outsider, _subject = people
    group = Decision.objects.admit_group([
        request_for(people, subject=None),
        request_for(people, subject=None, assignees=(outsider,), basis={"note": "Private basis"}),
    ], actor=issuer)
    answer(seat(group), reviewer)
    with actor_context(reviewer):
        assert DecisionGroup.objects.filter(pk=group.pk).exists()
        assert not Decision.objects.filter(group=group, index=1).exists()
    with pytest.raises(PermissionDenied):
        Decision.objects.reask(group.pk, actor=reviewer, actions=(Complete, Decline), errors={})
    assert system_queryset(DecisionGroup).count() == 1
    assert system_queryset(Decision).count() == 2


@pytest.mark.parametrize("target_kind", ["group", "decision", "both"])
def test_deletion_predicate_includes_generic_evidence_retaining_a_group(people, target_kind):
    issuer, reviewer, _outsider, _subject = people
    group = Decision.objects.admit_group([request_for(people)], actor=issuer)
    completed = answer(seat(group), reviewer)
    group.refresh_from_db()
    assert group.is_deletable
    retained = (group, completed) if target_kind == "both" else (
        group if target_kind == "group" else completed,
    )
    Decision.objects.admit_group([request_for(
        people, subject=None, assignees=(issuer,), requester=None,
        context=DecisionContext(references=tuple(reference(record) for record in retained)),
    )], actor=issuer)
    assert not group.is_deletable
    with pytest.raises(ProtectedError), system_context(reason="test.delete_retained_group"):
        system_queryset(DecisionGroup).get(pk=group.pk).delete()


def test_database_rejects_inconsistent_resolution_columns(people):
    issuer, _reviewer, _outsider, _subject = people
    group = Decision.objects.admit_group([request_for(people)], actor=issuer)
    with pytest.raises(IntegrityError), transaction.atomic(), system_context(reason="test.invalid_resolution"):
        Decision.objects.filter(group=group).owner_update(verdict="completed", revision=F("revision") + 1)
    assert seat(group).is_open


def test_database_rejects_terminal_answer_with_null_closure_reason(people):
    issuer, reviewer, _outsider, _subject = people
    group = Decision.objects.admit_group([request_for(people)], actor=issuer)
    with pytest.raises(IntegrityError), transaction.atomic(), system_context(reason="test.null_closure"):
        Decision.objects.filter(group=group).owner_update(
            verdict="completed", resolved_by=reviewer, resolved_at=Now(), closed_reason=None,
        )
    assert seat(group).is_open


def test_generic_collection_and_instance_writes_cannot_change_frozen_answers(people):
    issuer, reviewer, _outsider, _subject = people
    group = Decision.objects.admit_group([request_for(people)], actor=issuer)
    completed = answer(seat(group), reviewer)
    with pytest.raises(ValidationError), system_context(reason="test.generic_answer_edit"):
        Decision.objects.filter(pk=completed.pk).update(resolution={"action": "decline"})
    completed.form_schema = {}
    with pytest.raises(ValidationError), system_context(reason="test.generic_question_edit"):
        completed.save(update_fields=("form_schema",))
    with pytest.raises(ValidationError):
        completed.delete()
    retained = seat(group)
    assert retained.resolution == {"action": "complete", "note": "Read"}
    assert retained.form_schema


def test_settled_groups_cannot_be_reopened_by_generic_writes(people):
    issuer, reviewer, _outsider, _subject = people
    group = Decision.objects.admit_group([request_for(people)], actor=issuer)
    answer(seat(group), reviewer)
    with pytest.raises(ValidationError), system_context(reason="test.reopen_group"):
        DecisionGroup.objects.filter(pk=group.pk).update(settled_at=None)
    group.settled_at = None
    with pytest.raises(ValidationError), system_context(reason="test.resave_group"):
        group.save(update_fields=("settled_at",))
    assert system_queryset(DecisionGroup).settled().filter(pk=group.pk).exists()


def test_evidence_projection_rejects_generic_edits(people):
    issuer, _reviewer, _outsider, subject = people
    group = Decision.objects.admit_group([
        request_for(people, context=DecisionContext(references=(reference(subject),))),
    ], actor=issuer)
    evidence = system_queryset(DecisionEvidence).get(decision__group=group)
    with pytest.raises(ValidationError), system_context(reason="test.edit_evidence"):
        DecisionEvidence.objects.filter(pk=evidence.pk).update(object_id=subject.pk + 1)
    evidence.object_id = subject.pk + 1
    with pytest.raises(ValidationError), system_context(reason="test.resave_evidence"):
        evidence.save(update_fields=("object_id",))
    with pytest.raises(ValidationError):
        evidence.delete()
    assert system_queryset(DecisionEvidence).get(pk=evidence.pk).object_id == subject.pk


def test_deleting_a_superseding_group_keeps_the_original_history(people):
    issuer, _reviewer, _outsider, _subject = people
    old = Decision.objects.admit_group([request_for(people)], actor=issuer)
    new = Decision.objects.admit_group([request_for(people, supersede=True)], actor=issuer)
    assert seat(old).superseded_by_id == seat(new).pk
    with pytest.raises(ValidationError), system_context(reason="test.erase_supersession"):
        Decision.objects.filter(group=old).update(superseded_by=None)
    Decision.objects.cancel_group(new.pk)
    with system_context(reason="test.delete_superseding_group"):
        system_queryset(DecisionGroup).get(pk=new.pk).delete()
    assert seat(old).superseded_by_id is None
    assert seat(old).closed_reason == "superseded"


@pytest.mark.parametrize("closure,outcome", [
    ("resolved", None), ("expired", "expired"), ("invalid_attempts", "invalid_attempts"),
    ("superseded", "superseded"), ("canceled", "canceled"),
])
def test_group_outcome_and_query_verbs_are_available_to_waiters(people, closure, outcome):
    issuer, reviewer, _outsider, _subject = people
    changes = {"expires_at": timezone.now() + timedelta(minutes=5)} if closure == "expired" else {}
    group = Decision.objects.admit_group([request_for(people, max_attempts=1, **changes)], actor=issuer)
    assert group.outcome is None
    assert not system_queryset(DecisionGroup).settled().filter(pk=group.pk).exists()
    assert not system_queryset(Decision).unanswered().filter(group=group).exists()
    if closure == "resolved":
        answer(seat(group), reviewer)
    elif closure == "expired":
        elapse_deadline(group)
        Decision.objects.expire_due()
    elif closure == "invalid_attempts":
        with pytest.raises(ValidationError):
            answer(seat(group), reviewer, values={"note": ""})
    elif closure == "superseded":
        Decision.objects.admit_group([request_for(people, supersede=True)], actor=issuer)
    else:
        Decision.objects.cancel_group(group.pk)
    group.refresh_from_db()
    assert group.outcome == outcome
    assert system_queryset(DecisionGroup).settled().filter(pk=group.pk).exists()
    assert system_queryset(Decision).unanswered().filter(group=group).exists() is (closure != "resolved")


def test_settlement_publishes_the_group_once(people, monkeypatch):
    issuer, reviewer, _outsider, _subject = people
    group = Decision.objects.admit_group([request_for(people)], actor=issuer)
    published = []
    monkeypatch.setattr(
        decision_managers, "publish_change", lambda record, **kwargs: published.append((record, kwargs)),
    )
    answer(seat(group), reviewer)
    Decision.objects.cancel_group(group.pk)
    groups = [(record, options) for record, options in published if isinstance(record, DecisionGroup)]
    assert len(groups) == 1
    assert groups[0][0].settled_at is not None
    assert groups[0][1] == {"action": "update", "update_fields": None}


@pytest.mark.parametrize("verb", ["decide", "resolution", "resolutions", "cancel_group"])
def test_missing_rows_raise_a_defined_manager_error(people, verb):
    issuer, reviewer, _outsider, _subject = people
    with pytest.raises(ValidationError, match="no longer exists"):
        if verb == "decide":
            Decision.objects.decide(-1, actor=reviewer, revision=0, action="complete", values={"note": "Read"})
        elif verb == "resolution":
            Decision.objects.resolution(-1, actor=issuer, actions=(Complete,))
        elif verb == "resolutions":
            Decision.objects.resolutions(-1, actor=issuer, actions=(Complete,))
        else:
            Decision.objects.cancel_group(-1)


def test_graphql_inbox_hides_foreign_seats_and_dispatches_the_deciding_action(people):
    issuer, reviewer, outsider, _subject = people
    group = Decision.objects.admit_group([request_for(people)], actor=issuer)
    decision = seat(group)
    schema = addon_schema(decision_schema.schemas, "console")
    query = "query { decisions { id kind revision } decision_groups { id } }"
    visible = result_data(execute_schema(schema, query, user=reviewer))
    assert visible["decisions"] == [{"id": str(decision.sqid), "kind": decision.kind, "revision": decision.revision}]
    assert visible["decision_groups"] == [{"id": str(group.sqid)}]
    assert result_data(execute_schema(schema, query, user=outsider)) == {"decisions": [], "decision_groups": []}
    mutation = """
      mutation Decide($id: ID!, $revision: Int!, $values: JSON!) {
        decide(id: $id, revision: $revision, action: "complete", values: $values) { ok message }
      }
    """
    variables = {"id": str(decision.sqid), "revision": decision.revision, "values": {"note": "Read"}}
    denied = result_data(execute_schema(schema, mutation, variables, user=outsider))
    assert denied["decide"]["ok"] is False and seat(group).is_open
    accepted = result_data(execute_schema(schema, mutation, variables, user=reviewer))
    assert accepted["decide"]["ok"] is True
    assert seat(group).resolved_by_id == reviewer.pk
    mutation_fields = schema._schema.mutation_type.fields
    assert not any(name.startswith(("insert_", "update_", "delete_")) for name in mutation_fields)


def test_graphql_decide_uses_the_decision_instance_dispatch(people, monkeypatch):
    issuer, reviewer, _outsider, _subject = people
    group = Decision.objects.admit_group([request_for(people)], actor=issuer)
    decision = seat(group)
    called = []
    original = Decision.decide

    def dispatch(self, **kwargs):
        called.append(self.pk)
        return original(self, **kwargs)

    monkeypatch.setattr(Decision, "decide", dispatch)
    schema = addon_schema(decision_schema.schemas, "console")
    mutation = """mutation($id: ID!, $revision: Int!, $values: JSON!) {
      decide(id: $id, revision: $revision, action: "complete", values: $values) { ok }
    }"""
    result = result_data(execute_schema(schema, mutation, {"id": decision.sqid,
                                                          "revision": decision.revision,
                                                          "values": {"note": "Read"}}, user=reviewer))
    assert result == {"decide": {"ok": True}}
    assert called == [decision.pk]


def test_graphql_stale_decision_preserves_the_native_conflict_code(people):
    issuer, reviewer, _outsider, _subject = people
    group = Decision.objects.admit_group([request_for(people)], actor=issuer)
    decision = seat(group)
    answer(decision, reviewer)
    schema = addon_schema(decision_schema.schemas, "console")
    result = execute_schema(schema, """mutation($id: ID!, $revision: Int!) {
      decide(id: $id, revision: $revision, action: "complete", values: {note: "Again"}) { ok code }
    }""", {"id": str(decision.sqid), "revision": decision.revision}, user=reviewer)
    assert result.errors and len(result.errors) == 1
    assert result.errors[0].extensions == {"code": "STALE_REVISION", "current_revision": decision.revision + 1}
    assert result.errors[0].message == "STALE_REVISION"


def test_graphql_form_errors_preserve_authored_snake_case_field_names(people):
    issuer, reviewer, _outsider, _subject = people
    group = Decision.objects.admit_group([request_for(people, actions=(ChooseDocument,))], actor=issuer)
    decision = seat(group)
    schema = addon_schema(decision_schema.schemas, "console")
    mutation = """
      mutation Decide($id: ID!, $revision: Int!) {
        decide(id: $id, revision: $revision, action: "choose", values: {}) { ok validation_errors }
      }
    """
    result = result_data(execute_schema(
        schema, mutation, {"id": str(decision.sqid), "revision": decision.revision}, user=reviewer,
    ))["decide"]
    assert result["ok"] is False
    assert "document_id" in result["validation_errors"] and "documentId" not in result["validation_errors"]


def test_inbox_filters_assignees_separately_from_requesters(people):
    issuer, reviewer, _outsider, _subject = people
    write_relationships([
        RelationshipTuple(
            resource=to_object_ref(reviewer), relation="directory_reader", subject=to_subject_ref(issuer),
        ),
    ])
    assert reviewer.with_actor(issuer).has_access("read")
    assigned = Decision.objects.admit_group([request_for(people)], actor=issuer)
    requested = Decision.objects.admit_group([
        request_for(people, assignees=(issuer,), requester=reviewer),
    ], actor=issuer)
    schema = addon_schema(decision_schema.schemas, "console")
    query = """
      query Inbox($person: String!) {
        assigned: decisions(where: {assignees: {_eq: $person}}) { id }
        requested: decisions(where: {requester: {_eq: $person}}) { id }
      }
    """
    result = result_data(execute_schema(schema, query, {"person": str(reviewer.sqid)}, user=issuer))
    assert result == {
        "assigned": [{"id": str(seat(assigned).sqid)}],
        "requested": [{"id": str(seat(requested).sqid)}],
    }


def test_reask_retains_every_round_through_protected_group_links(people):
    """Retention traverses the complete decisions-owned chain after any re-ask."""
    issuer, reviewer, _outsider, _subject = people
    original = Decision.objects.admit_group([request_for(people)], actor=issuer)
    rounds = [original]
    for _ in range(2):
        answer(seat(rounds[-1]), reviewer)
        rounds.append(Decision.objects.reask(
            rounds[-1].pk, actor=issuer, actions=(Complete, Decline), errors={"note": ["Read again"]},
        ))
    answer(seat(rounds[-1]), reviewer)
    assert [group.pk for group in rounds[-1].rounds()] == [group.pk for group in reversed(rounds)]
    for current, previous in zip(rounds[1:], rounds):
        assert current.reasked_from_id == previous.pk
        previous.refresh_from_db()
        assert not previous.is_deletable
        with pytest.raises(ProtectedError), system_context(reason="test.delete_retained_round"):
            previous.delete()
    rounds[-1].refresh_from_db()
    assert rounds[-1].is_deletable
    assert system_queryset(DecisionGroup).count() == 3
    assert system_queryset(Decision).filter(closed_reason="resolved").count() == 3


def test_reask_renews_original_duration_from_the_database_clock(people):
    """An elapsed old deadline never immediately expires a freshly admitted round."""
    issuer, reviewer, _outsider, _subject = people
    group = Decision.objects.admit_group([
        request_for(people, expires_at=timezone.now() + timedelta(minutes=1)),
    ], actor=issuer)
    answer(seat(group), reviewer)
    with system_context(reason="test.age_retained_decision"):
        Decision.objects.filter(group=group).owner_update(
            created_at=Now() - timedelta(minutes=5), expires_at=Now() - timedelta(minutes=4),
        )
    repeated = Decision.objects.reask(group.pk, actor=issuer, actions=(Complete, Decline), errors={})
    decision = seat(repeated)
    assert decision.is_open
    assert abs((decision.expires_at - decision.created_at).total_seconds() - 60) < 1
    assert repeated.reasked_from_id == group.pk


def test_admission_rejects_a_deadline_that_is_already_past_atomically(people):
    issuer, _reviewer, _outsider, _subject = people
    with pytest.raises(ValidationError, match="deadline must be in the future"):
        Decision.objects.admit_group([
            request_for(people), request_for(people, expires_at=timezone.now() - timedelta(seconds=1)),
        ], actor=issuer, policy="all")
    assert not system_queryset(DecisionGroup).exists()
    assert not system_queryset(Decision).exists()


@pytest.mark.parametrize("ineligible", ["inactive", "service"])
def test_admission_rejects_a_seat_without_an_eligible_assignee(people, ineligible):
    issuer, reviewer, _outsider, _subject = people
    with system_context(reason="test.ineligible_assignee"):
        type(reviewer).objects.filter(pk=reviewer.pk).update(
            **({"is_active": False} if ineligible == "inactive" else {"kind": "service"}),
        )
    with pytest.raises(ValidationError, match="assignee who can act"):
        Decision.objects.admit_group([request_for(people, subject=None)], actor=issuer)
    assert not system_queryset(DecisionGroup).exists()


def test_admission_preserves_inactive_assignees_when_another_can_act(people):
    issuer, reviewer, outsider, _subject = people
    with system_context(reason="test.inactive_coassignee"):
        type(outsider).objects.filter(pk=outsider.pk).update(is_active=False)
    group = Decision.objects.admit_group([
        request_for(people, subject=None, assignees=(reviewer, outsider)),
    ], actor=issuer)
    with system_context(reason="test.retained_assignees"):
        assert set(seat(group).assignees.values_list("pk", flat=True)) == {reviewer.pk, outsider.pk}
    assert answer(seat(group), reviewer).resolved_by_id == reviewer.pk
