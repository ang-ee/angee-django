"""Money: the currency catalogue, dated rates, and conversion.

A :class:`Currency` is one ISO-4217 currency in a shared catalogue; a
:class:`CurrencyRate` is one dated exchange rate for a currency expressed as
*units of that currency per one reference unit*. Global rows use
``ANGEE_MONEY_REFERENCE_CURRENCY`` — a **required project setting with no shipped
default** — while contextual rows carry their exact generic record context and
explicit reference Currency. Money bakes in no fiscal constant (a currency,
country, or locale is a project fact, never a framework one), so a USD default
was deliberately rejected in review. :func:`reference_currency_code` fails fast with
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

from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import DEFAULT_DB_ALIAS, models, transaction
from django.utils import timezone

from angee.base.mixins import (
    ArchiveMixin,
    ArchiveQuerySet,
    ConditionalSharedReaderMixin,
    ConditionalSharedReaderQuerySet,
)
from angee.base.models import AngeeDataModel, AngeeManager, AngeeQuerySet, role_anchor
from angee.base.numeric import quantize
from angee.base.refs import CanonicalRecordTarget, RecordRefMixin, canonical_record_model
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

    def convert(
        self,
        amount: Decimal,
        to_currency: Currency,
        on_date: Any = None,
        *,
        context: models.Model | None = None,
        reference_currency: Currency | None = None,
    ) -> Decimal:
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

        if (context is None) != (reference_currency is None):
            raise ValidationError(
                "Contextual currency conversion requires its explicit reference currency."
            )
        aliases = {
            alias
            for value in (self, to_currency, context, reference_currency)
            if value is not None
            for alias in (value._state.db,)
            if alias is not None
        }
        if len(aliases) > 1:
            raise ValidationError("Currency conversion cannot span databases.")
        alias = aliases.pop() if aliases else DEFAULT_DB_ALIAS
        rates = apps.get_model(self._meta.app_label, "CurrencyRate").objects.db_manager(alias)
        if context is not None:
            rates.validate_context_reference(context, reference_currency)
        canonical_source, canonical_target = rates._canonical_currencies(
            self,
            to_currency,
            using=alias,
            lock=False,
        )
        if canonical_source.pk == canonical_target.pk:
            return amount
        return amount * rates.rate_for(
            canonical_target,
            on_date,
            context=context,
            reference_currency=reference_currency,
        ) / rates.rate_for(
            canonical_source,
            on_date,
            context=context,
            reference_currency=reference_currency,
        )


class CurrencyRateQuerySet(
    ConditionalSharedReaderQuerySet[Any],
    ArchiveQuerySet[Any],
    AngeeQuerySet[Any],
):
    """Protect contextual identity fields that also govern rate visibility."""

    _identity_fields = {
        "currency",
        "currency_id",
        "date",
        "context_content_type",
        "context_content_type_id",
        "context_object_id",
        "reference_currency",
        "reference_currency_id",
        "rate",
        "source_priority",
    }

    def update(self, **kwargs: Any) -> int:
        if self._identity_fields & set(kwargs):
            raise ValidationError("Currency-rate identity changes through its native owner.")
        return super().update(**kwargs)

    def bulk_update(
        self,
        objs: Any,
        fields: Any,
        batch_size: int | None = None,
    ) -> int:
        rows, names = list(objs), tuple(fields)
        if self._identity_fields & set(names):
            raise ValidationError("Currency-rate identity changes through its native owner.")
        return super().bulk_update(rows, names, batch_size=batch_size)


class CurrencyRateManager(AngeeManager.from_queryset(CurrencyRateQuerySet)):  # type: ignore[misc]
    """Resolves the effective exchange rate for a currency on a date."""

    def rate_for(
        self,
        currency: models.Model,
        on_date: Any = None,
        *,
        context: models.Model | None = None,
        reference_currency: models.Model | None = None,
    ) -> Decimal:
        """Return the latest rate for ``currency`` dated on or before ``on_date``.

        ``Decimal(1)`` for the reference currency itself (no row needed — it is the
        unit every other rate is quoted against). Global history uses the most
        recent row with ``date <= on_date``. Contextual history first selects the
        greatest source priority, then its most recent effective date. Fails fast
        with :class:`CurrencyRate.DoesNotExist` when no eligible rate exists — a
        missing rate is a data gap the caller must see, never a silent zero.
        """

        if (context is None) != (reference_currency is None):
            raise ValidationError(
                "Contextual currency conversion requires its explicit reference currency."
            )
        canonical_currency, canonical_reference = self._canonical_currencies(
            currency,
            reference_currency,
            using=self.db,
            lock=False,
        )
        context_ref = None
        if context is not None:
            context_ref = self._canonical_context(context, using=self.db, lock=False)[0]
        code = (
            reference_currency_code()
            if reference_currency is None
            else str(canonical_reference.code)
        )
        if not code:
            raise ValidationError("The contextual reference currency is invalid.")
        if canonical_currency.code == code:
            return Decimal(1)
        draw_date = on_date or timezone.localdate()
        rates = self.filter(
            currency=canonical_currency,
            date__lte=draw_date,
            is_archived=False,
        )
        if context is None:
            rates = rates.filter(
                context_content_type__isnull=True,
                context_object_id="",
                reference_currency__isnull=True,
            )
        else:
            assert context_ref is not None
            rates = rates.filter(
                context_content_type=context_ref.content_type,
                context_object_id=str(context_ref.object_id),
                reference_currency=canonical_reference,
            )
        ordering = (
            ("-date",)
            if context is None
            else ("-source_priority", "-date")
        )
        rate = rates.order_by(*ordering).values_list("rate", flat=True).first()
        if rate is None:
            raise self.model.DoesNotExist(
                f"No {code}-relative rate for {getattr(currency, 'code', currency)} on or before {draw_date}."
            )
        return rate

    def validate_context_reference(
        self,
        context: models.Model,
        reference_currency: models.Model,
    ) -> Any:
        """Return one saved same-database canonical context/reference identity."""

        if context is None or reference_currency is None:
            raise ValidationError("Contextual currency conversion requires saved canonical owners.")
        context_ref, _locked = self._canonical_context(
            context,
            using=self.db,
            lock=False,
        )
        self._canonical_currencies(
            reference_currency,
            reference_currency,
            using=self.db,
            lock=False,
        )
        return context_ref

    def apply_contextual_projection(
        self,
        expected: models.Model | None,
        prepared: models.Model,
        *,
        context: models.Model,
        reference_currency: models.Model,
    ) -> models.Model:
        """Create or exactly correct one company/context-specific effective rate."""

        if not isinstance(prepared, self.model) or prepared.pk is not None:
            raise ValidationError("Prepare one unsaved composed currency-rate projection.")
        if (
            prepared.context_content_type_id is not None
            or prepared.context_object_id
            or prepared.reference_currency_id is not None
        ):
            raise ValidationError("The currency-rate manager owns contextual identity fields.")
        alias = self.db
        if alias != DEFAULT_DB_ALIAS:
            raise ValidationError(
                "Contextual currency-rate writes require the default authorization database."
            )
        with transaction.atomic(using=alias):
            canonical_context, locked_context = self._canonical_context(
                context,
                using=alias,
                lock=True,
            )
            currency, reference = self._canonical_currencies(
                prepared.currency,
                reference_currency,
                using=alias,
                lock=True,
            )
            prepared.date = prepared._meta.get_field("date").to_python(prepared.date)
            prepared.rate = prepared._meta.get_field("rate").to_python(prepared.rate)
            prepared.source_priority = prepared._meta.get_field(
                "source_priority"
            ).to_python(prepared.source_priority)
            if prepared.date is None or prepared.rate is None or not prepared.rate.is_finite() or prepared.rate <= 0:
                raise ValidationError("A contextual currency rate requires a date and positive finite rate.")
            if (
                isinstance(prepared.source_priority, bool)
                or prepared.source_priority is None
                or prepared.source_priority < 0
            ):
                raise ValidationError(
                    "A contextual currency rate requires a non-negative source priority."
                )
            _require_exact_rate_decimal(prepared)
            current = None
            if expected is not None:
                if (
                    not isinstance(expected, self.model)
                    or expected.pk is None
                    or expected._state.adding
                    or expected._state.db != alias
                ):
                    raise ValidationError(
                        "A contextual currency-rate correction requires its exact saved target."
                    )
                current = self.model.system_queryset(
                    using=alias,
                    lock=("self",),
                ).get(pk=expected.pk)
                if _currency_rate_projection_facts(current) != _currency_rate_projection_facts(
                    expected
                ):
                    raise ValidationError(
                        "The contextual currency rate changed before its projection was applied."
                    )
                if (
                    current.currency_id != currency.pk
                    or current.date != prepared.date
                    or current.context_content_type_id != canonical_context.content_type.pk
                    or current.context_object_id != str(canonical_context.object_id)
                    or current.reference_currency_id != reference.pk
                ):
                    raise ValidationError("A contextual currency-rate identity is immutable.")
                prepared.pk = current.pk
                prepared._state.adding = False
                prepared._state.db = alias
            prepared.currency = currency
            prepared.context_content_type = canonical_context.content_type
            prepared.context_object_id = str(canonical_context.object_id)
            prepared.reference_currency = reference
            prepared.is_archived = False
            prepared.validate_contextual_projection_policy(
                context=locked_context,
                currency=currency,
                reference_currency=reference,
            )
            if current is not None and _currency_rate_projection_facts(
                current
            ) == _currency_rate_projection_facts(prepared):
                return current
            prepared.save(
                using=alias,
                update_fields=(
                    None
                    if current is None
                    else {"rate", "source_priority", "is_archived", "updated_at"}
                ),
            )
            return prepared

    def withdraw_contextual_projection(
        self,
        expected: models.Model,
        *,
        context: models.Model,
        reference_currency: models.Model,
    ) -> models.Model:
        """Deactivate one exact contextual rate without exposing a global fallback."""

        if (
            not isinstance(expected, self.model)
            or expected.pk is None
            or expected._state.adding
            or expected._state.db != self.db
        ):
            raise ValidationError("Withdraw an exact saved contextual currency rate.")
        alias = self.db
        if alias != DEFAULT_DB_ALIAS:
            raise ValidationError(
                "Contextual currency-rate writes require the default authorization database."
            )
        with transaction.atomic(using=alias):
            canonical_context, locked_context = self._canonical_context(
                context,
                using=alias,
                lock=True,
            )
            _currency, reference = self._canonical_currencies(
                expected.currency,
                reference_currency,
                using=alias,
                lock=True,
            )
            current = self.model.system_queryset(
                using=alias,
                lock=("self",),
            ).get(pk=expected.pk)
            if _currency_rate_projection_facts(current) != _currency_rate_projection_facts(
                expected
            ):
                raise ValidationError(
                    "The contextual currency rate changed before its withdrawal."
                )
            if (
                current.context_content_type_id != canonical_context.content_type.pk
                or current.context_object_id != str(canonical_context.object_id)
                or current.reference_currency_id != reference.pk
            ):
                raise ValidationError("The contextual currency-rate withdrawal identity is invalid.")
            current.validate_contextual_projection_policy(
                context=locked_context,
                currency=current.currency,
                reference_currency=reference,
            )
            if not current.is_archived:
                current.is_archived = True
                current.save(using=alias, update_fields={"is_archived", "updated_at"})
            return current

    @staticmethod
    def _canonical_context(
        context: models.Model,
        *,
        using: str,
        lock: bool,
    ) -> tuple[Any, models.Model]:
        if context.pk is None or context._state.db != using:
            raise ValidationError("A contextual currency rate requires one saved context.")
        model = canonical_record_model(type(context))
        if not hasattr(model, "system_queryset"):
            raise ValidationError("The currency-rate context is not a canonical resource.")
        content_type = ContentType.objects.db_manager(using).get_for_model(model)
        canonical = CanonicalRecordTarget(content_type, context.pk)
        locked = model.system_queryset(
            using=using,
            lock=("self",) if lock else None,
        ).filter(
            pk=canonical.object_id
        ).first()
        if locked is None or locked.pk != canonical.object_id:
            raise ValidationError("The currency-rate context is unavailable.")
        return canonical, locked

    def _canonical_currencies(
        self,
        currency: models.Model,
        reference_currency: models.Model | None,
        *,
        using: str,
        lock: bool,
    ) -> tuple[models.Model, models.Model]:
        if currency is None or currency.pk is None or currency._state.db != using:
            raise ValidationError("Currency-rate reads require saved canonical currencies.")
        declared_model = self.model._meta.get_field("currency").related_model
        if not isinstance(currency, declared_model):
            raise ValidationError("Currency-rate reads require the canonical Currency model.")
        if reference_currency is not None and (
            not isinstance(reference_currency, declared_model)
            or reference_currency.pk is None
            or reference_currency._state.db != using
        ):
            raise ValidationError("Contextual currency rates require canonical currencies.")
        ids = {currency.pk} | (
            set() if reference_currency is None else {reference_currency.pk}
        )
        rows = {
            row.pk: row
            for row in declared_model.system_queryset(
                using=using,
                lock=("self",) if lock else None,
            )
            .filter(pk__in=ids)
            .order_by("pk")
        }
        if set(rows) != ids:
            raise ValidationError("A contextual currency is unavailable.")
        return (
            rows[currency.pk],
            rows[currency.pk]
            if reference_currency is None
            else rows[reference_currency.pk],
        )


class CurrencyRate(
    RecordRefMixin,
    ConditionalSharedReaderMixin,
    ArchiveMixin,
    AngeeDataModel,
):
    """One dated global or exact-context exchange rate per reference unit.

    A global row has no context or explicit reference and remains relative to
    ``ANGEE_MONEY_REFERENCE_CURRENCY``. A contextual row instead carries a
    canonical generic record identity plus its explicit reference Currency and
    source priority; contextual reads never fall back to a global or
    different-context row.
    """

    runtime = True
    sqid_prefix = "crt_"
    record_ref_field_prefix = "context"

    currency = models.ForeignKey(
        "money.Currency",
        on_delete=models.CASCADE,
        related_name="rates",
    )
    date = models.DateField()
    rate = models.DecimalField(max_digits=38, decimal_places=20)
    context_content_type = models.ForeignKey(
        ContentType,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.PROTECT,
        related_name="+",
    )
    context_object_id = models.CharField(max_length=255, blank=True, editable=False)
    context = GenericForeignKey("context_content_type", "context_object_id")
    reference_currency = models.ForeignKey(
        "money.Currency",
        null=True,
        blank=True,
        editable=False,
        on_delete=models.PROTECT,
        related_name="contextual_rates",
    )
    source_priority = models.PositiveSmallIntegerField(default=0, editable=False)
    shared_reader_policy_fields = (
        "context_content_type",
        "context_object_id",
        "reference_currency",
    )

    objects = CurrencyRateManager()

    class Meta:
        """Django model options for a currency rate."""

        abstract = True
        ordering = ("currency", "-date")
        rebac_resource_type = "money/rate"
        constraints = (
            models.CheckConstraint(
                condition=(
                    models.Q(
                        context_content_type__isnull=True,
                        context_object_id="",
                        reference_currency__isnull=True,
                    )
                    | (
                        models.Q(
                            context_content_type__isnull=False,
                            reference_currency__isnull=False,
                        )
                        & ~models.Q(context_object_id="")
                    )
                ),
                name="%(app_label)s_rate_context_complete",
            ),
            models.CheckConstraint(
                condition=models.Q(rate__gt=0),
                name="%(app_label)s_rate_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(source_priority__gte=0),
                name="%(app_label)s_rate_source_priority",
            ),
            models.UniqueConstraint(
                fields=("currency", "date"),
                condition=models.Q(context_content_type__isnull=True),
                name="%(app_label)s_rate_currency_date",
            ),
            models.UniqueConstraint(
                fields=(
                    "currency",
                    "date",
                    "context_content_type",
                    "context_object_id",
                ),
                condition=models.Q(context_content_type__isnull=False),
                name="%(app_label)s_rate_context_currency_date",
            ),
        )

    @property
    def shared_reader_eligible(self) -> bool:
        """Only native global configured-reference rates receive a wildcard reader."""

        return (
            self.context_content_type_id is None
            and not self.context_object_id
            and self.reference_currency_id is None
        )

    def validate_contextual_projection_policy(
        self,
        *,
        context: models.Model,
        currency: models.Model,
        reference_currency: models.Model,
    ) -> None:
        """Cooperate with composed source policy after canonical rebinding."""

        del context, currency, reference_currency

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Keep every rate slot identity immutable after its first persistence."""

        alias = kwargs.get("using") or self._state.db or DEFAULT_DB_ALIAS
        with transaction.atomic(using=alias):
            self.date = self._meta.get_field("date").to_python(self.date)
            self.rate = self._meta.get_field("rate").to_python(self.rate)
            self.source_priority = self._meta.get_field("source_priority").to_python(
                self.source_priority
            )
            if (
                self.date is None
                or self.rate is None
                or not self.rate.is_finite()
                or self.rate <= 0
            ):
                raise ValidationError(
                    "A currency rate requires a date and positive finite rate."
                )
            if (
                isinstance(self.source_priority, bool)
                or self.source_priority is None
                or self.source_priority < 0
            ):
                raise ValidationError(
                    "A currency rate requires a non-negative source priority."
                )
            _require_exact_rate_decimal(self)
            if self.pk is not None:
                previous = type(self).system_queryset(
                    using=alias,
                    lock=("self",),
                ).filter(pk=self.pk).first()
                if previous is not None:
                    identity = (
                        "currency_id",
                        "date",
                        "context_content_type_id",
                        "context_object_id",
                        "reference_currency_id",
                    )
                    update_fields = kwargs.get("update_fields")
                    written = (
                        set(identity)
                        if update_fields is None
                        else {str(value) for value in update_fields}
                    )
                    for name in identity:
                        field = next(
                            field
                            for field in self._meta.fields
                            if name in {field.name, field.attname}
                        )
                        is_written = field.name in written or field.attname in written
                        if is_written and getattr(self, field.attname) != getattr(
                            previous, field.attname
                        ):
                            raise ValidationError("Currency-rate identity is immutable.")
                        if not is_written:
                            setattr(self, field.attname, getattr(previous, field.attname))
            kwargs["using"] = alias
            super().save(*args, **kwargs)

    def __str__(self) -> str:
        """Return a readable label for Django displays."""

        return f"{self.currency_id}@{self.date}={self.rate}"


def _currency_rate_projection_facts(row: models.Model) -> tuple[Any, ...]:
    """Return every native fact participating in contextual-rate CAS."""

    return (
        row.currency_id,
        row.date,
        row.rate,
        row.context_content_type_id,
        row.context_object_id,
        row.reference_currency_id,
        row.source_priority,
        row.is_archived,
    )


def _require_exact_rate_decimal(row: models.Model) -> None:
    """Reject a rate that the owning DecimalField would silently round or clamp."""

    field = row._meta.get_field("rate")
    quantum = Decimal(1).scaleb(-int(field.decimal_places))
    try:
        with localcontext() as context:
            context.prec = int(field.max_digits) + 2
            quantized = row.rate.quantize(quantum)
    except InvalidOperation as error:
        raise ValidationError("The currency-rate value exceeds native precision.") from error
    integer_digits = max(0, quantized.adjusted() + 1)
    if quantized != row.rate or integer_digits > int(field.max_digits) - int(field.decimal_places):
        raise ValidationError("The currency-rate value is not exactly representable.")


MoneyRole = role_anchor("money/role")
"""The ``money/role`` anchor: its const ``admin`` arm resolves a platform admin as
an effective money manager. See :func:`angee.base.models.role_anchor`.
"""
