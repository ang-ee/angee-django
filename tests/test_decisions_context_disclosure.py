"""GraphQL's ``context`` is the question's context as the reader is shown it, never the stored one whole."""

import json

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import RelationshipTuple, system_context, to_object_ref, to_subject_ref, write_relationships
from rebac.relationships import delete_relationships
from rebac.types import RelationshipFilter

from angee.decisions import schema as decision_schema
from angee.decisions.contracts import DecisionContext, DecisionFact, DecisionRecordReference, DecisionRequest
from angee.decisions.testing.models import Decision
from angee.graphql.testing.documents import execute_schema, result_data
from tests.conftest import addon_schema, create_user, vault_for

DOCUMENT = "query($id: String!) { decisions_by_pk(id: $id) { context } }"
LEDGER_LABEL = "Ledger of salaries 2027"
STATEMENT_LABEL = "Bank statement March"


def _revoke_viewer(record, user):
    subject, reference = to_subject_ref(user), to_object_ref(record)
    delete_relationships(RelationshipFilter(
        resource_type=reference.resource_type, resource_id=reference.resource_id, relation="viewer",
        subject_type=subject.object.resource_type, subject_id=subject.object.resource_id,
    ))


def _reference(record, label):
    return DecisionRecordReference.of(record).model_copy(update={"label": label})


def _fact(label, value, *evidence, **extra):
    return DecisionFact(pointer=f"/{label.lower()}", label=label, value=value, authority="source",
                        evidence=tuple(evidence), **extra)


@pytest.fixture
def asked(db):
    issuer, reviewer = create_user("disclosure-issuer"), create_user("disclosure-reviewer")
    shared = vault_for(issuer, name="Shared order")
    ledger, statement = vault_for(issuer, name=LEDGER_LABEL), vault_for(issuer, name=STATEMENT_LABEL)
    write_relationships([
        RelationshipTuple(to_object_ref(record), "viewer", to_subject_ref(reviewer))
        for record in (shared, ledger, statement)
    ])
    context = DecisionContext(
        reason="Check the order",
        facts=(
            _fact("Supplier", "Acme Ltd"),
            _fact("Amount", 1234.5, widget="money", row={"currency": "EUR"}),
            _fact("Paid", True, _reference(statement, STATEMENT_LABEL)),
            _fact("Order", "PO-7", _reference(shared, "Shared order")),
        ),
        references=(_reference(shared, "Shared order"), _reference(ledger, LEDGER_LABEL)),
    )
    question = Decision.objects.ask(DecisionRequest(
        kind="disclosure_review", records=(shared,), assignees=(reviewer,), requester=issuer,
        proposal={"alternatives": [{"key": "keep", "label": "Keep", "outcome": "done"}]}, context=context,
    ), actor=issuer)
    return issuer, reviewer, ledger, statement, shared, Decision.system_queryset().get(pk=question.pk)


def shown(question, user):
    schema = addon_schema(decision_schema.schemas, "console")
    return result_data(execute_schema(schema, DOCUMENT, {"id": question.sqid}, user=user))["decisions_by_pk"]["context"]


def test_a_reader_whose_read_was_revoked_sees_neither_the_label_nor_the_fact(asked):
    issuer, reviewer, ledger, statement, _shared, question = asked
    assert shown(question, reviewer) == question.context, "every record is readable, so nothing is left out yet"

    _revoke_viewer(ledger, reviewer)
    _revoke_viewer(statement, reviewer)
    stored = question.context
    context = shown(question, reviewer)
    assert context == {
        **stored,
        "facts": [fact for fact in stored["facts"] if fact["label"] != "Paid"],
        "references": [reference for reference in stored["references"] if reference["label"] != LEDGER_LABEL],
    }
    text = json.dumps(context)
    assert LEDGER_LABEL not in text and STATEMENT_LABEL not in text
    assert ledger.sqid not in text and statement.sqid not in text
    assert shown(question, issuer) == stored, "the issuer still reads every record"


def test_the_stored_context_is_never_returned_whole(asked):
    issuer, reviewer, _ledger, _statement, _shared, question = asked
    stored = DecisionContext.model_validate(question.context)
    gone = DecisionRecordReference(model="gone.Record", id="gon_1", label="A record of a dropped addon")
    with system_context(reason="test.context.stored"):
        Decision.system_queryset().filter(pk=question.pk).owner_update(context=stored.model_copy(update={
            "facts": (*stored.facts, _fact("Archived", "kept", gone)),
            "references": (*stored.references, gone),
        }).model_dump(mode="json"))
    question = Decision.system_queryset().get(pk=question.pk)
    for reader in (issuer, reviewer):
        context = shown(question, reader)
        assert context != question.context
        assert context == stored.model_dump(mode="json"), "a dropped model's entries are shown to no one"
        assert "A record of a dropped addon" not in json.dumps(context)


def test_a_reader_of_the_question_alone_is_shown_only_its_own_statements(asked):
    """The reason and the facts with no evidence; no reference and no fact drawn from a record they may not read."""

    *_rest, question = asked
    outsider = create_user("disclosure-outsider")
    stored = DecisionContext.model_validate(question.context)
    assert question.context_for(outsider) == DecisionContext(
        reason=stored.reason, facts=tuple(fact for fact in stored.facts if not fact.evidence),
    )
    schema = addon_schema(decision_schema.schemas, "console")
    assert result_data(execute_schema(schema, DOCUMENT, {"id": question.sqid}, user=outsider)) == {
        "decisions_by_pk": None,
    }, "GraphQL reads the row itself in the reader's scope"


def test_a_list_reads_every_rows_context_in_one_batch(asked):
    """The same statements for 1, 3 and 11 rows: one ``readable_evidence`` batch for the whole list."""

    issuer, reviewer, _ledger, _statement, shared, question = asked
    schema = addon_schema(decision_schema.schemas, "console")

    def statements() -> int:
        with CaptureQueriesContext(connection) as captured:
            result_data(execute_schema(schema, "query { decisions { id context } }", user=reviewer))
        return len(captured)

    def ask_more(count: int) -> None:
        for _ in range(count):
            Decision.objects.ask(DecisionRequest(
                kind="disclosure_review", records=(shared,), assignees=(reviewer,),
                requester=issuer, proposal={"alternatives": [{"key": "keep", "label": "Keep", "outcome": "done"}]},
                context=DecisionContext.model_validate(question.context),
            ), actor=issuer)

    one = statements()
    ask_more(2)
    three = statements()
    ask_more(8)
    assert one == three == statements(), "a listed row adds no statement"


def test_an_empty_fact_with_a_row_stays_in_the_context_and_out_of_the_facts(asked):
    """The projection leaves out only what the reader may not read; a widget still reads the empty fact's row."""

    _issuer, reviewer, _ledger, _statement, _shared, question = asked
    stored = DecisionContext.model_validate(question.context)
    empty = _fact("Fee", None, widget="money", row={"currency": "EUR"})
    with system_context(reason="test.context.empty_fact"):
        Decision.system_queryset().filter(pk=question.pk).owner_update(
            context=stored.model_copy(update={"facts": (empty, *stored.facts)}).model_dump(mode="json"),
        )
    question = Decision.system_queryset().get(pk=question.pk)
    assert shown(question, reviewer)["facts"][0] == empty.model_dump(mode="json")
    assert "Fee" not in [fact.label for fact in question.facts_for(reviewer)]


def test_an_answers_reference_to_a_dropped_model_is_shown_to_no_one(asked):
    issuer, reviewer, _ledger, statement, _shared, question = asked
    gone = DecisionRecordReference(model="gone.Record", id="gon_1", label="A record of a dropped addon")
    kept = _reference(statement, STATEMENT_LABEL)
    # The methods read the row alone; a stored answer also needs a verdict, which they never read.
    question.answered_for = gone.model_dump(mode="json")
    question.answer_evidence = [gone.model_dump(mode="json"), kept.model_dump(mode="json")]
    for reader in (issuer, reviewer):
        assert question.readable_answered_for(reader) is None
        assert question.readable_answer_evidence(reader) == [kept.model_dump(mode="json")]
