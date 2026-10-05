"""The shared interpreter validates declarations and uses normal record owners."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from pydantic import ValidationError as ContractError
from rebac import actor_context

from angee.decisions import schema as decision_schema
from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.decisions.signals import decision_answered
from angee.decisions.testing.models import Decision
from angee.workflows.decision_steps import apply_proposals
from tests.conftest import addon_schema, create_platform_admin, create_user, execute_schema, result_data


@pytest.fixture
def targets(composed_tables, composed_permissions):
    from tests.scopedemo.models import ProposalTarget
    reviewer = create_user("proposal-reviewer")
    outsider = create_user("proposal-outsider")
    issuer = create_platform_admin("proposal-issuer")
    with actor_context(reviewer):
        target = ProposalTarget.objects.create(name="Before")
        related = ProposalTarget.objects.create(name="Related")
    with actor_context(outsider):
        hidden = ProposalTarget.objects.create(name="Hidden")
    return issuer, reviewer, target, related, hidden


def ask(targets, actions, *, multiple=False, second=None):
    issuer, reviewer, target, *_ = targets
    alternatives = [{"key": "apply", "label": "Apply", "outcome": "applied", "actions": {target.sqid: actions}}]
    if second is not None:
        alternatives.append({"key": "also", "label": "Also", "outcome": "applied", "actions": {target.sqid: second}})
    return Decision.objects.ask(DecisionRequest(
        kind="proposal", records=(target,), requester=None, assignees=(reviewer,),
        proposal=DecisionProposal(multiple=multiple, alternatives=alternatives),
    ), actor=issuer)


@pytest.mark.parametrize("null", [False, True])
def test_foreign_key_public_id_and_null_and_declared_method_without_updated_at(targets, null):
    _, reviewer, target, related, _ = targets
    decision = ask(targets, {"fields": {"parent": {"set": None if null else related.sqid}, "name": {"set": "After"}},
                             "record": {"call": "confirm"}})
    decision = Decision.objects.decide(decision, actor=reviewer, chosen=["apply"])
    assert apply_proposals(decision, actor=reviewer) == "applied"
    target.refresh_from_db()
    assert target.parent_id == (None if null else related.pk)
    assert target.name == "After" and target.confirmed


def test_unreadable_related_record_is_rejected_at_ask(targets):
    with pytest.raises(PermissionDenied, match="participant"):
        ask(targets, {"fields": {"parent": {"set": targets[4].sqid}}})


@pytest.mark.parametrize("actions", [
    {"fields": {"name": {"set": "x" * 81}}},
    {"fields": {"locked_value": {"set": "changed"}}},
    {"fields": {"proposaltarget": {"set": None}}},
    {"record": {"call": "save"}}, {"record": {"call": "delete"}},
    {"record": {"call": "requires_input"}},
])
def test_bad_fields_and_undeclared_or_required_argument_methods_are_rejected_at_ask(targets, actions):
    with pytest.raises(ContractError):
        ask(targets, actions)


@pytest.mark.parametrize("operation", [{"set": "After"}, {"choose": {}}])
def test_multiple_rejects_overlapping_field_changes_when_answering(targets, operation):
    actions = {"fields": {"name": operation}}
    decision = ask(targets, actions, multiple=True, second=actions)
    with pytest.raises(ValidationError, match="Choose only one value"):
        decision.decide(actor=targets[1], chosen=["apply", "also"])


def test_missing_action_link_raises_validation_error(targets):
    _, reviewer, *_ = targets
    decision = ask(targets, {"fields": {"name": {"set": "After"}}})
    decision = Decision.objects.decide(decision, actor=reviewer, chosen=["apply"])
    # A detached retained concern can no longer apply its proposed write.
    from rebac import system_context

    from angee.base.scoping import system_queryset
    with system_context(reason="test detached decision concern"):
        for link in system_queryset(decision.records.model).filter(decision=decision):
            link._owner_delete()
    with pytest.raises(ValidationError, match="missing"):
        apply_proposals(decision, actor=reviewer)


@pytest.mark.parametrize("values", [None, {}, {"SELF": {"name": "Chosen", "confirmed": True}},
                                    {"other": {}}, {"SELF": {"name": "x" * 81}}, []])
def test_choose_refuses_missing_extra_or_invalid_values_without_closing(targets, values):
    _, reviewer, target, *_ = targets
    decision = ask(targets, {"fields": {"name": {"choose": {}}}})
    if isinstance(values, dict):
        values = {target.sqid if identity == "SELF" else identity: fields for identity, fields in values.items()}
    with pytest.raises(ValidationError):
        decision.decide(actor=reviewer, chosen=["apply"], values=values)
    decision.refresh_from_db()
    assert decision.is_open and decision.verdict_values is None and decision.revision == 1


def test_choose_refuses_value_for_an_unchosen_or_set_field(targets):
    _, reviewer, target, *_ = targets
    decision = ask(targets, {"fields": {"name": {"set": "Proposed"}}},
                   second={"fields": {"parent": {"choose": {}}}})
    for values in ({target.sqid: {"name": "Chosen"}}, {target.sqid: {"parent": None}}):
        with pytest.raises(ValidationError, match="only for choose"):
            decision.decide(actor=reviewer, chosen=["apply"], values=values)


def test_choose_related_record_is_readable_under_answering_actor(targets):
    _, reviewer, target, related, hidden = targets
    decision = ask(targets, {"fields": {"parent": {"choose": {"filter": {"name": {"_neq": "Hidden"}}}}}})
    with pytest.raises(ValidationError, match="unreadable"):
        decision.decide(actor=reviewer, chosen=["apply"], values={target.sqid: {"parent": hidden.sqid}})
    answered = decision.decide(actor=reviewer, chosen=["apply"], values={target.sqid: {"parent": related.sqid}})
    assert answered.verdict_values == {target.sqid: {"parent": related.sqid}}
    assert apply_proposals(answered, actor=reviewer) == "applied"
    target.refresh_from_db()
    assert target.parent_id == related.pk


@pytest.mark.parametrize("value", [None, "Chosen"])
def test_non_workflow_asker_applies_choose_in_answer_transaction(targets, value):
    _, reviewer, target, related, _ = targets
    field = "parent" if value is None else "name"
    decision = ask(targets, {"fields": {field: {"choose": {}}}})

    def apply(sender, decision, **kwargs):
        apply_proposals(decision, actor=decision.answered_by)

    decision_answered.connect(apply, weak=False)
    try:
        answered = decision.decide(actor=reviewer, chosen=["apply"], values={target.sqid: {field: value}})
    finally:
        decision_answered.disconnect(apply)
    target.refresh_from_db()
    assert getattr(target, field) == value
    assert answered.verdict_values == {target.sqid: {field: value}}


def test_choose_values_are_accepted_and_exposed_through_graphql(targets):
    _, reviewer, target, *_ = targets
    decision = ask(targets, {"fields": {"name": {"choose": {}}}})
    schema = addon_schema(decision_schema.schemas, "console")
    values = {target.sqid: {"name": "Chosen"}}
    result_data(execute_schema(schema, """mutation($id: ID!, $revision: Int!, $values: JSON!) {
      decide(id: $id, revision: $revision, chosen: ["apply"], values: $values) { __typename }
    }""", {"id": decision.sqid, "revision": decision.revision, "values": values}, user=reviewer))
    data = result_data(execute_schema(schema, "{ decisions { verdict verdict_values } }", user=reviewer))
    assert data["decisions"] == [{"verdict": ["apply"], "verdict_values": values}]
