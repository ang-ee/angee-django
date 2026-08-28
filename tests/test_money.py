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
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.test import override_settings
from rebac import system_context

from angee.money.rounding import RoundingMode
from tests.conftest import _clear_model_tables, _create_missing_tables
from tests.money_models import MONEY_TEST_MODELS, Currency, CurrencyRate


@pytest.fixture()
def money_tables(transactional_db: Any) -> Iterator[None]:
    """Create the concrete money tables for the duration of one test."""

    del transactional_db
    created_models = _create_missing_tables(MONEY_TEST_MODELS)
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
