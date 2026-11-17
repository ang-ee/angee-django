"""An answer given for a represented record keeps its answerer's authority and records its evidence."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from rebac import RelationshipTuple, system_context, to_object_ref, to_subject_ref, write_relationships

from angee.decisions import schema as decision_schema
from angee.decisions.contracts import DecisionProposal, DecisionRecordReference, DecisionRequest
from angee.decisions.testing.models import Decision
from angee.graphql.testing.documents import execute_schema, result_data
from tests.conftest import addon_schema, create_user, vault_for


@pytest.fixture
def answering(db):
    """A question about a note, a represented record with no account, and one shared evidence record."""
    requester, reviewer, outsider = (create_user(name) for name in ("ob-requester", "ob-reviewer", "ob-outsider"))
    subject = vault_for(requester, name="Delivery terms")
    represented = vault_for(requester, name="Harbour Lane Bakery")
    evidence = vault_for(requester, name="Call notes")
    write_relationships(
        [
            RelationshipTuple(to_object_ref(record), "viewer", to_subject_ref(reviewer))
            for record in (subject, represented, evidence)
        ]
    )
    question = Decision.objects.ask(
        DecisionRequest(
            kind="terms_review",
            records=(subject,),
            assignees=(reviewer,),
            requester=requester,
            proposal=DecisionProposal(
                alternatives=[
                    {"key": "accept", "label": "Accept", "outcome": "accepted"},
                    {"key": "decline", "label": "Decline", "outcome": "declined"},
                ]
            ),
        ),
        actor=requester,
    )
    return requester, reviewer, outsider, represented, evidence, question


def test_an_answer_for_a_represented_record_keeps_the_answerer_and_records_its_evidence(answering):
    requester, reviewer, _outsider, represented, evidence, question = answering
    answered = question.decide(
        actor=reviewer,
        chosen=["accept"],
        revision=question.revision,
        on_behalf_of=DecisionRecordReference.of(represented).model_copy(update={"label": "Harbour Lane Bakery"}),
        evidence=(DecisionRecordReference.of(evidence),),
        note="Confirmed by phone.",
    )
    assert answered.answered_by_id == reviewer.pk and answered.verdict == ["accept"]
    assert answered.answered_for == {
        "model": represented._meta.label,
        "id": represented.sqid,
        "pointer": "",
        "label": "Harbour Lane Bakery",
        "tab": None,
        "page": None,
        "search": {},
    }
    assert answered.verdict_context["reason"] == "Confirmed by phone."
    assert [(ref["model"], ref["id"]) for ref in answered.verdict_context["references"]] == [
        (evidence._meta.label, evidence.sqid),
    ]


def test_an_answer_without_a_represented_record_or_evidence_stores_neither(answering):
    _requester, reviewer, *_rest, question = answering
    answered = question.decide(actor=reviewer, chosen=["decline"])
    assert answered.answered_for is None and answered.verdict_context is None


@pytest.mark.parametrize("seat", ["requester", "outsider"])
def test_naming_a_represented_record_grants_no_answer(answering, seat):
    requester, _reviewer, outsider, represented, _evidence, question = answering
    with pytest.raises(PermissionDenied):
        question.decide(
            actor={"requester": requester, "outsider": outsider}[seat],
            chosen=["accept"],
            on_behalf_of=DecisionRecordReference.of(represented),
        )
    question.refresh_from_db()
    assert question.is_open and question.revision == 1


def test_evidence_a_participant_cannot_read_refuses_the_whole_answer(answering):
    _requester, reviewer, *_rest, question = answering
    private = vault_for(reviewer, name="Reviewer scratchpad")
    with pytest.raises(PermissionDenied, match="Every participant"):
        question.decide(actor=reviewer, chosen=["accept"], evidence=(DecisionRecordReference.of(private),))
    question.refresh_from_db()
    assert question.is_open and question.answered_for is None and question.verdict_context is None


def test_a_malformed_reference_is_a_validation_error(answering):
    _requester, reviewer, *_rest, question = answering
    with pytest.raises(ValidationError, match="evidence"):
        question.decide(actor=reviewer, chosen=["accept"], on_behalf_of={"model": "tests.vault"})
    question.refresh_from_db()
    assert question.is_open


@pytest.mark.parametrize("withdrawn", [False, True])
def test_open_and_withdrawn_questions_carry_no_represented_record(answering, withdrawn):
    requester, *_rest, represented, _evidence, question = answering
    if withdrawn:
        with system_context(reason="tests.decisions.withdraw"):
            question = question.withdraw(actor=requester)
    with pytest.raises(IntegrityError), transaction.atomic():
        Decision.system_queryset().filter(pk=question.pk).owner_update(
            answered_for=DecisionRecordReference.of(represented).model_dump(mode="json"),
        )
    if not withdrawn:
        with pytest.raises(IntegrityError), transaction.atomic():
            Decision.system_queryset().filter(pk=question.pk).owner_update(verdict_context={"reason": "Early."})


def test_the_decide_mutation_records_and_projects_the_represented_answer(answering):
    _requester, reviewer, _outsider, represented, evidence, question = answering
    schema = addon_schema(decision_schema.schemas, "console")
    mutation = """mutation($id: ID!, $revision: Int!, $for: JSON, $evidence: JSON) {
      decide(id: $id, revision: $revision, chosen: ["accept"], on_behalf_of: $for, evidence: $evidence,
             note: "Read back to them.") { ok }
    }"""
    variables = {
        "id": question.sqid,
        "revision": question.revision,
        "for": {"model": represented._meta.label, "id": represented.sqid},
        "evidence": [{"model": evidence._meta.label, "id": evidence.sqid, "label": "Call notes"}],
    }
    assert result_data(execute_schema(schema, mutation, variables, user=reviewer))["decide"]["ok"]
    (row,) = result_data(
        execute_schema(
            schema,
            "{ decisions { answered_by { id } answered_for verdict_context } }",
            user=reviewer,
        )
    )["decisions"]
    assert row["answered_for"]["id"] == represented.sqid
    assert row["verdict_context"]["reason"] == "Read back to them."
    assert row["verdict_context"]["references"][0]["label"] == "Call notes"
