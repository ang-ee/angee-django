"""Units of measure: a category tree of convertible units.

A :class:`UomCategory` groups the units that measure one physical quantity
(weight, volume, time, temperature, …); a :class:`Uom` is one unit within a
category. Each unit records its size as an affine map onto the category's
*reference* unit: ``value_in_reference = qty * ratio + offset``. ``ratio`` is
the number of reference units contained in one of this unit (``12`` for a
dozen when the reference is a single unit, ``0.001`` for a gram when the
reference is a kilogram); ``offset`` is zero for ordinary multiplicative
units and carries the zero-point shift for interval scales (``273.15`` for
Celsius against a Kelvin reference). Exactly one unit per category is the
reference (``is_reference`` with ``ratio == 1`` and ``offset == 0``),
enforced by a partial unique constraint plus a check constraint.

Conversion is reference-neutral:
``(qty * self.ratio + self.offset - to_uom.offset) / to_uom.ratio``
re-expresses ``qty`` of this unit in the other unit of the same category (the
plain ratio quotient when both offsets are zero). The result is quantized to
the destination unit's ``rounding`` step with ``ROUND_HALF_UP`` — half rounds
away from zero. That mode is **this addon's stated policy** for quantity
rounding, deliberately fixed here rather than caller-supplied (unlike the
money/tax owners, whose amount rounding takes an explicit mode from company
policy). ``rounding`` is a decimal step and, because unit steps are powers of ten,
is read as a number of fractional digits by :func:`angee.base.numeric.quantize`.
"""

from __future__ import annotations

import decimal
from decimal import Decimal
from typing import Any

from django.db import models

from angee.base.mixins import ArchiveMixin, ArchiveQuerySet
from angee.base.models import AngeeDataModel, AngeeManager, AngeeQuerySet, role_anchor
from angee.base.numeric import quantize


class UomCategory(AngeeDataModel):
    """A family of units that measure the same quantity (weight, volume, time)."""

    runtime = True
    catalogue = True
    catalogue_tier = "master"
    sqid_prefix = "uoc_"

    name = models.CharField(max_length=128)

    class Meta:
        """Django model options for a unit-of-measure category."""

        abstract = True
        ordering = ("name",)
        rebac_resource_type = "uom/category"
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return the category name for Django displays."""

        return self.name


class UomQuerySet(ArchiveQuerySet[Any], AngeeQuerySet[Any]):
    """Archive read scopes layered over the REBAC-scoped unit queryset."""


UomManager = AngeeManager.from_queryset(UomQuerySet)


class Uom(ArchiveMixin, AngeeDataModel):
    """One unit within a category, mapped affinely onto the reference unit.

    ``value_in_reference = qty * ratio + offset``: ``ratio`` is the number of
    reference units in one of this unit, ``offset`` the zero-point shift for
    interval scales (temperature) and ``0`` everywhere else. The reference
    unit itself carries ``ratio == 1``, ``offset == 0`` and
    ``is_reference == True``; at most one reference exists per category (the
    partial unique constraint below) and its identity map is check-enforced.
    ``rounding`` is the decimal step conversions *into* this unit are quantized to.
    """

    runtime = True
    catalogue = True
    catalogue_tier = "master"
    sqid_prefix = "uom_"

    name = models.CharField(max_length=128)
    category = models.ForeignKey(
        "uom.UomCategory",
        on_delete=models.PROTECT,
        related_name="units",
    )
    ratio = models.DecimalField(max_digits=20, decimal_places=10, default=Decimal(1))
    offset = models.DecimalField(max_digits=20, decimal_places=10, default=Decimal(0))
    rounding = models.DecimalField(max_digits=12, decimal_places=6, default=Decimal("0.01"))
    is_reference = models.BooleanField(default=False)

    objects = UomManager()

    class Meta:
        """Django model options for a unit of measure."""

        abstract = True
        ordering = ("category", "name")
        rebac_resource_type = "uom/uom"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("category",),
                condition=models.Q(is_reference=True),
                name="%(app_label)s_uom_one_reference_per_category",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(is_reference=False)
                    | (models.Q(ratio=Decimal(1)) & models.Q(offset=Decimal(0)))
                ),
                name="%(app_label)s_uom_reference_is_identity",
            ),
        )

    def __str__(self) -> str:
        """Return the unit name for Django displays."""

        return self.name

    @property
    def rounding_places(self) -> int:
        """Return the fractional-digit count of this unit's ``rounding`` step.

        ``rounding`` is a decimal step (``0.001`` rounds to a milligram, ``1`` to a
        whole unit). :func:`angee.base.numeric.quantize` rounds to a number of
        places, so the step is read as its normalized scale — exact because unit
        steps are powers of ten (a non-power-of-ten step such as ``0.5`` is not
        representable this way; see the module docstring).
        """

        exponent = self.rounding.normalize().as_tuple().exponent
        return -exponent if isinstance(exponent, int) and exponent < 0 else 0

    def quantize(self, qty: Decimal) -> Decimal:
        """Return ``qty`` quantized to this unit's ``rounding`` step (``ROUND_HALF_UP``).

        The one place a quantity meets this unit's precision: :meth:`convert`
        quantizes its result through it, and a consumer comparing a remaining
        quantity against zero quantizes here first so sub-step dust (a converted
        counter that landed a hair off the entered quantity) never reads as a real
        remainder.
        """

        return quantize(qty, self.rounding_places, decimal.ROUND_HALF_UP)

    def convert(self, qty: Decimal, to_uom: Uom) -> Decimal:
        """Return ``qty`` of this unit expressed in ``to_uom`` (same category only).

        Both units map affinely onto their category's reference
        (``value_in_reference = qty * ratio + offset``), so the conversion goes
        through the reference and back:
        ``(qty * self.ratio + self.offset - to_uom.offset) / to_uom.ratio`` —
        the plain ratio quotient when both offsets are zero. The result is
        quantized to ``to_uom``'s ``rounding`` step via :meth:`quantize` (half
        away from zero) — quantity rounding is this addon's fixed policy, not a
        caller-supplied mode. Raises :class:`ValueError` across categories.
        """

        if self.category_id != to_uom.category_id:
            raise ValueError(
                f"cannot convert {self.name!r} to {to_uom.name!r}: "
                "units belong to different categories"
            )
        return to_uom.quantize((qty * self.ratio + self.offset - to_uom.offset) / to_uom.ratio)


UomRole = role_anchor("uom/role")
"""The ``uom/role`` anchor: its const ``admin`` arm resolves a platform admin as
an effective uom manager. See :func:`angee.base.models.role_anchor`.
"""
