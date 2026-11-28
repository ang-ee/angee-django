"""Fixtures for the emitted units-of-measure models."""

from collections.abc import Callable
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from django.apps import apps
from rebac import system_context


@pytest.fixture()
def make_category(db: None) -> Callable[..., Any]:
    """Create categories through the emitted model under the admin boundary."""

    category = apps.get_model("uom", "UomCategory")

    def create(**fields: Any) -> Any:
        with system_context(reason="uom tests setup"):
            return category.objects.create(**fields)

    return create


@pytest.fixture()
def make_uom(db: None) -> Callable[..., Any]:
    """Create units through the emitted model under the admin boundary."""

    unit = apps.get_model("uom", "Uom")

    def create(**fields: Any) -> Any:
        with system_context(reason="uom tests setup"):
            return unit.objects.create(**fields)

    return create


@pytest.fixture()
def units(make_category: Callable[..., Any], make_uom: Callable[..., Any]) -> SimpleNamespace:
    """Seed a weight, a count, and a volume category with a few units."""

    weight = make_category(name="Weight")
    kilogram = make_uom(
        category=weight,
        name="Kilogram",
        ratio=Decimal(1),
        rounding=Decimal("0.001"),
        is_reference=True,
    )
    gram = make_uom(
        category=weight,
        name="Gram",
        ratio=Decimal("0.001"),
        rounding=Decimal("0.001"),
    )

    count = make_category(name="Unit")
    each = make_uom(
        category=count,
        name="Unit(s)",
        ratio=Decimal(1),
        rounding=Decimal("0.01"),
        is_reference=True,
    )
    dozen = make_uom(
        category=count,
        name="Dozen",
        ratio=Decimal(12),
        rounding=Decimal("0.01"),
    )

    volume = make_category(name="Volume")
    litre = make_uom(
        category=volume,
        name="Litre",
        ratio=Decimal(1),
        rounding=Decimal("0.001"),
        is_reference=True,
    )
    return SimpleNamespace(kilogram=kilogram, gram=gram, each=each, dozen=dozen, litre=litre)


@pytest.fixture()
def temperatures(make_category: Callable[..., Any], make_uom: Callable[..., Any]) -> SimpleNamespace:
    """Kelvin-referenced temperature units exercising the affine offset."""

    temperature = make_category(name="Temperature")
    kelvin = make_uom(
        category=temperature,
        name="Kelvin",
        ratio=Decimal(1),
        rounding=Decimal("0.01"),
        is_reference=True,
    )
    celsius = make_uom(
        category=temperature,
        name="Celsius",
        ratio=Decimal(1),
        offset=Decimal("273.15"),
        rounding=Decimal("0.1"),
    )
    fahrenheit = make_uom(
        category=temperature,
        name="Fahrenheit",
        ratio=Decimal("0.5555555556"),
        offset=Decimal("255.3722222222"),
        rounding=Decimal("0.1"),
    )
    return SimpleNamespace(kelvin=kelvin, celsius=celsius, fahrenheit=fahrenheit)
