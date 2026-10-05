"""The shared interpreter validates declarations and uses normal record owners."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from pydantic import ValidationError as ContractError
from rebac import actor_context

from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.decisions.testing.models import Decision
from angee.workflows.decision_steps import apply_proposals
from tests.conftest import create_platform_admin, create_user


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


def test_multiple_rejects_overlapping_field_changes(targets):
    actions = {"fields": {"name": {"set": "After"}}}
    with pytest.raises(ContractError, match="overlap"):
        ask(targets, actions, multiple=True, second=actions)


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
