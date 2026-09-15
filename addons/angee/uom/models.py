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
is read as a signed decimal-place position by :func:`angee.base.numeric.quantize`.
"""

from __future__ import annotations

import decimal
from decimal import Decimal
from typing import Any

from angee.base.mixins import (
    ArchiveMixin,
    ArchiveQuerySet,
    ConditionalSharedReaderMixin,
    ConditionalSharedReaderQuerySet,
)
from angee.base.models import AngeeDataModel, AngeeManager, AngeeQuerySet, role_anchor
from angee.base.numeric import quantize
from django.core.exceptions import ValidationError
from django.db import models, transaction


class UomCategoryQuerySet(
    ConditionalSharedReaderQuerySet[Any],
    AngeeQuerySet[Any],
):
    """Guard the conditional reader contract for unit categories."""


class UomCategoryManager(AngeeManager.from_queryset(UomCategoryQuerySet)):  # type: ignore[misc]
    """Own exact source-neutral category projection and correction."""

    def apply_reference_projection(
        self,
        expected: models.Model | None,
        prepared: models.Model,
    ) -> models.Model:
        """Create or compare-and-swap one unit category's canonical facts."""

        if not isinstance(prepared, self.model):
            raise ValidationError("Prepare a unit category from the composed uom model.")
        if expected is None and prepared.pk is not None:
            raise ValidationError("A new unit-category projection cannot reuse a saved identity.")
        with transaction.atomic(using=self.db):
            current = None
            if expected is not None:
                if not isinstance(expected, self.model) or expected.pk is None:
                    raise ValidationError(
                        "A unit-category correction requires its exact saved target."
                    )
                current = self.model.system_queryset(
                    using=self.db, lock=("self",)
                ).get(pk=expected.pk)
                if _category_projection_facts(current) != _category_projection_facts(expected):
                    raise ValidationError(
                        "The unit category changed before its projection was applied."
                    )
                prepared.pk = current.pk
                prepared._state.adding = False
                prepared._state.db = self.db
            prepared.validate_reference_projection_policy()
            if current is not None and _category_projection_facts(
                current
            ) == _category_projection_facts(prepared):
                return current
            prepared.save(
                using=self.db,
                update_fields=None if current is None else {"name", "updated_at"},
            )
            return prepared


class UomCategory(ConditionalSharedReaderMixin, AngeeDataModel):
    """A family of units that measure the same quantity (weight, volume, time)."""

    runtime = True
    catalogue = True
    catalogue_tier = "master"
    sqid_prefix = "uoc_"

    name = models.CharField(max_length=128)

    objects = UomCategoryManager()

    class Meta:
        """Django model options for a unit-of-measure category."""

        abstract = True
        ordering = ("name",)
        rebac_resource_type = "uom/category"
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return the category name for Django displays."""

        return self.name

    @property
    def shared_reader_eligible(self) -> bool:
        """Native catalogue categories are shared; source donors may narrow them."""

        return True

    def validate_reference_projection_policy(self) -> None:
        """Cooperate with a composed source owner before a projection write."""


def _category_projection_facts(row: models.Model) -> tuple[Any, ...]:
    """Return the exact native category facts guarded by projection CAS."""

    return (row.name,)


class UomQuerySet(
    ConditionalSharedReaderQuerySet[Any],
    ArchiveQuerySet[Any],
    AngeeQuerySet[Any],
):
    """Archive read scopes layered over the REBAC-scoped unit queryset."""


class UomManager(AngeeManager.from_queryset(UomQuerySet)):  # type: ignore[misc]
    """Own exact source-neutral unit projection and correction."""

    def apply_reference_projection(
        self,
        expected: models.Model | None,
        prepared: models.Model,
    ) -> models.Model:
        """Create or compare-and-swap one unit after locking its category."""

        if not isinstance(prepared, self.model):
            raise ValidationError("Prepare a unit from the composed uom model.")
        if expected is None and prepared.pk is not None:
            raise ValidationError("A new unit projection cannot reuse a saved identity.")
        if prepared.ratio <= 0 or prepared.rounding <= 0:
            raise ValidationError("Unit ratio and rounding must be positive.")
        normalized_rounding = prepared.rounding.normalize()
        if normalized_rounding.as_tuple().digits != (1,):
            raise ValidationError("Unit rounding must be a decimal power-of-ten step.")
        if prepared.is_reference and (
            prepared.ratio != Decimal(1) or prepared.offset != Decimal(0)
        ):
            raise ValidationError("A reference unit must be the category identity map.")
        with transaction.atomic(using=self.db):
            category = (
                self.model._meta.get_field("category")
                .related_model.system_queryset(using=self.db, lock=("self",))
                .filter(pk=prepared.category_id)
                .first()
            )
            if category is None:
                raise ValidationError("The unit category is unavailable.")
            current = None
            if expected is not None:
                if not isinstance(expected, self.model) or expected.pk is None:
                    raise ValidationError("A unit correction requires its exact saved target.")
                current = self.model.system_queryset(
                    using=self.db, lock=("self",)
                ).get(pk=expected.pk)
                if _uom_projection_facts(current) != _uom_projection_facts(expected):
                    raise ValidationError("The unit changed before its projection was applied.")
                if prepared.category_id != current.category_id:
                    raise ValidationError(
                        "A unit projection cannot move an existing unit between categories."
                    )
                prepared.pk = current.pk
                prepared._state.adding = False
                prepared._state.db = self.db
            prepared.category = category
            prepared.validate_reference_projection_policy(category=category)
            if current is not None and _uom_projection_facts(
                current
            ) == _uom_projection_facts(prepared):
                return current
            prepared.save(
                using=self.db,
                update_fields=(
                    None
                    if current is None
                    else {
                        "name",
                        "category",
                        "ratio",
                        "offset",
                        "rounding",
                        "is_reference",
                        "is_archived",
                        "updated_at",
                    }
                ),
            )
            return prepared


class Uom(ConditionalSharedReaderMixin, ArchiveMixin, AngeeDataModel):
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
    def shared_reader_eligible(self) -> bool:
        """Native catalogue units are shared; source donors may narrow them."""

        return True

    def validate_reference_projection_policy(self, *, category: models.Model) -> None:
        """Cooperate with a composed source owner after the canonical category lock."""

        del category

    @property
    def rounding_places(self) -> int:
        """Return the signed decimal-place position of this unit's ``rounding`` step.

        ``rounding`` is a decimal step (``0.001`` rounds to a milligram, ``1`` to a
        whole unit, and ``10`` rounds to tens). :func:`angee.base.numeric.quantize`
        accepts signed places, so the step is the inverse of its normalized
        exponent — exact because unit steps are powers of ten. A non-power-of-ten
        step such as ``0.5`` is not representable this way; see the module docstring.
        """

        exponent = self.rounding.normalize().as_tuple().exponent
        return -exponent if isinstance(exponent, int) else 0

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


def _uom_projection_facts(row: models.Model) -> tuple[Any, ...]:
    """Return the exact native unit facts guarded by projection CAS."""

    return (
        row.name,
        row.category_id,
        row.ratio,
        row.offset,
        row.rounding,
        row.is_reference,
        row.is_archived,
    )


UomRole = role_anchor("uom/role")
"""The ``uom/role`` anchor: its const ``admin`` arm resolves a platform admin as
an effective uom manager. See :func:`angee.base.models.role_anchor`.
"""
