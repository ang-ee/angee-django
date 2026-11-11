"""StatementRecords generation across a subject source and its consumer donors, and survivorship."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.test.utils import isolate_apps
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from rebac import RelationshipTuple, actor_context, system_context, to_object_ref, to_subject_ref, write_relationships

from angee.base.models.base import AngeeModel
from angee.provenance.evidence import FactAuthority
from angee.provenance.statements import (
    Aspect,
    SourceKind,
    StatementField,
    StatementRecords,
    StatementsMixin,
)
from tests.conftest import create_platform_admin, create_user
from tests.extcontrib.models import Claimed, ClaimedBilling, ClaimedSource, ClaimedStatementQuerySet
from tests.statement_models import Ledger


def test_statement_link_metadata_matches_the_declared_capability() -> None:
    assert Ledger.statements.model.link_field() is None
    statement_model = apps.get_model("parties", "Party").statements.model
    assert statement_model.link_field() is statement_model._meta.get_field("link")
    assert isinstance(statement_model.link_field(), models.ForeignKey)


def test_the_source_declaration_finalizes_one_table_carrying_every_donor_aspect() -> None:
    """Donors precede the source in composed bases; the source's options still own the table."""

    statement_model = Claimed.statements.model
    assert set(statement_model.statement_aspects) == {"identity", "tax", "registry"}
    assert {field.name for field in statement_model._meta.fields} >= {"subject", "name", "vat"}
    assert statement_model.sqid_prefix == "xcs_"
    assert isinstance(Claimed.statements.all(), ClaimedStatementQuerySet)
    declaration = ClaimedBilling.__dict__["statements"].declaration_for(Claimed)
    assert declaration is ClaimedSource.__dict__["statements"]
    assert not Claimed.check()


@pytest.mark.parametrize(
    ("source_kind", "authority"),
    [
        (SourceKind.DIRECTORY, FactAuthority.SOURCE),
        (SourceKind.PERSON, FactAuthority.CORRECTION),
        (SourceKind.RULE, FactAuthority.UNVERIFIED),
        (SourceKind.EXTRACTION, FactAuthority.UNVERIFIED),
    ],
)
def test_a_statement_owns_its_shared_fact_authority(source_kind: SourceKind, authority: FactAuthority) -> None:
    statement = Claimed.statements.model(source_kind=source_kind)

    assert statement.fact_authority is authority


def test_a_donor_aspect_asserts_and_resolves_through_the_subject(db: None) -> None:
    del db
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


def test_a_person_save_states_only_the_aspects_that_admit_a_person(db: None) -> None:
    """An aspect whose precedence leaves PERSON out is written by the save, not stated."""

    del db
    writer = create_user("person-save-writer")
    with system_context(reason="test person save fixture"):
        record = Claimed.objects.create(name="Acme")
        write_relationships([RelationshipTuple(to_object_ref(record), "writer", to_subject_ref(writer))])
    with actor_context(writer):
        row = Claimed.objects.get(pk=record.pk)
        row.vat = "PL1234567890"
        row.registry = "KRS 0000123456"
        row.save()
    record.refresh_from_db()
    assert (record.vat, record.registry) == ("PL1234567890", "KRS 0000123456")
    with system_context(reason="test person save read"):
        assert [statement.aspect for statement in record.statements.all()] == ["tax"]


def test_a_person_create_states_no_group_left_empty(db: None) -> None:
    """An empty field on create is no fact; clearing it later is an edit and is stated."""

    del db
    admin = create_platform_admin("person-create-admin")
    with actor_context(admin):
        record = Claimed.objects.create(name="Acme")
    with system_context(reason="test person create read"):
        assert [statement.aspect for statement in record.statements.all()] == ["identity"]
    with actor_context(admin):
        row = Claimed.objects.get(pk=record.pk)
        row.vat = "PL1234567890"
        row.save()
        row.vat = ""
        row.save()
    record.refresh_from_db()
    assert record.vat == ""
    with system_context(reason="test person clear read"):
        assert [(statement.aspect, statement.vat) for statement in record.statements.filter(aspect="tax")] == [
            ("tax", "")
        ]


def test_a_declaration_names_its_model_and_copies_money_without_its_currency() -> None:
    """``model_name`` sidesteps a taken ``<Subject>Statement``; a money column copies as a plain decimal."""

    generated = Ledger.statements.model
    assert generated.__name__ == "LedgerStance"
    assert generated._meta.db_table == "base_ledgerstance"
    amount = generated._meta.get_field("amount")
    assert type(amount) is models.DecimalField
    assert (amount.max_digits, amount.decimal_places, amount.null) == (12, 2, True)
    assert not getattr(amount, "angee_currency_field", None)
    assert not [error for error in generated.check() if error.id.startswith("angee.E01")]


@isolate_apps("tests")
def test_statement_copy_dispatches_to_the_field_owner() -> None:
    class ProjectedField(models.CharField, StatementField):
        def for_statement(self, fields):
            assert "label" in fields
            return models.TextField()

    class ProjectedSubject(StatementsMixin, AngeeModel):
        label = ProjectedField(max_length=40)
        statements = StatementRecords(aspects={"identity": Aspect(fields=("label",))})

        class Meta:
            app_label = "tests"

    field = ProjectedSubject.statements.model._meta.get_field("label")
    assert type(field) is models.TextField
    assert field.null and field.blank
    assert not field.has_default()
    assert isinstance(ProjectedSubject._meta.get_field("label"), ProjectedField)


# Survivorship over the ``tax`` aspect, whose precedence is PERSON, then SYSTEM_OF_RECORD, then every other kind.
P, SOR, IMPORT, SEED = SourceKind.PERSON, SourceKind.SYSTEM_OF_RECORD, SourceKind.IMPORT, SourceKind.SEED
_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_PREFERRED = {"rank": "preferred"}


def _confident(subject: Any, rows: Any) -> list[Any]:
    return [row for row in rows if row.confidence >= 0.5]


def _stance(record: Any, source: str, kind: str, vat: str, *, age: int = 0, **stance: Any) -> None:
    """Store one source's tax stance on ``record``, last changed ``age`` minutes before ``_NOW``."""

    rows = Claimed.statements.model._base_manager
    row = rows.create(subject=record, aspect="tax", asserted_by=f"src:{source}", source_kind=kind, vat=vat, **stance)
    rows.filter(pk=row.pk).update(updated_at=_NOW - timedelta(minutes=age))


def _resolved(record: Any) -> tuple[str, list[str]]:
    """Resolve the tax aspect; return the subject's value and the sources of its applied stances."""

    Claimed.statements.resolve(subject=record, aspect="tax")
    record.refresh_from_db()
    applied = Claimed.statements.model._base_manager.filter(subject=record, applied=True)
    return record.vat, [row.asserted_by.removeprefix("src:") for row in applied]


@pytest.mark.parametrize(
    ("stances", "declared", "outcome"),
    [
        pytest.param([("p", P, "P", {"confidence": 0.4}), ("s", SOR, "S", {})], {}, ("P", ["p"]), id="precedence"),
        pytest.param([("a", SOR, "A", {"confidence": 0.4}), ("b", SOR, "B", {})], {}, ("B", ["b"]), id="confidence"),
        pytest.param([("old", SOR, "O", {"age": 2}), ("new", SOR, "N", {"age": 1})], {}, ("N", ["new"]), id="recency"),
        pytest.param([("p", P, "P", {}), ("seed", SEED, "D", _PREFERRED)], {}, ("D", ["seed"]), id="preferred-first"),
        pytest.param(
            [("seed", SEED, "D", _PREFERRED), ("p", P, "P", _PREFERRED)], {}, ("P", ["p"]), id="preferred-by-precedence"
        ),
        pytest.param(
            [("p", P, "P", {"status": "withdrawn"}), ("q", P, "Q", {"status": "quarantined"}), ("s", SOR, "S", {})],
            {},
            ("S", ["s"]),
            id="only-asserted-compete",
        ),
        pytest.param([("p", P, "", {"snak": "no_value"}), ("s", SOR, "S", {})], {}, ("S", ["s"]), id="normal-no-value"),
        pytest.param([("p", P, "", {"snak": "no_value"})], {}, ("KEEP", []), id="no-value-alone-keeps"),
        pytest.param([("p", P, "", {"snak": "some_value"})], {}, ("KEEP", []), id="unknown-keeps"),
        pytest.param([("p", P, "", {"snak": "no_value", **_PREFERRED})], {}, ("", ["p"]), id="dismissal-clears"),
        pytest.param(
            [("p", P, "", {"snak": "some_value", **_PREFERRED}), ("s", SOR, "S", {})],
            {},
            ("KEEP", ["p"]),
            id="preferred-unknown-keeps",
        ),
        pytest.param([("p", P, "", {}), ("s", SOR, "S", {})], {}, ("", ["p"]), id="empty-winner"),
        pytest.param(
            [("a", IMPORT, "I", {}), ("p", P, "", {}), ("s", SOR, "S", {})],
            {"allow_fill": True},
            ("S", ["p"]),
            id="allow-fill-from-the-next-most-trusted",
        ),
        pytest.param(
            [("p", P, "P", {"confidence": 0.4}), ("s", SOR, "S", {})],
            {"candidates": _confident},
            ("S", ["s"]),
            id="candidates-policy",
        ),
    ],
)
def test_survivorship_applies_the_most_trusted_asserted_stance(
    db: None, monkeypatch: pytest.MonkeyPatch, stances: list[Any], declared: dict[str, Any], outcome: Any
) -> None:
    """Preferred rank, then precedence, confidence and recency; normal unknowns never erase the value."""

    aspects = Claimed.statements.model.statement_aspects
    monkeypatch.setitem(aspects, "tax", replace(aspects["tax"], **declared))
    with system_context(reason="test survivorship"):
        record = Claimed.objects.create(name="Acme", vat="KEEP")
        for source, kind, vat, stance in stances:
            _stance(record, source, kind, vat, **stance)
        assert _resolved(record) == outcome


def test_a_candidate_policy_may_select_only_asserted_normal_stances(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = Claimed.statements.model._base_manager
    aspects = Claimed.statements.model.statement_aspects
    every_row = replace(aspects["tax"], candidates=lambda subject, _normal: rows.filter(subject=subject))
    monkeypatch.setitem(aspects, "tax", every_row)
    with system_context(reason="test candidate policy"):
        record = Claimed.objects.create(name="Acme", vat="KEEP")
        _stance(record, "p", P, "P", status="withdrawn")
        with pytest.raises(ValidationError, match="asserted normal"):
            _resolved(record)


def test_an_authoritative_system_of_record_ranks_just_before_a_person() -> None:
    def row(kind: str, authoritative: bool = False) -> SimpleNamespace:
        return SimpleNamespace(source_kind=kind, authoritative=authoritative)

    tax = Claimed.statements.model.statement_aspects["tax"]
    ranked = [row(SOR, authoritative=True), row(P), row(SOR), row(IMPORT)]
    assert sorted(ranked, key=tax.priority) == ranked
    assert [tax.priority(item) for item in ranked] == sorted({tax.priority(item) for item in ranked})
    # Where the precedence already lists the system of record first, authority changes nothing.
    registry = Aspect(precedence=(SOR, P))
    assert registry.priority(row(SOR, authoritative=True)) == registry.priority(row(SOR)) < registry.priority(row(P))


_STANCES = st.lists(
    st.tuples(
        st.sampled_from([P, SOR, IMPORT, SEED]),
        st.sampled_from(["normal", "preferred"]),
        st.sampled_from(["asserted", "withdrawn"]),
        st.sampled_from(["value", "no_value", "some_value"]),
        st.sampled_from([0.4, 0.9]),
        st.booleans(),
    ),
    min_size=1,
    max_size=5,
)


@settings(deadline=None, max_examples=25, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(stances=_STANCES, allow_fill=st.booleans(), data=st.data())
def test_insertion_order_never_changes_the_survivor(
    db: None, monkeypatch: pytest.MonkeyPatch, stances: list[Any], allow_fill: bool, data: st.DataObject
) -> None:
    """Two subjects receive the same stances in different orders and resolve alike; each example rolls back."""

    order = data.draw(st.permutations(range(len(stances))))
    aspects = Claimed.statements.model.statement_aspects
    monkeypatch.setitem(aspects, "tax", replace(aspects["tax"], allow_fill=allow_fill))
    with transaction.atomic(), system_context(reason="test survivorship order"):
        outcomes = []
        for indexes in (range(len(stances)), order):
            record = Claimed.objects.create(name="Acme", vat="KEEP")
            # Sources are named by insertion position, so neither pk nor source order follows the stance.
            for position, index in enumerate(indexes):
                kind, rank, status, snak, confidence, filled = stances[index]
                vat = f"v{index}" if filled else ""
                rank = rank if kind in (P, SEED) else "normal"
                stance = {"rank": rank, "status": status, "snak": snak, "confidence": confidence}
                _stance(record, str(position), kind, vat, age=index, **stance)
            vat, applied = _resolved(record)
            outcomes.append((vat, [indexes[int(source)] for source in applied]))
        assert outcomes[0] == outcomes[1]
        transaction.set_rollback(True)
