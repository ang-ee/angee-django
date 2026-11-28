"""Tests for the uom addon — conversion math and the reference-unit invariant.

Rows are created under ``system_context`` because the write surface is admin-only
(strict REBAC). The conversion math is pure Decimal and backend-agnostic, so
nothing here is PostgreSQL-marked.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from rebac import SubjectRef, actor_context, system_context, to_object_ref
from rebac.evaluator import evaluator_scope
from rebac.models import active_relationship_model
from rebac.resources import model_resource_type

from angee.iam.testing import create_user
from angee.uom.models import UomExpressions

# The host's composer emits these; the tests read them from the registry.
Uom = apps.get_model("uom", "Uom")
UomCategory = apps.get_model("uom", "UomCategory")


def _shared_reader_exists(row: Any) -> bool:
    return active_relationship_model().objects.filter(
        resource_type=row._meta.rebac_resource_type,
        resource_id=to_object_ref(row).resource_id,
        relation="shared",
        subject_type="auth/user",
        subject_id="*",
    ).exists()



def test_units_have_the_names_record_pickers_show() -> None:
    """The irregular plurals come from the emitted models' Django metadata."""

    assert (str(Uom._meta.verbose_name), str(Uom._meta.verbose_name_plural)) == (
        "unit of measure",
        "units of measure",
    )
    assert (str(UomCategory._meta.verbose_name), str(UomCategory._meta.verbose_name_plural)) == (
        "unit of measure category",
        "unit of measure categories",
    )

def test_native_categories_and_units_use_authenticated_reads(
    make_category: Callable[..., Any], make_uom: Callable[..., Any],
) -> None:
    """A signed-in user reads native catalogue rows without wildcard tuples."""

    category = make_category(name="Native catalogue")
    unit = make_uom(
        category=category,
        name="Each",
        ratio=Decimal(1),
        rounding=Decimal("0.01"),
        is_reference=True,
    )
    reader = create_user("uom-reader")
    with actor_context(reader):
        assert UomCategory.objects.filter(pk=category.pk).exists()
        assert Uom.objects.filter(pk=unit.pk).exists()
    with actor_context(AnonymousUser()):
        assert not UomCategory.objects.filter(pk=category.pk).exists()
        assert not Uom.objects.filter(pk=unit.pk).exists()
    assert not _shared_reader_exists(category)
    assert not _shared_reader_exists(unit)



def test_reference_rows_follow_the_authenticated_reference_contract(
    make_category: Callable[..., Any],
    make_uom: Callable[..., Any],
) -> None:
    """Every authenticated principal reads units through scoped querysets; none may write; saves add no wildcard."""

    category = make_category(name="Reference")
    unit = make_uom(category=category, name="Each", ratio=Decimal(1), rounding=Decimal(1), is_reference=True)
    reader = create_user("uom-reference-reader")
    with actor_context(reader), evaluator_scope():
        for row in (category, unit):
            scoped = type(row).objects.with_actor(reader).scoped().filter(pk=row.pk)
            assert list(scoped.values_list("pk", flat=True)) == [row.pk]
            assert not row.with_actor(reader).has_access("write")
    with actor_context(SubjectRef.of("service/principal", "uom-reference-reader")):
        assert UomCategory.objects.filter(pk=category.pk).exists()
        assert Uom.objects.filter(pk=unit.pk).exists()
    # Fresh rows: with_actor() above bound the reader to those instances.
    with system_context(reason="uom reference inspect"):
        for row in (UomCategory.objects.get(pk=category.pk), Uom.objects.get(pk=unit.pk)):
            row.save()
            assert not active_relationship_model().objects.filter(
                resource_type=model_resource_type(row), resource_id=str(row.pk),
                subject_type="auth/user", subject_id="*",
            ).exists()

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


def _literal_uom(unit: Any) -> UomExpressions:
    decimal_field = models.DecimalField(max_digits=26, decimal_places=10)
    return UomExpressions(
        category_id=models.Value(unit.category_id),
        ratio=models.Value(unit.ratio, output_field=decimal_field),
        offset=models.Value(unit.offset, output_field=decimal_field),
        rounding=models.Value(unit.rounding, output_field=decimal_field),
    )


@pytest.mark.parametrize("field_reference", [False, True])
def test_expression_conversion_uses_affine_half_up_owner(units: SimpleNamespace, field_reference: bool) -> None:
    """ORM consumers get the same conversion and destination precision as scalar callers."""

    quantity_field = models.DecimalField(max_digits=26, decimal_places=10)
    quantity = models.Value(Decimal("1234.5"), output_field=quantity_field)
    expression = Uom.convert_expression(
        models.F("input_quantity") if field_reference else quantity,
        UomExpressions.from_path(""),
        _literal_uom(units.kilogram),
        output_field=quantity_field,
    )
    with system_context(reason="uom expression conversion"):
        converted = Uom.objects.filter(pk=units.gram.pk).alias(input_quantity=quantity).annotate(
            converted=expression
        ).values_list("converted", flat=True).get()
    assert converted == Decimal("1.235")


def test_expression_conversion_rejects_cross_category_rows(units: SimpleNamespace) -> None:
    """Unlike-category set rows project NULL instead of entering an aggregate."""

    quantity_field = models.DecimalField(max_digits=26, decimal_places=10)
    expression = Uom.convert_expression(
        models.Value(Decimal(1), output_field=quantity_field),
        UomExpressions.from_path(""),
        _literal_uom(units.litre),
        output_field=quantity_field,
    )
    with system_context(reason="uom expression category validation"):
        converted = Uom.objects.filter(pk=units.kilogram.pk).annotate(
            converted=expression
        ).values_list("converted", flat=True).get()
    assert converted is None


def test_one_reference_per_category_is_rejected(
    make_category: Callable[..., Any],
    make_uom: Callable[..., Any],
) -> None:
    """A second is_reference unit in the same category violates the constraint."""

    weight = make_category(name="Weight")
    make_uom(
        category=weight,
        name="Kilogram",
        ratio=Decimal(1),
        rounding=Decimal("0.001"),
        is_reference=True,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        make_uom(
            category=weight,
            name="Second reference",
            ratio=Decimal(1),
            rounding=Decimal("0.001"),
            is_reference=True,
        )


def test_multiple_non_reference_units_coexist(make_category: Callable[..., Any], make_uom: Callable[..., Any]) -> None:
    """The partial constraint leaves non-reference units unconstrained."""

    weight = make_category(name="Weight")
    gram = make_uom(
        category=weight,
        name="Gram",
        ratio=Decimal("0.001"),
        rounding=Decimal("0.001"),
    )
    tonne = make_uom(
        category=weight,
        name="Tonne",
        ratio=Decimal(1000),
        rounding=Decimal("0.001"),
    )
    assert gram.pk is not None
    assert tonne.pk is not None


def test_same_reference_flag_across_categories_is_allowed(
    make_category: Callable[..., Any],
    make_uom: Callable[..., Any],
) -> None:
    """The constraint is per category — each category keeps its own reference."""

    weight = make_category(name="Weight")
    volume = make_category(name="Volume")
    kilogram = make_uom(
        category=weight,
        name="Kilogram",
        ratio=Decimal(1),
        rounding=Decimal("0.001"),
        is_reference=True,
    )
    litre = make_uom(
        category=volume,
        name="Litre",
        ratio=Decimal(1),
        rounding=Decimal("0.001"),
        is_reference=True,
    )
    assert kilogram.pk is not None
    assert litre.pk is not None


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


def test_reference_with_offset_is_rejected(make_category: Callable[..., Any], make_uom: Callable[..., Any]) -> None:
    """A reference unit must be the identity map: ratio 1, offset 0."""

    temperature = make_category(name="Temperature (constraint)")
    with pytest.raises(IntegrityError), transaction.atomic():
        make_uom(
            category=temperature,
            name="Bad reference",
            ratio=Decimal(1),
            offset=Decimal("273.15"),
            rounding=Decimal("0.01"),
            is_reference=True,
        )


@pytest.mark.django_db
@pytest.mark.parametrize("field,value", [
    ("ratio", "0"), ("ratio", "-1"), ("rounding", "0"), ("rounding", "-0.1"), ("rounding", "0.5"),
])
def test_invalid_conversion_units_fail_validation_and_database_admission(
    field: str, value: str, make_category: Callable[..., Any],
) -> None:
    category = make_category(name="Validity")
    unit = Uom(name="Invalid", category=category, **{field: Decimal(value)})
    with system_context(reason="uom invalid validation"), pytest.raises(ValidationError):
        unit.full_clean()
    with pytest.raises(IntegrityError), transaction.atomic(), system_context(reason="uom invalid admission"):
        unit.save()
