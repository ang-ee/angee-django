"""A question's facts as one reader is shown them: the readable ones, in stored order, within a bound."""

import pytest
from rebac import RelationshipTuple, system_context, to_object_ref, to_subject_ref, write_relationships
from rebac.relationships import delete_relationships
from rebac.types import RelationshipFilter

from angee.decisions.contracts import (
    DecisionContext,
    DecisionFact,
    DecisionRecordReference,
    DecisionRequest,
    title_case,
)
from angee.decisions.testing.models import Decision
from tests.conftest import create_user, vault_for


def _revoke_viewer(record, user):
    subject, reference = to_subject_ref(user), to_object_ref(record)
    delete_relationships(RelationshipFilter(
        resource_type=reference.resource_type, resource_id=reference.resource_id, relation="viewer",
        subject_type=subject.object.resource_type, subject_id=subject.object.resource_id,
    ))


def _fact(label, value, *evidence, authority="source", **extra):
    return DecisionFact(
        pointer=f"/{label.lower()}", label=label, value=value, authority=authority,
        evidence=tuple(DecisionRecordReference.of(record) for record in evidence), **extra,
    )


@pytest.fixture
def question(db):
    issuer, reviewer, outsider = (create_user(f"facts-{role}") for role in ("issuer", "reviewer", "outsider"))
    shared, statement = vault_for(issuer, name="Shared"), vault_for(issuer, name="Bank statement")
    write_relationships([
        RelationshipTuple(to_object_ref(record), "viewer", to_subject_ref(reviewer)) for record in (shared, statement)
    ])
    facts = (
        _fact("Supplier", "Acme Ltd"),
        _fact("Amount", 1234.5, widget="money", row={"currency": "EUR"}),
        _fact("Empty", ""),
        _fact("Missing", None),
        _fact("Paid", True, statement, authority="unverified"),
        _fact("Reference", "INV-0042", shared, authority="correction"),
    )
    decision = Decision.objects.ask(DecisionRequest(
        kind="review_facts", records=(shared,), assignees=(reviewer,), requester=issuer,
        proposal={"alternatives": [{"key": "keep", "label": "Keep", "outcome": "done"}]},
        context=DecisionContext(reason="Check the bill", facts=facts),
    ), actor=issuer)
    return issuer, reviewer, outsider, statement, Decision.system_queryset().get(pk=decision.pk)


def labels(facts):
    return [fact.label for fact in facts]


def test_a_reader_is_shown_the_facts_with_a_value_in_stored_order(question):
    issuer, reviewer, *_ = question
    row = question[-1]
    for reader in (issuer, reviewer):
        assert labels(row.facts_for(reader)) == ["Supplier", "Amount", "Paid", "Reference"]
    (amount,) = [fact for fact in row.facts_for(reviewer) if fact.label == "Amount"]
    assert (amount.value, amount.widget, amount.row) == (1234.5, "money", {"currency": "EUR"})
    assert amount.authority == "source"


def test_a_revoked_read_on_a_facts_evidence_drops_that_fact_for_that_reader(question):
    issuer, reviewer, _outsider, statement, row = question
    _revoke_viewer(statement, reviewer)
    assert labels(row.facts_for(reviewer)) == ["Supplier", "Amount", "Reference"]
    assert labels(row.facts_for(issuer)) == ["Supplier", "Amount", "Paid", "Reference"]


def test_a_fact_whose_evidence_names_a_model_no_longer_installed_is_shown_to_no_one(question):
    issuer, reviewer, *_rest, row = question
    stale = _fact("Archived", "kept", authority="source").model_copy(
        update={"evidence": (DecisionRecordReference(model="gone.Record", id="gon_1"),)},
    )
    context = DecisionContext.model_validate(row.context)
    with system_context(reason="test.facts.dropped_model"):
        Decision.system_queryset().filter(pk=row.pk).owner_update(
            context=context.model_copy(update={"facts": (*context.facts, stale)}).model_dump(mode="json"),
        )
    row = Decision.system_queryset().get(pk=row.pk)
    for reader in (issuer, reviewer):
        assert "Archived" not in labels(row.facts_for(reader))


def test_a_reader_who_may_not_read_the_question_is_shown_no_fact(question):
    _issuer, _reviewer, outsider, _statement, row = question
    assert row.facts_for(outsider) == []


@pytest.mark.parametrize(
    ("key", "card"),
    [
        ("due_date", "Due Date"),
        ("dueDate", "Due Date"),
        ("invoiceNumber", "Invoice Number"),
        ("vat-id", "Vat Id"),
        ("iban.check:status", "Iban Check Status"),
        ("Read value", "Read Value"),
        ("line2Total", "Line2 Total"),
    ],
)
def test_a_value_key_reads_as_the_cards_title_case(key, card):
    """The card's ``titleCase``: camelCase and ``-_./:`` split, every word capitalised."""

    assert title_case(key) == card


@pytest.mark.parametrize(
    ("value", "widget", "text"),
    [
        pytest.param("INV-0042", None, "INV-0042", id="text"),
        pytest.param(1234.5, "money", "1234.5", id="widget-reads-the-bare-value"),
        pytest.param(["A", None, 3], None, "A; -; 3", id="list"),
        pytest.param(
            {"due_date": "2027-03-01", "amountPaid": 12, "note": ""}, None, "Due Date 2027-03-01 · Amount Paid 12",
            id="object",
        ),
        pytest.param(True, None, "yes", id="boolean"),
    ],
)
def test_a_fact_has_one_text_form(value, widget, text):
    fact = DecisionFact(pointer="/x", label="X", value=value, authority="source", widget=widget,
                        row={"currency": "EUR", "precision": 2})
    assert fact.as_text() == text
