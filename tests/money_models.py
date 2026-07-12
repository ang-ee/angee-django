"""Concrete money models used by the bare source-addon test harness.

``angee.money`` ships abstract source models (the composer materializes them in
a real project), so these concrete twins register them under the ``money`` app
label for tests: ``Currency``/``CurrencyRate`` back the runtime behavior tests
(``tests/test_money.py``), and the ``MoneyField``-composing documents pin the
field contract (``tests/test_money_field.py``) in both currency-path shapes — a
sibling FK and a one-hop related path. Tables are created on demand by
``_create_missing_tables``; the field-contract documents never need one.
"""

from __future__ import annotations

from django.db import models

from angee.money.fields import MoneyField
from angee.money.models import Currency as AbstractCurrency
from angee.money.models import CurrencyRate as AbstractCurrencyRate


class Currency(AbstractCurrency):
    """Concrete currency — the ``money.Currency`` target ``MoneyField`` validates."""

    class Meta(AbstractCurrency.Meta):
        """Django model options for the canonical test currency."""

        abstract = False
        app_label = "money"
        db_table = "test_money_currency"
        rebac_resource_type = "money/currency"
        rebac_id_attr = "sqid"


class CurrencyRate(AbstractCurrencyRate):
    """Concrete dated exchange rate used by the conversion tests."""

    class Meta(AbstractCurrencyRate.Meta):
        """Django model options for the canonical test currency rate."""

        abstract = False
        app_label = "money"
        db_table = "test_money_rate"
        rebac_resource_type = "money/rate"
        rebac_id_attr = "sqid"


MONEY_TEST_MODELS = (Currency, CurrencyRate)
"""Concrete money models created on demand by money test fixtures."""


class MoneyOrder(models.Model):
    """A parent document owning the currency for its lines (one-hop path)."""

    currency = models.ForeignKey(Currency, on_delete=models.CASCADE)

    class Meta:
        """Register under the ``money`` app label."""

        app_label = "money"


class MoneyDocument(models.Model):
    """A document whose amount is denominated by a sibling ``currency`` FK."""

    currency = models.ForeignKey(Currency, on_delete=models.CASCADE)
    amount = MoneyField(currency_field="currency")

    class Meta:
        """Register under the ``money`` app label."""

        app_label = "money"


class MoneyLine(models.Model):
    """A line whose price currency lives one hop away on its ``order``."""

    order = models.ForeignKey(MoneyOrder, on_delete=models.CASCADE)
    price = MoneyField(currency_field="order.currency")

    class Meta:
        """Register under the ``money`` app label."""

        app_label = "money"


class MoneyStatement(models.Model):
    """A statement whose currency lives one hop away through a OneToOne parent link.

    ``order`` is a ``OneToOneField`` (a legitimate parent link), so the one-hop
    ``order.currency`` path must resolve — a ``OneToOneField`` is a foreign-key
    subclass, not a non-relation.
    """

    order = models.OneToOneField(MoneyOrder, on_delete=models.CASCADE)
    total = MoneyField(currency_field="order.currency")

    class Meta:
        """Register under the ``money`` app label."""

        app_label = "money"
