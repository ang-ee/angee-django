"""Tests for the money addon's runtime behavior — rounding, rates, conversion.

The reference currency is a **required** setting with no shipped default, so the
tests that exercise conversion inject it through ``override_settings`` (the test
owns its required setting). Catalogue and rate rows are admin-only surfaces, so
setup writes and the conversion reads run under ``system_context`` — emulating
the elevated actor a real posting runs as.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal, localcontext
from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.management import call_command
from django.db import connection
from django.test import override_settings
from rebac import system_context, to_object_ref
from rebac.models import active_relationship_model

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.money.rounding import RoundingMode
from tests.conftest import SchemaAddon, _clear_model_tables, _create_missing_tables
from tests.money_models import MONEY_TEST_MODELS, Currency, CurrencyRate


@pytest.fixture()
def money_tables(transactional_db: Any) -> Iterator[None]:
    """Create the concrete money tables for the duration of one test."""

    del transactional_db
    created_models = _create_missing_tables(MONEY_TEST_MODELS)
    # CurrencyRate now owns a persisted per-row reader. Production syncs the
    # authorization schema before load; dynamic test models must do the same.
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        _clear_model_tables(MONEY_TEST_MODELS)
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


def _make_currency(code: str, *, decimal_places: int = 2, name: str | None = None, symbol: str = "") -> Any:
    """Create one Currency row under system_context (admin-only surface)."""

    with system_context(reason="money tests setup"):
        return Currency.objects.create(
            code=code,
            name=name or code,
            symbol=symbol,
            decimal_places=decimal_places,
        )


def _make_rate(currency: Any, on_date: date, rate: str) -> Any:
    """Create one CurrencyRate row under system_context."""

    with system_context(reason="money tests setup"):
        return CurrencyRate.objects.create(currency=currency, date=on_date, rate=Decimal(rate))


def _make_contextual_rate(
    currency: Any,
    on_date: date,
    rate: str,
    *,
    context: Any,
    reference_currency: Any,
    source_priority: int = 0,
) -> Any:
    """Create one contextual rate directly through its native model contract."""

    return CurrencyRate.objects.create(
        currency=currency,
        date=on_date,
        rate=Decimal(rate),
        context_content_type=ContentType.objects.get_for_model(context),
        context_object_id=str(context.pk),
        reference_currency=reference_currency,
        source_priority=source_priority,
    )


def _shared_reader_exists(row: Any) -> bool:
    """Return whether a rate carries the authenticated-user wildcard reader."""

    return active_relationship_model().objects.filter(
        resource_type=row._meta.rebac_resource_type,
        resource_id=to_object_ref(row).resource_id,
        relation="shared",
        subject_type="auth/user",
        subject_id="*",
    ).exists()


def test_currency_resource_authors_human_label_and_exact_search_fields() -> None:
    """Currency choices display code plus name and search both concrete columns."""

    from angee.money import schema as money_schema

    parts = {
        key: tuple(money_schema.schemas["console"].get(key, ()))
        for key in SCHEMA_PART_KEYS
    }
    schema = GraphQLSchemas([SchemaAddon({"console": parts})]).build("console")
    resources = {item.model_label: item for item in schema.angee_resources}
    currency = resources["money.Currency"]
    code_filter = currency.query.fields["code"].filter
    name_filter = currency.query.fields["name"].filter

    assert currency.record_representation == "display_name"
    assert currency.record_search_fields == ("code", "name")
    assert code_filter is not None and "iContains" in code_filter.operators
    assert name_filter is not None and "iContains" in name_filter.operators


def test_round_uses_currency_exponent(money_tables: None) -> None:
    """The exponent comes from decimal_places: 0 for JPY, 3 for BHD, 2 for EUR."""

    del money_tables
    jpy = _make_currency("JPY", decimal_places=0)
    bhd = _make_currency("BHD", decimal_places=3)
    eur = _make_currency("EUR", decimal_places=2)
    assert jpy.round(Decimal("1234.567")) == Decimal("1235")
    assert bhd.round(Decimal("1.23449")) == Decimal("1.234")
    assert eur.round(Decimal("2.128")) == Decimal("2.13")


def test_round_uses_default_mode_and_explicit_overrides(money_tables: None) -> None:
    """The money vocabulary supplies a default and allows explicit overrides."""

    del money_tables
    eur = _make_currency("EUR", decimal_places=2)
    jpy = _make_currency("JPY", decimal_places=0)
    bhd = _make_currency("BHD", decimal_places=3)
    # 2dp tie at .125
    assert eur.round(Decimal("2.125")) == Decimal("2.13")
    assert eur.round(Decimal("2.125"), RoundingMode.HALF_EVEN) == Decimal("2.12")
    # 0dp tie at .5
    assert jpy.round(Decimal("2.5")) == Decimal("3")
    assert jpy.round(Decimal("2.5"), RoundingMode.HALF_EVEN) == Decimal("2")
    # 3dp tie at .0005
    assert bhd.round(Decimal("1.2345")) == Decimal("1.235")
    assert bhd.round(Decimal("1.2345"), RoundingMode.HALF_EVEN) == Decimal("1.234")


def test_convert_same_currency_returns_amount_untouched(money_tables: None) -> None:
    """The identity fast-path needs neither a rate nor the reference setting."""

    del money_tables
    eur = _make_currency("EUR", decimal_places=2)
    amount = Decimal("100.123456")
    assert eur.convert(amount, eur) == amount


@pytest.fixture()
def cross_rates(money_tables: None) -> Iterator[SimpleNamespace]:
    """Seed USD (reference), EUR and GBP with dated rates per one USD."""

    del money_tables
    usd = _make_currency("USD", decimal_places=2)
    eur = _make_currency("EUR", decimal_places=2)
    gbp = _make_currency("GBP", decimal_places=2)
    jpy = _make_currency("JPY", decimal_places=0)
    _make_rate(eur, date(2026, 1, 1), "0.9000000000")
    _make_rate(eur, date(2026, 6, 1), "0.8500000000")
    _make_rate(gbp, date(2026, 1, 1), "0.8000000000")
    with override_settings(ANGEE_MONEY_REFERENCE_CURRENCY="USD"):
        yield SimpleNamespace(usd=usd, eur=eur, gbp=gbp, jpy=jpy)


def test_reference_currency_has_rate_one_without_a_row(cross_rates: SimpleNamespace) -> None:
    """rate_for the reference is Decimal(1) — the unit every rate is quoted against."""

    with system_context(reason="money tests"):
        assert CurrencyRate.objects.rate_for(cross_rates.usd) == Decimal(1)


def test_convert_crosses_via_the_reference(cross_rates: SimpleNamespace) -> None:
    """EUR→GBP is amount * rate(GBP) / rate(EUR): 90 * 0.8 / 0.9 == 80."""

    with system_context(reason="money tests"):
        result = cross_rates.eur.convert(Decimal("90"), cross_rates.gbp, on_date=date(2026, 3, 1))
    assert result == Decimal("80")


def test_convert_to_reference_uses_unit_rate(cross_rates: SimpleNamespace) -> None:
    """EUR→USD is amount / rate(EUR): 90 / 0.9 == 100 (USD is the reference)."""

    with system_context(reason="money tests"):
        result = cross_rates.eur.convert(Decimal("90"), cross_rates.usd, on_date=date(2026, 3, 1))
    assert result == Decimal("100")


def test_convert_does_not_round(cross_rates: SimpleNamespace) -> None:
    """The converted amount keeps full precision — the consumer rounds, not convert."""

    with system_context(reason="money tests"):
        result = cross_rates.eur.convert(Decimal("10"), cross_rates.gbp, on_date=date(2026, 3, 1))
    # 10 * 0.8 / 0.9 = 8.888… — more than EUR's or GBP's 2 places, i.e. unrounded.
    assert result != result.quantize(Decimal("0.01"))


def test_missing_rate_fails_fast(cross_rates: SimpleNamespace) -> None:
    """A currency with no rate on or before the date raises, never returns zero."""

    with system_context(reason="money tests"), pytest.raises(CurrencyRate.DoesNotExist):
        cross_rates.eur.convert(Decimal("1"), cross_rates.jpy, on_date=date(2026, 3, 1))


def test_rate_for_picks_the_latest_on_or_before_the_date(cross_rates: SimpleNamespace) -> None:
    """rate_for returns the most recent rate dated on or before the query date."""

    rates = CurrencyRate.objects
    with system_context(reason="money tests"):
        assert rates.rate_for(cross_rates.eur, date(2026, 3, 1)) == Decimal("0.9000000000")
        assert rates.rate_for(cross_rates.eur, date(2026, 7, 1)) == Decimal("0.8500000000")
        with pytest.raises(CurrencyRate.DoesNotExist):
            rates.rate_for(cross_rates.eur, date(2025, 12, 1))


def test_conversion_without_the_setting_raises_improperly_configured(money_tables: None) -> None:
    """The reference setting is required at conversion time — no silent default."""

    del money_tables
    eur = _make_currency("EUR", decimal_places=2)
    gbp = _make_currency("GBP", decimal_places=2)
    with (
        override_settings(ANGEE_MONEY_REFERENCE_CURRENCY=""),
        system_context(reason="money tests"),
        pytest.raises(ImproperlyConfigured),
    ):
        eur.convert(Decimal("1"), gbp)


def test_contextual_rates_never_fall_back_to_global_history(money_tables: None) -> None:
    """An explicit context reads only its exact reference-relative history."""

    del money_tables
    usd = _make_currency("USD")
    eur = _make_currency("EUR")
    context = _make_currency("GBP")
    global_rate = _make_rate(eur, date(2026, 1, 1), "0.9")
    with system_context(reason="money contextual rate test"):
        with pytest.raises(CurrencyRate.DoesNotExist):
            CurrencyRate.objects.rate_for(
                eur,
                date(2026, 1, 1),
                context=context,
                reference_currency=usd,
            )
        contextual = _make_contextual_rate(
            eur,
            date(2026, 1, 1),
            "0.85",
            context=context,
            reference_currency=usd,
        )
        assert _shared_reader_exists(global_rate)
        assert not _shared_reader_exists(contextual)
        assert CurrencyRate.objects.rate_for(
            eur,
            date(2026, 1, 1),
            context=context,
            reference_currency=usd,
        ) == Decimal("0.85")
        contextual.is_archived = True
        contextual.save(update_fields={"is_archived"})
        with pytest.raises(CurrencyRate.DoesNotExist):
            CurrencyRate.objects.rate_for(
                eur,
                date(2026, 1, 1),
                context=context,
                reference_currency=usd,
            )


def test_contextual_rate_priority_precedes_date(money_tables: None) -> None:
    """A higher-priority context fact outranks a newer lower-priority fact."""

    del money_tables
    usd = _make_currency("USD")
    eur = _make_currency("EUR")
    context = _make_currency("GBP")
    with system_context(reason="money contextual priority test"):
        _make_contextual_rate(
            eur,
            date(2026, 1, 10),
            "0.8",
            context=context,
            reference_currency=usd,
            source_priority=0,
        )
        _make_contextual_rate(
            eur,
            date(2026, 1, 1),
            "0.9",
            context=context,
            reference_currency=usd,
            source_priority=1,
        )
        assert CurrencyRate.objects.rate_for(
            eur,
            date(2026, 1, 15),
            context=context,
            reference_currency=usd,
        ) == Decimal("0.9")


def test_context_is_validated_before_same_currency_identity(money_tables: None) -> None:
    """An invalid contextual owner cannot bypass validation through identity conversion."""

    del money_tables
    usd = _make_currency("USD")
    unsaved_context = Currency(code="EUR", name="EUR")
    with system_context(reason="money contextual identity test"), pytest.raises(ValidationError):
        usd.convert(
            Decimal("1"),
            usd,
            context=unsaved_context,
            reference_currency=usd,
        )


def test_currency_rate_identity_is_native_owned(money_tables: None) -> None:
    """Ordinary saves and bulk writes cannot move a rate slot."""

    del money_tables
    eur = _make_currency("EUR")
    gbp = _make_currency("GBP")
    rate = _make_rate(eur, date(2026, 1, 1), "0.9")
    rate.currency = gbp
    with system_context(reason="money rate identity test"), pytest.raises(ValidationError):
        rate.save(update_fields={"currency"})
    with (
        system_context(reason="money queryset identity test"),
        pytest.raises(ValidationError, match="Currency-rate identity changes through its native owner"),
    ):
        CurrencyRate.objects.filter(pk=rate.pk).update(currency=gbp)
    with (
        system_context(reason="money bulk identity test"),
        pytest.raises(ValidationError, match="Currency-rate identity changes through its native owner"),
    ):
        CurrencyRate.objects.bulk_update([rate], ["currency"])
    rate.refresh_from_db()
    assert rate.currency_id == eur.pk
    with system_context(reason="money queryset archive test"):
        assert CurrencyRate.objects.filter(pk=rate.pk).update(is_archived=True) == 1
        rate.is_archived = False
        assert CurrencyRate.objects.bulk_update([rate], ["is_archived"]) == 1
    rate.refresh_from_db()
    assert rate.is_archived is False


@pytest.mark.parametrize(
    "value",
    [
        "123456789012345678.12345678901234567890",
        "123456789012345678.1234567890123456789000",
        "100000000000000000.0000000000000000000000",
        "0.0000000000000000000100",
    ],
)
def test_currency_rate_accepts_exact_values_with_trailing_zeros(money_tables: None, value: str) -> None:
    """Field validation ignores redundant zeros without rounding significant digits."""

    del money_tables
    eur = _make_currency("EUR")
    rate = CurrencyRate(currency=eur, date=date(2026, 1, 1), rate=value)
    with system_context(reason="money exact rate test"), localcontext(prec=6, Emax=9, Emin=-9):
        rate.save()
    assert rate.pk is not None
    assert rate.rate.as_tuple() == Decimal(value).as_tuple()


@pytest.mark.parametrize("existing", [False, True], ids=["create", "update"])
@pytest.mark.parametrize(
    ("value", "error_code"),
    [
        ("0.123456789012345678901", "max_decimal_places"),
        ("123456789012345678.123456789012345678901", "max_digits"),
        ("1E18", "max_whole_digits"),
        ("1E39", "max_digits"),
        ("1E-21", "max_decimal_places"),
        ("1E1000000", "max_digits"),
        ("1E-1000000", "max_digits"),
    ],
)
def test_currency_rate_rejects_inexact_values_before_writing(
    money_tables: None, value: str, error_code: str, existing: bool
) -> None:
    """Native field limits reject inserts and updates independently of Decimal context."""

    del money_tables
    eur = _make_currency("EUR")
    rate = (
        _make_rate(eur, date(2026, 1, 1), "0.9")
        if existing
        else CurrencyRate(currency=eur, date=date(2026, 1, 1))
    )
    rate.rate = Decimal(value)
    with (
        system_context(reason="money invalid rate test"),
        localcontext(prec=6, Emax=9, Emin=-9),
        pytest.raises(ValidationError) as error,
    ):
        rate.save(update_fields={"rate"} if existing else None)
    assert [item.code for item in error.value.error_list] == [error_code]
    with system_context(reason="money unchanged rate test"):
        if existing:
            rate.refresh_from_db()
            assert rate.rate == Decimal("0.9")
        else:
            assert not CurrencyRate.objects.filter(currency=eur).exists()
