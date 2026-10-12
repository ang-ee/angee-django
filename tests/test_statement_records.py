"""StatementRecords generation across a subject source and its consumer donors."""

from __future__ import annotations

import pytest
from rebac import RelationshipTuple, actor_context, system_context, to_object_ref, to_subject_ref, write_relationships

from tests.conftest import create_user
from tests.extcontrib.models import Claimed, ClaimedBilling, ClaimedSource, ClaimedStatementQuerySet


def test_the_source_declaration_finalizes_one_table_carrying_every_donor_aspect() -> None:
    """Donors precede the source in composed bases; the source's options still own the table."""

    statement_model = Claimed.statements.model
    assert set(statement_model.statement_aspects) == {"identity", "tax"}
    assert {field.name for field in statement_model._meta.fields} >= {"subject", "name", "vat"}
    assert statement_model.sqid_prefix == "xcs_"
    assert isinstance(Claimed.statements.all(), ClaimedStatementQuerySet)
    declaration = ClaimedBilling.__dict__["statements"].declaration_for(Claimed)
    assert declaration is ClaimedSource.__dict__["statements"]
    assert not Claimed.check()


@pytest.mark.django_db(transaction=True)
def test_a_donor_aspect_asserts_and_resolves_through_the_subject(composed_tables: None) -> None:
    del composed_tables
    writer = create_user("donor-aspect-writer")
    with system_context(reason="test donor aspect fixture"):
        record = Claimed.objects.create(name="Acme")
        write_relationships([RelationshipTuple(to_object_ref(record), "writer", to_subject_ref(writer))])
    with actor_context(writer):
        statement = record.with_actor(writer).statements.set(aspect="tax", values={"vat": "PL1234567890"})
    record.refresh_from_db()
    assert (statement.aspect, statement.vat, statement.applied, statement.source_kind) == (
        "tax",
        "PL1234567890",
        True,
        "person",
    )
    assert record.vat == "PL1234567890"
    with system_context(reason="test donor aspect read"):
        assert list(record.statements.provenance("vat")) == [statement]


def test_a_declaration_names_its_model_and_copies_money_without_its_currency() -> None:
    """``model_name`` sidesteps a taken ``<Subject>Statement``; a money column copies as a plain decimal."""

    from django.db import models

    from tests.statement_models import Ledger

    generated = Ledger.statements.model
    assert generated.__name__ == "LedgerStance"
    amount = generated._meta.get_field("amount")
    assert type(amount) is models.DecimalField
    assert (amount.max_digits, amount.decimal_places, amount.null) == (12, 2, True)
    assert not getattr(amount, "angee_currency_field", None)
    assert not [error for error in generated.check() if error.id.startswith("angee.E01")]
