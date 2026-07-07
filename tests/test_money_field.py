"""Tests for ``MoneyField`` — currency-path validation and money metadata.

The field owns the money vocabulary the frontend renders against; the widget
renderer itself ships with the ``angee.money`` addon (out of scope here). These
tests pin the backend contract: ``check`` validates the sibling / one-hop
currency path, ``deconstruct`` keeps the change migration-inert, and resource
metadata carries the ``Decimal`` scalar, the ``money`` widget, and the resolved
currency path.
"""

from __future__ import annotations

import decimal
from typing import cast

import strawberry
import strawberry_django
from django.db import models
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.state import ModelState, ProjectState
from strawberry import auto

from angee.base.fields import MoneyField
from angee.graphql.data.metadata import (
    DataResourceRoots,
    DataResourceTypeNames,
    make_data_resource_metadata,
    serialize_data_resources,
)
from angee.graphql.data.resource_fields import model_resource_fields
from angee.graphql.field_types import register_field_types
from tests.money_models import MoneyDocument, MoneyLine, MoneyStatement


def test_check_passes_for_a_sibling_currency_fk() -> None:
    """A ``currency_field`` naming a sibling FK to ``money.Currency`` is valid."""

    assert MoneyDocument._meta.get_field("amount").check() == []


def test_check_passes_for_a_one_hop_currency_path() -> None:
    """A one-hop ``order.currency`` path resolving through a parent FK is valid."""

    assert MoneyLine._meta.get_field("price").check() == []


def test_check_passes_for_a_one_hop_path_through_a_one_to_one() -> None:
    """A one-hop path whose first segment is a ``OneToOneField`` parent link is valid."""

    assert MoneyStatement._meta.get_field("total").check() == []


def test_check_reports_a_missing_currency_field() -> None:
    """A ``currency_field`` naming no field on the model fails with a clear error."""

    class NoCurrencyDoc(models.Model):
        """Model whose default ``currency`` sibling FK is absent."""

        amount = MoneyField()

        class Meta:
            """Register under the money app label."""

            app_label = "money"

    errors = NoCurrencyDoc._meta.get_field("amount").check()

    assert [error.id for error in errors] == ["angee.E011"]
    assert "has no field 'currency'" in errors[0].msg


def test_check_reports_a_non_currency_target() -> None:
    """A ``currency_field`` FK pointing at a non-``money.Currency`` model fails."""

    class WrongTargetDoc(models.Model):
        """Model whose ``currency`` FK targets an order, not a currency."""

        currency = models.ForeignKey("money.MoneyOrder", on_delete=models.CASCADE)
        amount = MoneyField(currency_field="currency")

        class Meta:
            """Register under the money app label."""

            app_label = "money"

    errors = WrongTargetDoc._meta.get_field("amount").check()

    assert [error.id for error in errors] == ["angee.E013"]
    assert "money.Currency" in errors[0].msg


def test_check_rejects_a_too_deep_path() -> None:
    """Only a sibling or a one-hop path is allowed; deeper paths fail."""

    field = MoneyField(currency_field="a.b.c")
    field.name = "amount"

    errors = field._check_currency_field()

    assert [error.id for error in errors] == ["angee.E010"]


def test_deconstruct_drops_currency_field_and_keeps_the_decimal_column() -> None:
    """The semantic kwarg never reaches migration state; decimal precision does."""

    _, path, _, kwargs = MoneyField(currency_field="order.currency").deconstruct()

    assert path == "angee.base.fields.MoneyField"
    assert "currency_field" not in kwargs
    assert kwargs == {"max_digits": 18, "decimal_places": 6}


def test_changing_currency_field_makes_no_migration() -> None:
    """Two states differing only in ``currency_field`` autodetect no changes."""

    def state(currency_field: str) -> ProjectState:
        project = ProjectState()
        project.add_model(
            ModelState(
                "money",
                "Doc",
                [
                    ("id", models.AutoField(primary_key=True)),
                    ("amount", MoneyField(currency_field=currency_field)),
                ],
            )
        )
        return project

    changes = MigrationAutodetector(state("currency"), state("order.currency"))._detect_changes()

    assert changes == {}


def test_model_metadata_carries_the_money_vocabulary() -> None:
    """The model-path field classifier emits Decimal + money + currency path."""

    fields = {field.name: field for field in model_resource_fields(MoneyDocument, ("amount",))}

    amount = fields["amount"]
    assert amount.kind == "scalar"
    assert amount.scalar == "Decimal"
    assert amount.widget == "money"
    assert amount.currency_field == "currency"


def test_resource_metadata_wire_projects_the_money_widget_and_currency_path() -> None:
    """The serialized resource wire carries widget ``money`` + ``currencyField``."""

    @strawberry.type(name="MoneyDoc")
    class MoneyDocType:
        """Node surface exposing the money amount over the wire."""

        id: strawberry.ID
        amount: decimal.Decimal

    metadata = make_data_resource_metadata(
        model=MoneyDocument,
        node_type=MoneyDocType,
        roots=DataResourceRoots(list_name="money_docs", aggregate_name="money_docs_aggregate"),
        type_names=DataResourceTypeNames(
            query="money_docs_Query",
            node="MoneyDoc",
            filter="money_docs_bool_exp",
            order="money_docs_order_by",
        ),
        capabilities=("list", "aggregate"),
        filter_fields=("id", "amount"),
    )

    [wire] = serialize_data_resources((metadata,), schema_name="console")
    wire_fields = cast("list[dict[str, object]]", wire["fields"])
    amount = next(field for field in wire_fields if field["name"] == "amount")
    assert amount["scalar"] == "Decimal"
    assert amount["widget"] == "money"
    assert amount["currencyField"] == "currency"


@strawberry_django.type(MoneyDocument)
class _MoneyDocAuto:
    """Projection resolving the money amount through ``auto``.

    Declared at module scope (not inside the test) so Strawberry can resolve the
    forward reference from the query root under ``from __future__ import
    annotations``.
    """

    amount: auto


@strawberry.type
class _MoneyAutoQuery:
    """Minimal query root exposing the auto-projected money document."""

    doc: _MoneyDocAuto


def test_moneyfield_resolves_under_auto() -> None:
    """``auto`` maps a ``MoneyField`` to the ``Decimal`` scalar once registered.

    strawberry-django's ``field_type_map`` is an exact-class lookup, so a
    ``DecimalField`` subclass raises ``NotImplemented`` until
    ``register_field_types`` maps it. After registration the amount resolves like
    any decimal — no explicit annotation on the projecting type — which is what
    lets an addon write ``list_price: auto`` instead of ``list_price: Decimal``.
    """

    register_field_types()

    sdl = strawberry.Schema(query=_MoneyAutoQuery).as_str()

    assert "amount: Decimal" in sdl
