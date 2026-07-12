"""Money: the currency catalogue, dated rates, and conversion.

A :class:`Currency` is one ISO-4217 currency in a shared catalogue; a
:class:`CurrencyRate` is one dated exchange rate for a currency expressed as
*units of that currency per one reference unit*. The reference is
``ANGEE_MONEY_REFERENCE_CURRENCY`` — a **required project setting with no shipped
default**: money bakes in no fiscal constant (a currency, country, or locale is a
project fact, never a framework one), so a USD default was deliberately rejected
in review. :func:`reference_currency_code` fails fast with
:class:`~django.core.exceptions.ImproperlyConfigured` naming the setting when it
is unset, and the addon's system check (``apps.py``) surfaces the same at
``manage.py check`` time.

Rounding vocabulary lives here too. :meth:`Currency.round` wraps
:func:`angee.base.numeric.quantize` to the currency's exponent and resolves the
mode from :class:`angee.money.rounding.RoundingMode`, defaulting to ``half_up``
unless a caller explicitly overrides it. :meth:`Currency.convert` crosses through
the reference currency and returns the amount **unrounded** — the consumer rounds
the converted amount at the point that owns the business policy.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.utils import timezone

from angee.base.mixins import ArchiveMixin, ArchiveQuerySet
from angee.base.models import AngeeDataModel, AngeeManager, AngeeQuerySet, role_anchor
from angee.base.numeric import quantize
from angee.money.rounding import RoundingMode, rounding_constant

REFERENCE_CURRENCY_SETTING = "ANGEE_MONEY_REFERENCE_CURRENCY"
"""The project setting naming the ISO-4217 code every :class:`CurrencyRate` is relative to."""


def reference_currency_code() -> str:
    """Return the configured reference currency code, or fail fast.

    The single owner of "which currency all rates are relative to" — read by the
    rate manager and the addon's system check alike. Raises
    :class:`~django.core.exceptions.ImproperlyConfigured` naming
    :data:`REFERENCE_CURRENCY_SETTING` when it is unset, because money ships no
    default (the project owns this choice).
    """

    code = getattr(settings, REFERENCE_CURRENCY_SETTING, None)
    if not code:
        raise ImproperlyConfigured(
            f"{REFERENCE_CURRENCY_SETTING} is required for currency conversion but is not set. "
            "It names the ISO-4217 code all CurrencyRate rows are relative to; money ships no "
            "default (a currency is a project fact, not a framework one)."
        )
    return str(code)


class CurrencyQuerySet(ArchiveQuerySet[Any], AngeeQuerySet[Any]):
    """Archive read scopes layered over the REBAC-scoped currency queryset."""


CurrencyManager = AngeeManager.from_queryset(CurrencyQuerySet)


class Currency(ArchiveMixin, AngeeDataModel):
    """One ISO-4217 currency: its code, display name, symbol, and minor-unit exponent.

    ``decimal_places`` is the currency's minor-unit exponent (2 for most, 0 for
    JPY/KRW, 3 for the Gulf dinars). :meth:`round` quantizes to it with the
    money-owned default mode unless a caller passes an explicit override.
    """

    runtime = True
    catalogue = True
    catalogue_tier = "master"
    sqid_prefix = "cur_"

    code = models.CharField(max_length=3, unique=True)
    name = models.CharField(max_length=128)
    symbol = models.CharField(max_length=8, blank=True, default="")
    decimal_places = models.PositiveSmallIntegerField(default=2)

    objects = CurrencyManager()

    class Meta:
        """Django model options for a currency."""

        abstract = True
        ordering = ("code",)
        rebac_resource_type = "money/currency"
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return the ISO-4217 code for Django displays."""

        return self.code

    def round(self, amount: Decimal, mode: RoundingMode | str | None = None) -> Decimal:
        """Return ``amount`` quantized to this currency's exponent.

        ``mode`` may be a :class:`angee.money.rounding.RoundingMode` value or its
        stored string value. When omitted, the money addon's default rounding
        vocabulary applies.
        """

        return quantize(amount, self.decimal_places, rounding_constant(mode))

    def convert(self, amount: Decimal, to_currency: Currency, on_date: Any = None) -> Decimal:
        """Return ``amount`` re-expressed in ``to_currency`` at ``on_date`` rates.

        Identity is a fast-path (same currency returns the amount untouched).
        Otherwise the amount crosses through the reference currency: rates are
        *units per one reference unit*, so ``amount`` in this currency is worth
        ``amount / rate_for(self)`` reference units, and
        ``amount * rate_for(to_currency) / rate_for(self)`` in the target. The
        result is **not rounded** — the consumer rounds per its own policy
        (:meth:`round` with its chosen mode). Raises
        :class:`CurrencyRate.DoesNotExist` when a needed rate is missing and
        :class:`~django.core.exceptions.ImproperlyConfigured` when the reference
        currency setting is unset.
        """

        if self.code == to_currency.code:
            return amount
        rates = apps.get_model(self._meta.app_label, "CurrencyRate").objects
        return amount * rates.rate_for(to_currency, on_date) / rates.rate_for(self, on_date)


class CurrencyRateManager(AngeeManager):
    """Resolves the effective exchange rate for a currency on a date."""

    def rate_for(self, currency: models.Model, on_date: Any = None) -> Decimal:
        """Return the latest rate for ``currency`` dated on or before ``on_date``.

        ``Decimal(1)`` for the reference currency itself (no row needed — it is the
        unit every other rate is quoted against). Otherwise the most recent
        :class:`CurrencyRate` with ``date <= on_date`` (today when ``on_date`` is
        omitted). Fails fast with :class:`CurrencyRate.DoesNotExist` when no rate
        exists on or before the date — a missing rate is a data gap the caller must
        see, never a silent zero.
        """

        code = reference_currency_code()
        if getattr(currency, "code", None) == code:
            return Decimal(1)
        draw_date = on_date or timezone.localdate()
        rate = (
            self.filter(currency=currency, date__lte=draw_date)
            .order_by("-date")
            .values_list("rate", flat=True)
            .first()
        )
        if rate is None:
            raise self.model.DoesNotExist(
                f"No {code}-relative rate for {getattr(currency, 'code', currency)} on or before {draw_date}."
            )
        return rate


class CurrencyRate(AngeeDataModel):
    """One dated exchange rate for a currency, quoted per one reference unit.

    ``rate`` is *units of ``currency`` per one ``ANGEE_MONEY_REFERENCE_CURRENCY``
    unit* on ``date``; the ``(currency, date)`` pair is unique. Rows are global
    (not per-company) in v1 — per-company rates arrive later as an additive
    ``extends`` merge.
    """

    runtime = True
    sqid_prefix = "crt_"

    currency = models.ForeignKey(
        "money.Currency",
        on_delete=models.CASCADE,
        related_name="rates",
    )
    date = models.DateField()
    rate = models.DecimalField(max_digits=20, decimal_places=10)

    objects = CurrencyRateManager()

    class Meta:
        """Django model options for a currency rate."""

        abstract = True
        ordering = ("currency", "-date")
        rebac_resource_type = "money/rate"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("currency", "date"),
                name="%(app_label)s_rate_currency_date",
            ),
        )

    def __str__(self) -> str:
        """Return a readable label for Django displays."""

        return f"{self.currency_id}@{self.date}={self.rate}"


MoneyRole = role_anchor("money/role")
"""The ``money/role`` anchor: its const ``admin`` arm resolves a platform admin as
an effective money manager. See :func:`angee.base.models.role_anchor`.
"""
