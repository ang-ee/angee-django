"""Concern relations, proposals, attention and answers across unrelated owners."""

import pytest
import strawberry_django
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from pydantic import ValidationError as ContractError
from rebac import actor_context, system_context

from angee.base.scoping import system_queryset
from angee.decisions import schema as decisions_schema
from angee.decisions.contracts import DecisionProposal, DecisionRequest, RecordActions
from angee.decisions.testing.models import Decision
from angee.graphql.data import hasura_model_resource
from angee.graphql.node import AngeeNode
from tests.conftest import addon_schema, create_platform_admin, create_user, execute_schema, result_data, vault_for


@pytest.fixture
def records(composed_tables):
    issuer = create_platform_admin("concerns-issuer")
    reviewer = create_user("concerns-reviewer")
    outsider = create_user("concerns-outsider")
    vaults = [vault_for(reviewer, name=f"Reference {index}") for index in range(3)]
    drive_model = apps.get_model("storage", "Drive")
    with system_context(reason="test.concern_records"):
        backend = apps.get_model("storage", "Backend").objects.create(
            slug="concerns",
            label="References",
            backend_class="local",
        )
        drives = [
            drive_model.objects.create(
                owner=reviewer, backend=backend, prefix=str(index), slug=f"concerns-{index}", name=f"Drive {index}"
            )
            for index in range(3)
        ]
    return issuer, reviewer, outsider, vaults, drives


def admit(records, concerned, **changes):
    issuer, reviewer, _outsider, _vaults, _drives = records
    if "proposal" in changes:
        changes["proposal"] = DecisionProposal(
            alternatives=[{"key": "confirm", "label": "Confirm", "outcome": "done", "actions": changes["proposal"]}]
        )
    request = DecisionRequest(
        **{
            "kind": "confirm_record",
            "records": tuple(concerned),
            "proposal": DecisionProposal(alternatives=[{"key": "confirm", "label": "Confirm", "outcome": "done"}]),
            "assignees": (reviewer,),
            "requester": None,
            **changes,
        }
    )
    return Decision.objects.ask(request, actor=issuer)


@pytest.mark.parametrize("count", [1, 2, 3])
def test_concerns_form_one_relation_per_record_and_gate_each(records, count):
    _issuer, reviewer, _outsider, vaults, _drives = records
    decision = admit(records, vaults[:count])
    assert decision.records.with_actor(reviewer).count() == count
    with actor_context(reviewer):
        for record in vaults[:count]:
            assert list(Decision.objects.open_for(record)) == [decision]


def test_empty_concerns_are_rejected(records):
    with pytest.raises(ContractError):
        admit(records, [])


def test_null_is_rejected_for_a_required_field(records):
    record = records[3][0]
    with pytest.raises(ContractError, match="cannot be null"):
        admit(records, [record], proposal={record.sqid: {"fields": {"name": {"set": None}}}})


def test_admission_revalidates_mutated_proposals(records):
    issuer, reviewer, _outsider, vaults, _drives = records
    request = DecisionRequest(
        kind="confirm_record",
        records=(vaults[0],),
        proposal=DecisionProposal(alternatives=[{"key": "confirm", "label": "Confirm", "outcome": "done"}]),
        assignees=(reviewer,),
    )
    request.proposal.alternatives[0].actions["unknown"] = RecordActions(fields={"name": {"set": "Proposed"}})
    with pytest.raises(ContractError, match="Unknown concerned record"):
        Decision.objects.ask(request, actor=issuer)


@pytest.mark.parametrize(
    "proposal",
    [
        {"unknown": {"fields": {"name": {"set": "Proposed"}}}},
        {"SELF": {"fields": {"invented": {"set": "Proposed"}}}},
        {"SELF": {"fields": {"name": "Proposed"}}},
        {"SELF": {"fields": {"name": {"set": "Proposed", "call": "save"}}}},
        {"SELF": {"record": {"set": "Proposed"}}},
        {"SELF": {"extra": True}},
        [],
    ],
)
def test_proposals_reject_unknown_records_fields_and_malformed_actions(records, proposal):
    record = records[3][0]
    if isinstance(proposal, dict):
        proposal = {str(record.sqid) if key == "SELF" else key: value for key, value in proposal.items()}
    with pytest.raises(ContractError):
        admit(records, [record], proposal=proposal)
    assert not system_queryset(Decision).exists()


@pytest.mark.parametrize("kind", ["person", "service"])
def test_human_and_agent_answers_close_without_applying_proposals(records, kind):
    _issuer, reviewer, _outsider, vaults, drives = records
    with system_context(reason="test.agent_principal"):
        type(reviewer).objects.filter(pk=reviewer.pk).update(kind=kind)
    reviewer.refresh_from_db()
    proposal = {
        str(vaults[0].sqid): {"fields": {"name": {"set": "Changed"}}},
    }
    before = vaults[0].name
    decision = admit(records, [vaults[0], drives[0]], proposal=proposal)
    answered = Decision.objects.decide(decision.pk, actor=reviewer, revision=decision.revision, chosen=["confirm"])
    assert not answered.is_open
    assert answered.answered_by_id == reviewer.pk and answered.answered_at is not None
    assert answered.verdict == ["confirm"]
    vaults[0].refresh_from_db()
    assert vaults[0].name == before
    assert system_queryset(type(drives[0])).filter(pk=drives[0].pk).exists()
    with actor_context(reviewer):
        assert not Decision.objects.open_for(vaults[0]).exists()


def test_invalid_answers_and_record_edits_leave_the_question_open(records):
    record = records[3][0]
    decision = admit(records, [record])
    reviewer = records[1]
    for _ in range(4):
        with pytest.raises(ValidationError):
            Decision.objects.decide(decision.pk, actor=reviewer, revision=decision.revision, chosen=["unknown"])
    record.with_actor(reviewer).name = "Direct edit"
    record.save(update_fields={"name"})
    decision.refresh_from_db()
    assert decision.is_open and decision.revision == 1


@pytest.mark.parametrize("owner", ["vault", "drive"])
def test_attention_is_actor_scoped_for_unrelated_resources_and_open_record_read(records, owner):
    issuer, reviewer, outsider, vaults, drives = records
    targets = vaults if owner == "vault" else drives
    opened = admit(records, [targets[0]])
    closed = admit(records, [targets[1]])
    Decision.objects.decide(closed.pk, actor=reviewer, revision=closed.revision, chosen=["confirm"])
    # An actor can read a record without being allowed to read its question.
    hidden = admit(records, [targets[2]], assignees=(issuer,))
    model = type(targets[0])

    @strawberry_django.type(model)
    class AttentionType(AngeeNode):
        name: str

    resource = hasura_model_resource(
        AttentionType,
        model=model,
        name="attention_records",
        filterable=("id",),
        sortable=("id",),
        aggregatable=("id",),
        insert=False,
        update=False,
        delete=False,
    )
    console = decisions_schema.schemas["console"]
    schema = addon_schema(
        {
            "console": {
                **console,
                "query": [*console["query"], resource.query],
                "types": [*console["types"], *resource.types],
            }
        },
        "console",
    )
    document = """query($model: String!, $id: ID!) {
      attention_records(where: {has_open_decisions: {_eq: true}}) { id has_open_decisions }
      all_records: attention_records { id has_open_decisions }
      open_decisions(record_model: $model, record_id: $id) { id proposal records { record_model record_id } }
    }"""
    result = result_data(
        execute_schema(schema, document, {"model": model._meta.label, "id": targets[0].sqid}, user=reviewer)
    )
    assert result["attention_records"] == [
        {"id": record.sqid, "has_open_decisions": True} for record in (targets[0], targets[2])
    ]
    assert {row["id"]: row["has_open_decisions"] for row in result["all_records"]} == {
        record.sqid: record is not targets[1] for record in targets
    }
    assert result["open_decisions"][0]["id"] == opened.sqid
    with actor_context(outsider):
        # Pinning the target actor must win over the ambient outsider.
        scoped = model.objects.with_actor(reviewer)
        assert list(Decision.objects.records_with_open_decisions(scoped)) == [targets[0], targets[2]]
    result = result_data(
        execute_schema(schema, document, {"model": model._meta.label, "id": targets[2].sqid}, user=reviewer)
    )
    assert result["open_decisions"] == []
    assert hidden.is_open
    # A list that does not request attention never joins the question owner.
    with CaptureQueriesContext(connection) as captured:
        plain = result_data(execute_schema(schema, "{ attention_records { id } }", user=reviewer))
    assert len(plain["attention_records"]) == 3
    assert not any('decisions_decision' in item["sql"].lower() for item in captured)
