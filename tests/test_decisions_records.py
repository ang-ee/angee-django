"""Concern relations, proposals, attention and answers across unrelated owners."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import strawberry_django
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connection
from django.db import models
from django.test.utils import CaptureQueriesContext
from pydantic import ValidationError as ContractError
from rebac import actor_context, system_context

from angee.base.scoping import system_queryset
from angee.decisions import schema as decisions_schema
from angee.decisions.contracts import DecisionProposal, DecisionRequest, RecordActions
from angee.decisions.testing.models import Decision
from angee.graphql.data import hasura_model_resource, public_pk_decoder
from angee.graphql.node import AngeeNode
from angee.graphql.publishing import connect_publishers, disconnect_publishers
from angee.graphql.schema import SchemaParts
from angee.workflows.decision_steps import apply_proposals
from tests.conftest import addon_schema, create_platform_admin, create_user, execute_schema, result_data, vault_for
from tests.linesdemo.models import Document, DocumentLine, Tag


def linked_tags(line):
    """The line's tags, read with system authority: the test asserts state, not access."""
    with system_context(reason="test.decision_many_to_many.read"):
        return list(line.tags.order_by("pk"))


@pytest.fixture
def many_to_many(composed_tables, monkeypatch):
    actor = create_platform_admin("relation-answerer")
    with system_context(reason="test.decision_many_to_many"):
        document = Document.objects.create(title="Document")
        line = DocumentLine.objects.create(document=document, label="Before")
        tags = tuple(Tag.objects.create(name=name) for name in ("First", "Second", "Hidden"))
        line.tags.set(tags[:1])

    @strawberry_django.type(DocumentLine)
    class LineType(AngeeNode):
        label: str

        @strawberry_django.field
        def tags(self) -> list[str]:
            return [tag.public_id for tag in self.tags.all()]

    resource = hasura_model_resource(
        LineType, model=DocumentLine, name="decision_lines", filterable=("id",), sortable=("id",),
        aggregatable=("id",), writable=("label", "tags"), field_id_decode={"tags": public_pk_decoder(Tag)},
    )
    monkeypatch.setattr("angee.decisions.contracts.schema_parts_for", lambda app: {
        "console": SchemaParts(mutation=(resource.mutation,)),
    })
    return actor, line, tags


@pytest.mark.parametrize("choose", [False, True])
@pytest.mark.parametrize("clear", [False, True])
def test_many_to_many_proposals_replace_or_clear_after_the_scalar_save(many_to_many, choose, clear):
    actor, line, tags = many_to_many
    identities = [] if clear else [tag.public_id for tag in tags[1:]]
    proposal = DecisionProposal(alternatives=[{
        "key": "apply", "label": "Apply", "outcome": "done", "actions": {line.public_id: {"fields": {
            "label": {"set": "After"}, "tags": {"choose": {}} if choose else {"set": identities},
        }}},
    }])
    values = {line.public_id: {"tags": identities}} if choose else {}
    selected = proposal.choose(["apply"], values=values, record_models={line.public_id: DocumentLine})
    related = []
    resolved = selected[0].actions[line.public_id].resolve(
        line, context={"actor": actor, "related_records": related}, values=values.get(line.public_id),
    )
    assert resolved["tags"] == (() if clear else tags[1:])
    assert related == ([] if clear else list(tags[1:]))
    links = Mock()
    links.with_actor.return_value.all.return_value = [SimpleNamespace(record_public_id=line.public_id, record=line)]
    decision = SimpleNamespace(is_open=False, verdict=["apply"], proposal=proposal.model_dump(mode="json"),
                               verdict_values=values, records=links)
    ctx = Mock()

    def relation_changed(sender, instance, action, **kwargs):
        if action.startswith("pre_"):
            assert system_queryset(DocumentLine).get(pk=instance.pk).label == "After"

    models.signals.m2m_changed.connect(relation_changed, sender=DocumentLine.tags.through, weak=False)
    try:
        assert apply_proposals(decision, actor=actor, ctx=ctx) == "done"
    finally:
        models.signals.m2m_changed.disconnect(relation_changed, sender=DocumentLine.tags.through)
    line.refresh_from_db()
    assert line.label == "After"
    assert set(linked_tags(line)) == (set() if clear else set(tags[1:]))
    ctx.record.assert_called_once_with(line, operation="changed")


@pytest.mark.parametrize("value", [None, "tag_single", [1], ["tag_one", None], {"id": "tag_one"}])
def test_many_to_many_set_and_choose_require_string_lists(many_to_many, value):
    actor, line, _tags = many_to_many
    with pytest.raises(ValueError):
        RecordActions(fields={"tags": {"set": value}}).resolve(line, context={"actor": actor})
    proposal = DecisionProposal(alternatives=[{
        "key": "apply", "label": "Apply", "outcome": "done",
        "actions": {line.public_id: {"fields": {"tags": {"choose": {}}}}},
    }])
    with pytest.raises(ValueError):
        proposal.choose(["apply"], values={line.public_id: {"tags": value}},
                        record_models={line.public_id: DocumentLine})
    with pytest.raises(ValueError):
        proposal.alternatives[0].actions[line.public_id].resolve(
            line, context={"actor": actor}, values={"tags": value},
        )


@pytest.mark.parametrize("choose", [False, True])
def test_many_to_many_refuses_an_unreadable_related_id(many_to_many, monkeypatch, choose):
    actor, line, tags = many_to_many
    original = type(Tag.objects).with_actor
    actors = []

    def scoped(manager, answering):
        if manager.model is Tag:
            actors.append(answering)
            return original(manager, answering).exclude(pk=tags[-1].pk)
        return original(manager, answering)

    monkeypatch.setattr(type(Tag.objects), "with_actor", scoped)
    identities = [tags[1].public_id, tags[-1].public_id]
    actions = RecordActions(fields={"tags": {"choose": {}} if choose else {"set": identities}})
    with pytest.raises(ValueError, match="absent or unreadable"):
        actions.resolve(line, context={"actor": actor}, values={"tags": identities} if choose else None)
    assert actors == [actor]
    assert linked_tags(line) == [tags[0]]


@pytest.mark.parametrize("choose", [False, True])
def test_one_to_many_proposals_stay_refused(many_to_many, choose):
    actor, line, _tags = many_to_many
    with pytest.raises(ValueError, match="Set a scalar field"):
        RecordActions(fields={"lines": {"choose": {}} if choose else {"set": []}}).resolve(
            line.document, context={"actor": actor}, values={"lines": []} if choose else None,
        )


def test_many_to_many_requires_an_updatable_resource(many_to_many, monkeypatch):
    actor, line, _tags = many_to_many
    monkeypatch.setattr("angee.decisions.contracts.schema_parts_for", lambda app: {})
    with pytest.raises(ValueError, match="does not expose writes to tags"):
        RecordActions(fields={"tags": {"set": []}}).resolve(line, context={"actor": actor})


def test_many_to_many_is_not_written_when_scalar_full_clean_fails(many_to_many):
    actor, line, tags = many_to_many
    line.with_actor(actor).label = ""
    line.save(update_fields=["label"])
    proposal = DecisionProposal(alternatives=[{
        "key": "apply", "label": "Apply", "outcome": "done",
        "actions": {line.public_id: {"fields": {"tags": {"set": [tags[1].public_id]}}}},
    }])
    links = Mock()
    links.with_actor.return_value.all.return_value = [SimpleNamespace(record_public_id=line.public_id, record=line)]
    decision = SimpleNamespace(is_open=False, verdict=["apply"], proposal=proposal.model_dump(mode="json"),
                               verdict_values={}, records=links)
    ctx = Mock()
    with pytest.raises(ValidationError, match="label"):
        apply_proposals(decision, actor=actor, ctx=ctx)
    assert linked_tags(line) == [tags[0]]
    ctx.record.assert_not_called()


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


def test_need_without_cutover_question_refuses_plainly():
    need = apps.get_model("intake", "Need")()
    with pytest.raises(ValidationError, match="no access question"):
        need.decide_access("intake.approve")


def test_change_concerns_deduplicate_before_querying_child_models(composed_tables):
    from angee.base.refs import record_ref_for
    from angee.graphql.events import ChangeRelatedRecord
    from tests.mtidemo.models import MtiChild, MtiParent

    with system_context(reason="test.batch_concerns"):
        children = [MtiChild.objects.create(title=str(index)) for index in range(3)]
        refs = [record_ref_for(MtiParent.objects.get(pk=child.pk)) for child in children]
        with CaptureQueriesContext(connection) as captured:
            result = ChangeRelatedRecord.for_records(*refs, *refs)
    assert len(result) == 6
    assert len(captured) == 1


def test_relation_without_actor_scoping_is_a_contract_value_error():
    from django.contrib.contenttypes.models import ContentType

    class UnscopedEdge(models.Model):
        target = models.ForeignKey(ContentType, on_delete=models.CASCADE)

        class Meta:
            app_label = "mtidemo"

    with pytest.raises(ValueError, match="actor scoping"):
        RecordActions(fields={"target": {"set": "unscoped"}}).resolve(UnscopedEdge(), context={"actor": None})


@pytest.mark.django_db(transaction=True)
def test_admission_publishes_once_with_its_concern_records(records, monkeypatch):
    events = []
    monkeypatch.setattr("angee.graphql.publishing._send_change", lambda model, payload: events.append(payload))
    connect_publishers(Decision)
    try:
        decision = admit(records, records[3][:2])
    finally:
        disconnect_publishers(Decision)
    assert len(events) == 1
    assert events[0].id == decision.sqid
    assert {(ref.model, ref.id) for ref in events[0].related_records} == {
        (record._meta.label, record.sqid) for record in records[3][:2]
    }


def test_null_is_rejected_for_a_required_field(records):
    record = records[3][0]
    with pytest.raises(ContractError, match="cannot be null"):
        admit(records, [record], proposal={record.sqid: {"fields": {"name": {"set": None}}}})


def test_confirmation_has_no_write_and_owner_transfer_is_checked_at_admission(records):
    record = records[3][0]
    decision = admit(records, [record], proposal={record.sqid: {"fields": {"name": {}}}})
    actions = RecordActions.model_validate(decision.proposal["alternatives"][0]["actions"][record.sqid])
    assert actions.resolve(record, context={"actor": records[1]}) == {}
    assert actions.model_dump()["fields"] == {"name": {}}
    with pytest.raises(ValueError, match="owner"):
        RecordActions(fields={"owner": {"set": records[1].sqid}}).resolve(record, context={"actor": records[1]})


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
    assert answered.verdict_label == "Confirm"
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
@pytest.mark.parametrize("variable", [False, True])
def test_attention_is_actor_scoped_for_unrelated_resources_and_open_record_read(records, owner, variable):
    issuer, reviewer, outsider, vaults, drives = records
    targets = vaults if owner == "vault" else drives
    admit(records, [targets[0]])
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
    where = "$where" if variable else "{has_open_decisions: {_eq: true}}"
    declaration = "$where: attention_records_bool_exp" if variable else ""
    document = """query(DECLARATION) {
      attention_records(where: WHERE) { id has_open_decisions }
      count: attention_records_aggregate(where: WHERE) { aggregate { count } }
      all_records: attention_records { id has_open_decisions }
    }""".replace("(DECLARATION)", f"({declaration})" if declaration else "").replace("WHERE", where)
    variables = {}
    if variable:
        variables["where"] = {"_and": [{"has_open_decisions": {"_eq": True}}]}
    result = result_data(
        execute_schema(schema, document, variables, user=reviewer)
    )
    assert result["count"]["aggregate"]["count"] == 2
    assert result["attention_records"] == [
        {"id": record.sqid, "has_open_decisions": True} for record in (targets[0], targets[2])
    ]
    assert {row["id"]: row["has_open_decisions"] for row in result["all_records"]} == {
        record.sqid: record is not targets[1] for record in targets
    }
    with actor_context(outsider):
        # Pinning the target actor must win over the ambient outsider.
        scoped = model.objects.with_actor(reviewer)
        assert list(scoped.filter(Decision.objects.attention_expression(scoped))) == [targets[0], targets[2]]
    assert hidden.is_open
    # A list that does not request attention never joins the question owner.
    with CaptureQueriesContext(connection) as captured:
        plain = result_data(execute_schema(schema, "{ attention_records { id } }", user=reviewer))
    assert len(plain["attention_records"]) == 3
    assert not any('decisions_decision' in item["sql"].lower() for item in captured)
