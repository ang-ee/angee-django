"""Finite fractional ranks with contextual native database allocation."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, cast

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models, transaction
from django.db.backends.base.base import BaseDatabaseWrapper
from django.utils.hashable import make_hashable


class FractionalRankExhausted(ValidationError, ValueError):
    """Raised when binary64 cannot represent another safe fractional rank."""


@dataclass(frozen=True, slots=True)
class _PendingAppendRank:
    """Carry an omitted rank to the native connection-aware save hook."""

    instance: models.Model


class FractionalRankField(models.FloatField):
    """Finite float rank for manual ordering inside a model-defined context.

    The field owns finite ranks and allocation after the current last row. The consumer
    model owns the context columns and their database uniqueness constraint;
    the field cannot infer whether a list is scoped by a lane, parent, project,
    or another domain fact.

    Adding the column backfills pre-existing rows with ``STEP``. Those equal
    ranks are valid only when the rows share no non-null ordering group under
    the model's uniqueness constraint; grouped rows need a contextual backfill.
    """

    STEP = 1024.0
    angee_widget = "float"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Default ranks to indexed because ordered contexts query by them."""

        kwargs.setdefault("db_index", True)
        kwargs.setdefault("db_default", self.STEP)
        super().__init__(*args, **kwargs)

    def deconstruct(self) -> tuple[str, str, Sequence[Any], dict[str, Any]]:
        """Omit constructor defaults and preserve explicit index/backfill opt-outs."""

        name, path, args, kwargs = super().deconstruct()
        for key, default in (("db_index", True), ("db_default", self.STEP)):
            value = getattr(self, key)
            if value == default:
                kwargs.pop(key, None)
            else:
                kwargs[key] = value
        return name, path, args, kwargs

    def has_default(self) -> bool:
        """Expose field-owned append allocation as a server-side default.

        Hasura input generation uses Django's field-owned ``has_default`` fact
        to decide whether an insert column may be omitted.  A fractional rank
        has no context-free literal default; native save preparation supplies
        its contextual append rank on the actual write database.
        """

        return True

    def get_default(self) -> None:
        """Leave omitted ranks for connection-aware allocation at save time."""

        return None

    def validate(self, value: Any, model_instance: models.Model | None) -> None:
        """Allow the omitted ``None`` allocated by the native save-preparation hooks."""

        if value is None:
            return
        super().validate(value, model_instance)

    def pre_save(self, model_instance: models.Model, add: bool) -> float | _PendingAppendRank:
        """Honor explicit ranks; defer omitted allocation until the write DB is known."""

        value = super().pre_save(model_instance, add)
        return _PendingAppendRank(model_instance) if value is None else cast(float, value)

    def get_db_prep_save(self, value: Any, connection: BaseDatabaseWrapper) -> Any:
        """Allocate omitted ranks on the compiler's actual database connection."""

        if isinstance(value, _PendingAppendRank):
            instance = value.instance
            value = self._get_append_rank_for_instance(instance, using=connection.alias)
            setattr(instance, self.attname, value)
        return super().get_db_prep_save(value, connection)

    def _get_append_rank_for_instance(self, instance: models.Model, *, using: str) -> float:
        """Read the next rank within the instance's context on its write database."""

        model = type(instance)
        context = self._context_values(instance)
        previous = (
            model._base_manager.all()
            .using(using)
            .filter(**context)
            .order_by(f"-{self.name}")
            .values_list(self.name, flat=True)
            .first()
        )
        return self.get_append_rank(previous)

    def _context_values(self, instance: models.Model) -> dict[str, Any]:
        """Read this field's declared unique context from one row."""

        return {
            field.attname: getattr(instance, field.attname)
            for field in self._unique_context_fields(self.model)
        }

    def allocate_missing_ranks(self, instances: Sequence[models.Model], *, using: str) -> None:
        """Allocate omitted ranks before a native multirow INSERT under its owner's lock.

        One INSERT cannot see its own earlier rows during field preparation.
        Read each declared context once and include explicit batch destinations,
        then allocate only omitted values in input order. Literal ranks stay exact.
        An unsaved related context is new, distinct from persisted NULL: local
        object identity groups its rows without becoming any emitted value.
        """

        groups: dict[tuple[Any, ...], list[models.Model]] = {}
        context_fields = self._unique_context_fields(self.model)
        for instance in instances:
            pending = []
            for field in context_fields:
                related = field.get_cached_value(instance, None) if isinstance(field, models.ForeignKey) else None
                pending.append(id(related) if related is not None and related.pk is None else None)
            key = (make_hashable(tuple(self._context_values(instance).values())), tuple(pending))
            groups.setdefault(key, []).append(instance)
        for (_context, pending), rows in groups.items():
            omitted = [row for row in rows if getattr(row, self.attname) is None]
            if not omitted:
                continue
            previous = (
                None if any(pending) else
                self.model._base_manager.using(using).filter(**self._context_values(rows[0]))
                .order_by(f"-{self.name}").values_list(self.name, flat=True).first()
            )
            for row in rows:
                rank = self.to_python(getattr(row, self.attname))
                if rank is not None and (previous is None or rank > previous):
                    previous = rank
            for row in omitted:
                previous = self.get_append_rank(previous)
                setattr(row, self.attname, previous)

    def _unique_context_fields(
        self,
        model: type[models.Model],
    ) -> tuple[models.Field[Any, Any], ...]:
        """Resolve the one field-based unique context declaring this rank."""

        contexts = [
            tuple(name for name in constraint.fields if name != self.name)
            for constraint in model._meta.constraints
            if isinstance(constraint, models.UniqueConstraint)
            and self.name in constraint.fields
        ]
        if len(contexts) != 1:
            raise ImproperlyConfigured(
                f"{model._meta.label}.{self.name} must belong to exactly one "
                "field-based UniqueConstraint so a missing rank can be allocated."
            )
        fields = []
        for name in contexts[0]:
            field = model._meta.get_field(name)
            if not isinstance(field, models.Field):
                raise ImproperlyConfigured(f"{model._meta.label}.{name} is not a concrete rank context field.")
            fields.append(field)
        return tuple(fields)

    def to_python(self, value: Any) -> float | None:
        """Coerce a rank and reject NaN or infinity at validation boundaries."""

        rank = super().to_python(value)
        if rank is not None and not math.isfinite(rank):
            raise ValidationError("Fractional ranks must be finite.", code="non_finite_rank")
        return rank

    def get_prep_value(self, value: Any) -> float | None:
        """Reject non-finite ranks on every ORM write path."""

        rank = super().get_prep_value(value)
        if rank is not None and not math.isfinite(rank):
            raise ValidationError("Fractional ranks must be finite.", code="non_finite_rank")
        return rank

    @classmethod
    def get_append_rank(cls, previous: float | None) -> float:
        """Return the initial rank, or one clean step after ``previous``."""

        if previous is None:
            return cls.STEP
        lower = cls._coerce_endpoint(previous, name="previous")
        candidate = lower + cls.STEP
        if not math.isfinite(candidate) or candidate <= lower:
            raise FractionalRankExhausted("No finite append rank remains in this ordering context.")
        return candidate

    @classmethod
    def get_rank_between(cls, previous: float | None, following: float | None) -> float:
        """Allocate one finite rank strictly between adjacent stored endpoints.

        The ordering owner supplies the full persisted context under its write
        lock; visible lane neighbors are not sufficient endpoints. Explicit
        numeric writes never call this allocator and retain their exact values.
        """

        if following is None:
            return cls.get_append_rank(previous)
        upper = cls._coerce_endpoint(following, name="following")
        if previous is None:
            candidate = upper - cls.STEP
            valid = math.isfinite(candidate) and candidate < upper
        else:
            lower = cls._coerce_endpoint(previous, name="previous")
            if lower >= upper:
                raise ValueError("Ordering neighbors must be strictly increasing.")
            candidate = lower / 2 + upper / 2
            valid = lower < candidate < upper
        if not valid:
            raise FractionalRankExhausted("No finite rank remains between these ordering neighbors.")
        return candidate

    def rebalance(self, rows: models.QuerySet[Any]) -> int:
        """Spread a complete context under its owner's write lock, preserving order.

        Both phases use native tracked updates in one transaction. The caller
        supplies every row of the context and holds its insertion/reorder lock.
        Staging avoids all stored and final ranks, including an exhausted tail.
        """

        using = rows.db
        with transaction.atomic(using=using):
            instances = list(rows.select_for_update(of=("self",)).order_by(self.name, "pk"))
            if not instances:
                return 0
            finals = [self.STEP * (index + 1) for index in range(len(instances))]
            occupied = {getattr(row, self.attname) for row in instances} | set(finals)
            temporary = []
            candidate = 0.0
            for _row in instances:
                while candidate in occupied:
                    candidate -= self.STEP
                temporary.append(candidate)
                occupied.add(candidate)
            for ranks in (temporary, finals):
                for row, rank in zip(instances, ranks, strict=True):
                    rows.filter(pk=row.pk).update(**{self.name: rank})
            return len(instances)

    def get_temporary_ranks(
        self,
        instances: Sequence[models.Model],
        requested: Iterable[Any],
        *,
        using: str,
    ) -> tuple[float, ...]:
        """Allocate a disjoint finite range for an atomic contextual reorder.

        The caller holds the context's write lock and owns validation and both
        write phases. Include every requested destination so staging never
        occupies a final rank. Reading the declared unique contexts also covers
        stored rows outside the caller's subset. Allocation is read-only and
        returns only a complete range; exhaustion leaves the caller no partial plan.
        """

        if not instances:
            return ()
        distinct_contexts = {
            make_hashable(tuple(context.items())): context
            for instance in instances for context in (self._context_values(instance),)
        }
        contexts = models.Q()
        for context in distinct_contexts.values():
            contexts |= models.Q(**context)
        previous = (
            self.model._base_manager.using(using).filter(contexts)
            .order_by(f"-{self.name}").values_list(self.name, flat=True).first()
        )
        for value in requested:
            rank = self.to_python(value)
            if rank is not None and (previous is None or rank > previous):
                previous = rank
        temporary = []
        for _instance in instances:
            previous = self.get_append_rank(previous)
            temporary.append(previous)
        return tuple(temporary)


    @staticmethod
    def _coerce_endpoint(value: Any, *, name: str) -> float:
        """Return one finite rank endpoint or reject it."""

        try:
            rank = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{name} rank must be a finite float.") from error
        if not math.isfinite(rank):
            raise ValueError(f"{name} rank must be a finite float.")
        return rank
