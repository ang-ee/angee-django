"""Tests for the uom addon — conversion math and the reference-unit invariant.

Rows are created under ``system_context`` because the write surface is admin-only
(strict REBAC). The conversion math is pure Decimal and backend-agnostic, so
nothing here is PostgreSQL-marked.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from django.db import IntegrityError, connection, transaction
from rebac import system_context

from angee.uom.models import Uom as AbstractUom
from angee.uom.models import UomCategory as AbstractUomCategory
from tests.conftest import _clear_model_tables, _create_missing_tables


class UomCategory(AbstractUomCategory):
    """Concrete unit-of-measure category used by uom tests."""

    class Meta(AbstractUomCategory.Meta):
        """Django model options for the canonical test uom category."""

        abstract = False
        app_label = "uom"
        db_table = "test_uom_category"
        rebac_resource_type = "uom/category"
        rebac_id_attr = "sqid"


class Uom(AbstractUom):
    """Concrete unit of measure used by uom tests."""

    class Meta(AbstractUom.Meta):
        """Django model options for the canonical test uom."""

        abstract = False
        app_label = "uom"
        db_table = "test_uom_uom"
        rebac_resource_type = "uom/uom"
        rebac_id_attr = "sqid"


UOM_TEST_MODELS = (UomCategory, Uom)
"""Concrete uom models created on demand by uom test fixtures."""


@pytest.fixture()
def uom_tables(transactional_db: Any) -> Iterator[None]:
    """Create the concrete uom tables for the duration of one test."""

    del transactional_db
    created_models = _create_missing_tables(UOM_TEST_MODELS)
    try:
        yield
    finally:
        _clear_model_tables(UOM_TEST_MODELS)
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


def _make_category(**fields: Any) -> Any:
    """Create one UomCategory under system_context (admin-only surface)."""

    with system_context(reason="uom tests setup"):
        return UomCategory.objects.create(**fields)


def _make_uom(**fields: Any) -> Any:
    """Create one Uom under system_context (admin-only surface)."""

    with system_context(reason="uom tests setup"):
        return Uom.objects.create(**fields)


@pytest.fixture()
def units(uom_tables: None) -> SimpleNamespace:
    """Seed a weight, a count, and a volume category with a few units."""

    del uom_tables
    weight = _make_category(name="Weight")
    kilogram = _make_uom(
        category=weight,
        name="Kilogram",
        ratio=Decimal(1),
        rounding=Decimal("0.001"),
        is_reference=True,
    )
    gram = _make_uom(
        category=weight,
        name="Gram",
        ratio=Decimal("0.001"),
        rounding=Decimal("0.001"),
    )

    count = _make_category(name="Unit")
    each = _make_uom(
        category=count,
        name="Unit(s)",
        ratio=Decimal(1),
        rounding=Decimal("0.01"),
        is_reference=True,
    )
    dozen = _make_uom(
        category=count,
        name="Dozen",
        ratio=Decimal(12),
        rounding=Decimal("0.01"),
    )

    volume = _make_category(name="Volume")
    litre = _make_uom(
        category=volume,
        name="Litre",
        ratio=Decimal(1),
        rounding=Decimal("0.001"),
        is_reference=True,
    )
    return SimpleNamespace(kilogram=kilogram, gram=gram, each=each, dozen=dozen, litre=litre)


def test_dozen_to_unit_is_exactly_twelve(units: SimpleNamespace) -> None:
    """One dozen resolves to twelve single units, exactly."""

    assert units.dozen.convert(Decimal(1), units.each) == Decimal(12)


def test_grams_to_kilograms(units: SimpleNamespace) -> None:
    """1500 grams resolve to 1.5 kilograms."""

    assert units.gram.convert(Decimal(1500), units.kilogram) == Decimal("1.5")


def test_cross_category_conversion_raises(units: SimpleNamespace) -> None:
    """Converting between categories fails fast with a clear error."""

    with pytest.raises(ValueError):
        units.kilogram.convert(Decimal(1), units.litre)


def test_conversion_rounds_half_up(units: SimpleNamespace) -> None:
    """1234.5 g -> 1.2345 kg rounds to the kg step (0.001) half away from zero.

    HALF_UP keeps 1.235 (HALF_EVEN would keep 1.234), so this pins the mode.
    """

    assert units.gram.convert(Decimal("1234.5"), units.kilogram) == Decimal("1.235")


def test_conversion_quantizes_to_destination_rounding(units: SimpleNamespace) -> None:
    """1 unit -> 1/12 dozen = 0.08333…, quantized to the dozen step (0.01)."""

    assert units.each.convert(Decimal(1), units.dozen) == Decimal("0.08")


def test_one_reference_per_category_is_rejected(uom_tables: None) -> None:
    """A second is_reference unit in the same category violates the constraint."""

    del uom_tables
    weight = _make_category(name="Weight")
    _make_uom(
        category=weight,
        name="Kilogram",
        ratio=Decimal(1),
        rounding=Decimal("0.001"),
        is_reference=True,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        _make_uom(
            category=weight,
            name="Second reference",
            ratio=Decimal(1000),
            rounding=Decimal("0.001"),
            is_reference=True,
        )


def test_multiple_non_reference_units_coexist(uom_tables: None) -> None:
    """The partial constraint leaves non-reference units unconstrained."""

    del uom_tables
    weight = _make_category(name="Weight")
    gram = _make_uom(
        category=weight,
        name="Gram",
        ratio=Decimal("0.001"),
        rounding=Decimal("0.001"),
    )
    tonne = _make_uom(
        category=weight,
        name="Tonne",
        ratio=Decimal(1000),
        rounding=Decimal("0.001"),
    )
    assert gram.pk is not None
    assert tonne.pk is not None


def test_same_reference_flag_across_categories_is_allowed(uom_tables: None) -> None:
    """The constraint is per category — each category keeps its own reference."""

    del uom_tables
    weight = _make_category(name="Weight")
    volume = _make_category(name="Volume")
    kilogram = _make_uom(
        category=weight,
        name="Kilogram",
        ratio=Decimal(1),
        rounding=Decimal("0.001"),
        is_reference=True,
    )
    litre = _make_uom(
        category=volume,
        name="Litre",
        ratio=Decimal(1),
        rounding=Decimal("0.001"),
        is_reference=True,
    )
    assert kilogram.pk is not None
    assert litre.pk is not None


@pytest.fixture()
def temperatures(uom_tables: None) -> SimpleNamespace:
    """Kelvin-referenced temperature units exercising the affine offset."""

    del uom_tables
    temperature = _make_category(name="Temperature")
    kelvin = _make_uom(
        category=temperature,
        name="Kelvin",
        ratio=Decimal(1),
        rounding=Decimal("0.01"),
        is_reference=True,
    )
    celsius = _make_uom(
        category=temperature,
        name="Celsius",
        ratio=Decimal(1),
        offset=Decimal("273.15"),
        rounding=Decimal("0.1"),
    )
    fahrenheit = _make_uom(
        category=temperature,
        name="Fahrenheit",
        ratio=Decimal("0.5555555556"),
        offset=Decimal("255.3722222222"),
        rounding=Decimal("0.1"),
    )
    return SimpleNamespace(kelvin=kelvin, celsius=celsius, fahrenheit=fahrenheit)


def test_celsius_to_kelvin_applies_offset(temperatures: SimpleNamespace) -> None:
    """0 °C is 273.15 K: the offset carries the zero-point shift."""

    assert temperatures.celsius.convert(Decimal(0), temperatures.kelvin) == Decimal("273.15")


def test_celsius_to_fahrenheit_round_trip(temperatures: SimpleNamespace) -> None:
    """The affine map recovers the textbook anchor points both ways."""

    assert temperatures.celsius.convert(Decimal(0), temperatures.fahrenheit) == Decimal("32.0")
    assert temperatures.celsius.convert(Decimal(100), temperatures.fahrenheit) == Decimal("212.0")
    assert temperatures.celsius.convert(Decimal(37), temperatures.fahrenheit) == Decimal("98.6")
    assert temperatures.fahrenheit.convert(Decimal("98.6"), temperatures.celsius) == Decimal("37.0")


def test_multiplicative_units_keep_zero_offset_math(units: SimpleNamespace) -> None:
    """Offset defaults to 0, so ordinary units convert exactly as before."""

    assert units.gram.offset == Decimal(0)
    assert units.gram.convert(Decimal(500), units.kilogram) == Decimal("0.5")


def test_reference_with_offset_is_rejected(uom_tables: None) -> None:
    """A reference unit must be the identity map: ratio 1, offset 0."""

    temperature = _make_category(name="Temperature (constraint)")
    with pytest.raises(IntegrityError), transaction.atomic():
        _make_uom(
            category=temperature,
            name="Bad reference",
            ratio=Decimal(1),
            offset=Decimal("273.15"),
            rounding=Decimal("0.01"),
            is_reference=True,
        )
