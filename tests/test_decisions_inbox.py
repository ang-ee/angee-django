"""Public inbox reads, filters, and the one verdict mutation."""

from types import SimpleNamespace

import pytest
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import RelationshipTuple, to_object_ref, to_subject_ref, write_relationships
from rebac.backends.local import LocalBackend

from angee.decisions import schema as decision_schema
from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.decisions.testing.models import Decision
from angee.iam.graphql.actions import authorized_permission_target
from angee.iam.permissions import PermissionProof, prove_target
from tests.conftest import addon_schema, create_user, execute_schema, result_data, vault_for


@pytest.fixture
def inbox(composed_tables):
    requester, reviewer, outsider = (
        create_user(name) for name in ("inbox-requester", "inbox-reviewer", "inbox-outsider")
    )
    subject = vault_for(requester)
    write_relationships([RelationshipTuple(to_object_ref(subject), "viewer", to_subject_ref(reviewer))])
    decision = Decision.objects.ask(
        DecisionRequest(
            kind="review_note",
            records=(subject,),
            assignees=(reviewer,),
            requester=requester,
            proposal=DecisionProposal(
                alternatives=[
                    {"key": "accept", "label": "Accept", "outcome": "accepted"},
                    {"key": "reject", "label": "Keep current record", "outcome": "rejected"},
                ]
            ),
        ),
        actor=requester,
    )
    return requester, reviewer, outsider, subject, decision


@pytest.mark.parametrize("viewer", [0, 1, 2])
def test_inbox_scope_and_permission_field(inbox, viewer):
    requester, reviewer, outsider, subject, decision = inbox
    schema = addon_schema(decision_schema.schemas, "console")
    data = result_data(
        execute_schema(
            schema, "{ decisions { id is_open verdict permissions records { record_id } } }", user=inbox[viewer]
        )
    )
    if viewer == 2:
        assert data == {"decisions": []}
    else:
        row = data["decisions"][0]
        assert row["id"] == decision.sqid and row["is_open"] and row["verdict"] is None
        assert ("act" in row["permissions"]) is (viewer == 1)
        assert row["records"] == [{"record_id": subject.sqid}]


def test_public_mutation_records_only_chosen_keys_and_audit(inbox):
    requester, reviewer, _, _, decision = inbox
    schema = addon_schema(decision_schema.schemas, "console")
    mutation = """mutation($id: ID!, $revision: Int!) {
      decide(id: $id, revision: $revision, chosen: ["accept"]) { __typename }
    }"""
    result = execute_schema(schema, mutation, {"id": decision.sqid, "revision": decision.revision}, user=reviewer)
    assert not result.errors
    decision.refresh_from_db()
    assert decision.verdict == ["accept"] and decision.answered_by_id == reviewer.pk
    result = result_data(
        execute_schema(schema, "{ decisions { is_open verdict answered_at answered_by { id } } }", user=reviewer)
    )
    row = result["decisions"][0]
    assert row["is_open"] is False and row["verdict"] == ["accept"] and row["answered_at"]


def test_public_decide_proves_act_once(inbox, monkeypatch):
    _, reviewer, _, _, decision = inbox
    schema = addon_schema(decision_schema.schemas, "console")
    scope = LocalBackend.queryset_filter
    point = LocalBackend.check_access
    validate_verdict = Decision.validate_verdict
    proofs = []
    bound_actors = []

    def scoped(self, **kwargs):
        if kwargs["action"] == "act":
            proofs.append("scope")
        return scope(self, **kwargs)

    def checked(self, **kwargs):
        if kwargs["action"] == "act":
            proofs.append("point")
        return point(self, **kwargs)

    def validated(self, chosen, *, actor):
        bound_actors.append(self.actor())
        return validate_verdict(self, chosen, actor=actor)

    monkeypatch.setattr(LocalBackend, "queryset_filter", scoped)
    monkeypatch.setattr(LocalBackend, "check_access", checked)
    monkeypatch.setattr(Decision, "validate_verdict", validated)
    result = result_data(
        execute_schema(
            schema,
            """mutation($id: ID!, $revision: Int!) {
              decide(id: $id, revision: $revision, chosen: ["accept"]) { ok }
            }""",
            {"id": decision.sqid, "revision": decision.revision},
            user=reviewer,
        )
    )
    assert result["decide"]["ok"]
    assert proofs == ["scope"]
    assert bound_actors == [to_subject_ref(reviewer)]


def test_decide_rejects_unissued_and_mismatched_proofs(inbox):
    requester, reviewer, outsider, subject, first = inbox
    second = Decision.objects.ask(
        DecisionRequest(
            kind="review_note",
            records=(subject,),
            assignees=(reviewer,),
            requester=requester,
            proposal=DecisionProposal.model_validate(first.proposal),
        ),
        actor=requester,
    )
    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=reviewer)))
    target, proof = authorized_permission_target(info, Decision, first.sqid, "act", with_proof=True)
    assert prove_target(Decision, first.sqid, requester, "act") == (None, None)

    with pytest.raises(PermissionDenied):
        PermissionProof(target, to_subject_ref(outsider), "act")
    forged = object.__new__(PermissionProof)
    object.__setattr__(forged, "target", target)
    object.__setattr__(forged, "actor", to_subject_ref(outsider))
    object.__setattr__(forged, "permission", "act")
    with pytest.raises(PermissionDenied):
        target.decide(actor=outsider, chosen=["accept"], act_proof=forged)

    with pytest.raises(PermissionDenied):
        second.decide(actor=reviewer, chosen=["accept"], act_proof=proof)
    with pytest.raises(PermissionDenied):
        target.decide(actor=outsider, chosen=["accept"], act_proof=proof)
    read_target, read_proof = authorized_permission_target(info, Decision, first.sqid, "read", with_proof=True)
    with pytest.raises(PermissionDenied):
        read_target.decide(actor=reviewer, chosen=["accept"], act_proof=read_proof)

    original_pk = target.pk
    target.pk = second.pk
    try:
        with pytest.raises(PermissionDenied):
            target.decide(actor=reviewer, chosen=["accept"], act_proof=proof)
    finally:
        target.pk = original_pk

    original_db = target._state.db
    target._state.db = "other"
    assert not proof.matches(target, reviewer, "act")
    target._state.db = None
    try:
        with pytest.raises(PermissionDenied):
            target.decide(actor=reviewer, chosen=["accept"], act_proof=proof)
    finally:
        target._state.db = original_db

    assert Decision.system_queryset().filter(pk__in=(first.pk, second.pk), verdict__isnull=True).count() == 2


def test_decide_returns_one_optimized_reader_projection(inbox):
    requester, reviewer, _, subject, first = inbox
    second = Decision.objects.ask(DecisionRequest(
        kind="review_note", records=(subject,), assignees=(reviewer,), requester=requester,
        proposal=DecisionProposal.model_validate(first.proposal),
    ), actor=requester)
    schema = addon_schema(decision_schema.schemas, "console")
    brief = "id revision"
    full = """id kind kind_label revision created_at is_open context proposal
      verdict verdict_values verdict_label answered_at"""

    def answer(question, fields):
        query = f"""mutation($id: ID!, $revision: Int!) {{
          decide(id: $id, revision: $revision, chosen: ["accept"]) {{
            ok decision {{ {fields} }}
          }}
        }}"""
        with CaptureQueriesContext(connection) as statements:
            result = result_data(execute_schema(schema, query, {
                "id": question.sqid, "revision": question.revision,
            }, user=reviewer))["decide"]
        assert result["ok"] and result["decision"]["revision"] == question.revision + 1
        return len(statements), result["decision"]

    brief_count, _ = answer(first, brief)
    full_count, answered = answer(second, full)
    assert answered["id"] == second.sqid and answered["verdict"] == ["accept"]
    # The selected scalar fields share the actor-scoped decision read.
    assert full_count <= brief_count + 1, (brief_count, full_count)


def test_record_answer_history_returns_only_answered_decisions_for_the_readable_record(inbox):
    requester, reviewer, _, subject, answered = inbox
    answered.decide(actor=reviewer, chosen=["accept"])
    open_question = Decision.objects.ask(
        DecisionRequest(
            kind="second_review",
            records=(subject,),
            assignees=(reviewer,),
            requester=requester,
            proposal=DecisionProposal(alternatives=[
                {"key": "keep", "label": "Keep", "outcome": "accepted"},
                {"key": "change", "label": "Change", "outcome": "rejected"},
            ]),
        ),
        actor=requester,
    )
    schema = addon_schema(decision_schema.schemas, "console")

    data = result_data(execute_schema(
        schema,
        """query($model: String!, $id: ID!) {
          record_decision_answers(model: $model, id: $id) {
            id is_open verdict_label concerns_label answered_by { id display_name }
          }
        }""",
        {"model": subject._meta.label, "id": subject.sqid},
        user=reviewer,
    ))

    assert data == {"record_decision_answers": [{
        "id": answered.sqid,
        "is_open": False,
        "verdict_label": "Accept",
        # The concerned record's own label, for tables that cannot follow the records relation.
        "concerns_label": str(subject),
        "answered_by": {"id": reviewer.sqid, "display_name": reviewer.username},
    }]}
    assert open_question.is_open


def test_native_can_act_and_open_filters(inbox):
    _, reviewer, _, _, decision = inbox
    schema = addon_schema(decision_schema.schemas, "console")
    result = result_data(
        execute_schema(
            schema,
            """{
      decisions(where: {can_act: {_eq: true}, is_open: {_eq: true}}) { id }
    }""",
            user=reviewer,
        )
    )
    assert result == {"decisions": [{"id": decision.sqid}]}
