"""Concrete stand-in models for ``MoneyField`` tests.

The framework core ships ``MoneyField`` but not the ``angee.money`` addon (that
incubates in arpee), so these bare models stand in for the currency addon: a
``Currency`` under app label ``money`` — the ``"money.Currency"`` label
``MoneyField.check`` resolves against — plus documents carrying a ``MoneyField``
in both currency-path shapes (a sibling FK and a one-hop related path). They are
plain models under an app that is not installed: nothing migrates them and no
row is ever created, so they serve field ``check``/``deconstruct``/metadata
introspection without a database table.
"""

from __future__ import annotations

from django.db import models

from angee.base.fields import MoneyField


class Currency(models.Model):
    """Stand-in for ``money.Currency`` — the FK target ``MoneyField`` validates."""

    code = models.CharField(max_length=3, unique=True)

    class Meta:
        """Register under the ``money`` app label the field couples to."""

        app_label = "money"


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
