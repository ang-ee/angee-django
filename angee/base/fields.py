"""Angee model field types.

Thin semantic wrappers over the libraries ``docs/stack.md`` names as the owner
of each concern. Angee adds only the naming and the framework default; the
library owns the behavior.

Fields may also declare projection facts for data-resource metadata:
``angee_widget``, ``angee_scalar_hint``, and ``angee_currency_field``. The
GraphQL classifier reads those inert attributes before falling back to stock
Django field types, so a field's owner states its own wire vocabulary.

``FractionalRankField`` is the framework's manual-ordering contract. An ordered
row stores one finite binary64 rank; its model defines the surrounding context
and must enforce ``UniqueConstraint(fields=(*context_fields, rank_field))``
(``nulls_distinct=False`` when a context field is nullable). Append with
``get_append_rank(last_rank)`` and insert or move with
``get_rank_between(previous_rank, next_rank)``. Ranks start and rebalance at
``1024.0`` intervals, a power-of-two spread whose midpoints stay exact until the
available binary64 values are genuinely exhausted. ``FractionalRankExhausted``
is the signal to enqueue the durable ``jobs.rebalance_fractional_ranks`` task
with the concrete model label, exact context values, and rank-field name; callers
must not guess an epsilon or silently reuse a rank. Rebalance rewrites only that
context under one transaction, preserves its visible ``(rank, pk)`` order, and
is idempotent. Allocation is optimistic: the contextual unique constraint
arbitrates concurrent writers, and a losing writer rereads its neighbors before
retrying.
"""

from __future__ import annotations

import base64
import math
from collections.abc import Mapping
from typing import Any, cast

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.core.exceptions import FieldDoesNotExist, FieldError, ImproperlyConfigured, ValidationError
from django.db import models, router, transaction
from django.db.models.query_utils import DeferredAttribute
from django_choices_field import TextChoicesField
from django_sqids import SqidsField
from sqids import Sqids

from angee.base.scoping import system_queryset


def _derive_fernet(label: str) -> Fernet:
    """Return the Fernet instance for one model column label."""

    secret_key = settings.SECRET_KEY
    if not secret_key:
        raise ImproperlyConfigured("EncryptedField requires a non-empty SECRET_KEY.")
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=label.encode(),
    ).derive(secret_key.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def canonical_sqid_prefix(prefix: str) -> str:
    """Return ``prefix`` carrying Angee's public-id separator (``abc`` -> ``abc_``)."""

    if not prefix:
        return ""
    return prefix if prefix.endswith("_") else f"{prefix}_"


def encode_public_id(sqids: Sqids, prefix: str, value: Any) -> str:
    """Return the public id encoding ``value``'s backing integer under ``prefix``.

    The one reading of "encode a primary-key value to an Angee public id" — the
    shared body behind ``SqidField.public_id_from_value``. ``prefix`` is already
    canonical.
    """

    if value in (None, ""):
        return ""
    encoded = sqids.encode([int(value)])
    return f"{prefix}{encoded}" if encoded is not None else ""


class SqidField(SqidsField):
    """Angee's opaque public id column, declared as ``django-sqids`` glue.

    ``docs/stack.md`` names ``django-sqids`` the owner of opaque external ids;
    this wrapper makes the decoder total and lets a model state only the one
    fact that varies between models — the prefix. A model declares
    ``sqid_prefix = "nte_"`` (``SqidMixin`` exposes the attribute and the shared
    column); the field reads it in ``contribute_to_class`` rather than every
    model re-declaring the whole column. An explicit ``prefix=`` still wins.

    Totality: ``from_db_value`` receives ``None`` when the encoded column
    arrives through a nullable join — e.g. ``values_list("parent__sqid")`` over
    a nullable self-FK, the shape REBAC field-backed arrows query — and upstream
    encodes unconditionally there.

    The field is also usable unbound as a pure public-id codec through
    ``public_id_from_value`` and ``public_id_to_value``; third-party-model
    adapters such as ``SqidPublicIdentity`` build on that declared API.
    """

    sqids_instance: Sqids | None
    angee_scalar_hint = "ID"

    def __init__(self, *args: Any, prefix: str = "", **kwargs: Any) -> None:
        """Normalize Angee public-id prefixes to the canonical ``abc_`` shape."""

        self._angee_declared_prefix = prefix
        super().__init__(*args, prefix=canonical_sqid_prefix(prefix), **kwargs)

    def contribute_to_class(self, cls: type[models.Model], name: str) -> None:
        """Resolve the prefix from the model's ``<field>_prefix`` when unset.

        Lets ``SqidMixin``'s one shared column serve every model: each model
        states only ``sqid_prefix = "nte_"`` and the inherited field picks it up
        here. ``sqid`` is a private, non-concrete column, so this never reaches a
        migration — it only shapes how the id encodes.
        """

        super().contribute_to_class(cls, name)
        if not self._angee_declared_prefix:
            declared = getattr(cls, f"{name}_prefix", "")
            if not isinstance(declared, str):
                raise ImproperlyConfigured(
                    f"{cls.__name__}.{name}_prefix must be a str, got {type(declared).__name__}."
                )
            self.prefix = canonical_sqid_prefix(declared)

    def deconstruct(self) -> tuple[str | None, str, list[Any], dict[str, Any]]:
        """Serialize the full public-id contract for generated/runtime models.

        Emits the *resolved* ``prefix`` (not the declared one), so an emitted or
        migration-state model carries the full prefix without needing the
        source's ``sqid_prefix`` class attribute.
        """

        name, path, args, kwargs = super().deconstruct()
        kwargs["real_field_name"] = self.real_field_name
        if self.prefix:
            kwargs["prefix"] = self.prefix
        if self.min_length is not None:
            kwargs["min_length"] = self.min_length
        if self.alphabet is not None:
            kwargs["alphabet"] = self.alphabet
        if self._explicit_sqids_instance is not None:
            kwargs["sqids_instance"] = self._explicit_sqids_instance
        return name, path, args, kwargs

    def from_db_value(self, value: Any, expression: Any, connection: Any, *args: Any) -> Any:
        """Return the encoded public id, passing NULL columns through.

        ``django_sqids`` ``from_db_value`` encodes unconditionally, so a NULL
        arriving through a nullable join crashes it (``sqids.encode([None])``
        raises ``TypeError``); this guard is the workaround. The durable fix is an
        upstream ``django_sqids`` PR, after which this override can be deleted.
        """

        if value is None:
            return None
        return super().from_db_value(value, expression, connection, *args)

    def public_id_from_value(self, value: Any) -> str:
        """Return the encoded public id for one backing integer value."""

        return encode_public_id(self._public_id_codec(), self.prefix, value)

    def public_id_to_value(self, public_id: Any) -> int | None:
        """Return the backing integer decoded from one public id."""

        if public_id in (None, ""):
            return None
        self.sqids_instance = self._public_id_codec()
        decoded = self.get_prep_value(str(public_id))
        return cast(int | None, decoded)

    def _public_id_codec(self) -> Sqids:
        """Return the field's Sqids codec, initialising unbound adapter fields."""

        codec = self.sqids_instance
        if codec is None:
            codec = cast(Sqids, self.get_sqid_instance())
            self.sqids_instance = codec
        return codec


class StateField(TextChoicesField):
    """A finite-state column backed by a ``TextChoices`` enum.

    ``docs/stack.md`` names ``django-choices-field`` the owner of enum-backed
    model fields; this is the ``StateField`` semantic wrapper it lists. The
    enum is the single source of truth — ``strawberry-django`` emits the
    GraphQL enum straight from ``choices_enum`` and the column ``max_length``
    is derived from it, so a state column never restates its choices. Declared
    natively, e.g. ``StateField(choices_enum=Note.Status, default=...)``.
    """

    angee_widget = "select"
    angee_scalar_hint = "String"

    def __init__(self, **kwargs: Any) -> None:
        """Default a state column to indexed; it is what queries filter on."""

        self._angee_blank_string = bool(kwargs.get("blank")) and not bool(kwargs.get("null"))
        if self._angee_blank_string:
            kwargs["blank"] = False
        kwargs.setdefault("db_index", True)
        super().__init__(**kwargs)
        if self._angee_blank_string:
            self.blank = True

    def to_python(self, value: Any) -> Any:
        """Accept stored values and GraphQL enum member names for this state."""

        choices_enum = cast(Any, self.choices_enum)
        member = enum_member_for(choices_enum, value)
        if member is not None:
            return member
        raw = getattr(value, "value", value)
        if self._angee_blank_string and raw in self.empty_values:
            return ""
        return super().to_python(raw)

    def pre_save(self, model_instance: models.Model, add: bool) -> Any:
        """Normalize the in-memory value before Django writes and returns it."""

        value = self.to_python(super().pre_save(model_instance, add))
        setattr(model_instance, self.attname, value)
        return value


class FractionalRankExhausted(ValueError):
    """Raised when binary64 cannot represent another safe fractional rank."""


class FractionalRankField(models.FloatField):
    """Finite float rank for manual ordering inside a model-defined context.

    The field owns rank arithmetic and transactional rebalance. The consumer
    model owns the context columns and their database uniqueness constraint;
    the field cannot infer whether a list is scoped by a lane, parent, project,
    or another domain fact.
    """

    STEP = 1024.0
    angee_scalar_hint = "Float"
    angee_widget = "float"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Default ranks to indexed because ordered contexts query by them."""

        kwargs.setdefault("db_index", True)
        super().__init__(*args, **kwargs)

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
            raise FractionalRankExhausted("No finite append rank remains; rebalance the context.")
        return candidate

    @classmethod
    def get_rank_between(cls, previous: float | None, following: float | None) -> float:
        """Return a rank strictly between optional neighboring ranks.

        ``None`` denotes the corresponding edge: both edges absent returns the
        initial rank, no following neighbor appends, and no previous neighbor
        prepends by one clean step.
        """

        if following is None:
            return cls.get_append_rank(previous)
        upper = cls._coerce_endpoint(following, name="following")
        if previous is None:
            candidate = upper - cls.STEP
            if not math.isfinite(candidate) or candidate >= upper:
                raise FractionalRankExhausted("No finite prepend rank remains; rebalance the context.")
            return candidate

        lower = cls._coerce_endpoint(previous, name="previous")
        if lower >= upper:
            raise ValueError("previous rank must be strictly less than following rank.")
        if math.nextafter(lower, upper) >= upper:
            raise FractionalRankExhausted(
                "No binary64 rank exists strictly between the neighbors; rebalance the context."
            )
        candidate = lower / 2.0 + upper / 2.0
        if not lower < candidate < upper:
            raise FractionalRankExhausted(
                "No binary64 midpoint exists strictly between the neighbors; rebalance the context."
            )
        return candidate

    def rebalance(self, *, context: Mapping[str, Any], using: str | None = None) -> int:
        """Rewrite this field to a clean spread inside one exact context.

        Returns the number of rows whose rank changed. Rows are read in committed
        ``(rank, pk)`` order through the model's system queryset and staged outside
        both the old and final ranges before the clean ranks are written. Staging
        avoids transient unique-constraint collisions. A concurrent insert is
        arbitrated by that constraint and must retry. The rank must be NOT NULL.
        """

        model = getattr(self, "model", None)
        if model is None or self.name is None:
            raise ImproperlyConfigured("FractionalRankField must be bound to a model before rebalance().")
        context_filter = self._context_filter(context)
        database = using or router.db_for_write(model)

        with transaction.atomic(using=database):
            writer = system_queryset(model, using=database, lock=()).filter(**context_filter)
            rows = list(writer.only(model._meta.pk.name, self.name).order_by(self.name, model._meta.pk.name))
            current = [self._coerce_endpoint(getattr(row, self.attname), name=self.attname) for row in rows]
            clean = self._clean_ranks(len(rows))
            changed = sum(rank != target for rank, target in zip(current, clean, strict=True))
            if not changed:
                return 0

            temporary = self._temporary_ranks(current, clean)
            for row, rank in zip(rows, temporary, strict=True):
                setattr(row, self.attname, rank)
            writer.bulk_update(rows, [self.name])
            for row, rank in zip(rows, clean, strict=True):
                setattr(row, self.attname, rank)
            writer.bulk_update(rows, [self.name])
        return changed

    def _context_filter(self, context: Mapping[str, Any]) -> dict[str, Any]:
        """Return an exact direct-field filter for this field's bound model."""

        model = getattr(self, "model", None)
        if model is None:
            raise ImproperlyConfigured("FractionalRankField must be bound before resolving a context.")
        result: dict[str, Any] = {}
        for name, value in context.items():
            if not isinstance(name, str) or "__" in name:
                raise ImproperlyConfigured("Fractional-rank context keys must be direct model field names.")
            try:
                field = model._meta.get_field(name)
            except FieldDoesNotExist as error:
                raise ImproperlyConfigured(
                    f"{model._meta.label}.{name} is not a fractional-rank context field."
                ) from error
            if field is self or not field.concrete or field.many_to_many:
                raise ImproperlyConfigured(
                    f"{model._meta.label}.{name} cannot be a fractional-rank context field."
                )
            result[name] = value
        return result

    @classmethod
    def _clean_ranks(cls, count: int) -> list[float]:
        """Return ``count`` positive, evenly-spaced finite ranks."""

        ranks = [index * cls.STEP for index in range(1, count + 1)]
        if any(not math.isfinite(rank) for rank in ranks):
            raise FractionalRankExhausted("The context is too large for a finite clean rank spread.")
        return ranks

    @staticmethod
    def _temporary_ranks(current: list[float], clean: list[float]) -> list[float]:
        """Return unique ranks outside both current and final ranges."""

        if not current:
            return []
        high = max((*current, *clean))
        above: list[float] = []
        candidate = high
        for _ in current:
            candidate = math.nextafter(candidate, math.inf)
            if not math.isfinite(candidate):
                break
            above.append(candidate)
        if len(above) == len(current):
            return above

        low = min((*current, *clean))
        below: list[float] = []
        candidate = low
        for _ in current:
            candidate = math.nextafter(candidate, -math.inf)
            if not math.isfinite(candidate):
                break
            below.append(candidate)
        if len(below) == len(current):
            return below
        raise FractionalRankExhausted("No finite staging range remains for rebalance.")

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


class _InvalidEncryptedValue:
    """Row-local marker for ciphertext that cannot be decrypted."""

    def __init__(self, label: str | None) -> None:
        """Store the field label for the eventual access error."""

        self.label = label or "<unbound encrypted field>"

    def error(self) -> ImproperlyConfigured:
        """Return the actionable error for this unreadable value."""

        return ImproperlyConfigured(
            f"Cannot decrypt {self.label}: ciphertext is not valid for the current "
            "SECRET_KEY-derived key (rotated SECRET_KEY, renamed model/field label, or non-encrypted data). "
            "Plan key rotation with ANGEE_FERNET_KEYS/MultiFernet before changing SECRET_KEY or model labels."
        )

    def __repr__(self) -> str:
        """Return a safe debug representation without exposing ciphertext."""

        return f"<InvalidEncryptedValue {self.label}>"


class _EncryptedFieldDescriptor(DeferredAttribute):
    """Descriptor that isolates decrypt failures to field access."""

    def __set__(self, instance: models.Model, value: Any) -> None:
        """Store assigned values where Django expects concrete field data."""

        instance.__dict__[self.field.attname] = value

    def __get__(self, instance: models.Model | None, cls: type[models.Model] | None = None) -> Any:
        """Return the plaintext value or raise the row-local decrypt error."""

        value = super().__get__(instance, cls)
        if isinstance(value, _InvalidEncryptedValue):
            raise value.error()
        return value


class EncryptedField(models.TextField):
    """Fernet-at-rest text field for framework secret values.

    The database stores a Fernet token while Python reads return decrypted
    plaintext. Each column derives its Fernet key from ``settings.SECRET_KEY``
    with HKDF-SHA256 using the model's ``label_lower`` plus field name as the
    per-column label. The field is secret-by-type: never put it on a GraphQL
    type. Fernet is non-deterministic, so the column is not queryable by value;
    ``get_or_create()``/``update_or_create()`` keyed on it and ``bulk_update()``
    of it will raise, ``unique=True``/``primary_key=True`` are rejected at
    construction, and ordering or distinct on the column are meaningless. Today
    the key tracks ``SECRET_KEY``, so rotating ``SECRET_KEY`` orphans existing
    ciphertext; ``ANGEE_FERNET_KEYS``/``MultiFernet`` is the future rotation
    path.
    """

    descriptor_class = _EncryptedFieldDescriptor
    angee_scalar_hint = "String"

    _angee_fernet_label: str | None = None
    _angee_fernet: Fernet | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Reject uniqueness contracts Fernet ciphertext cannot enforce."""

        if kwargs.get("unique") or kwargs.get("primary_key"):
            raise ImproperlyConfigured(
                "EncryptedField cannot be unique or a primary key: "
                "non-deterministic ciphertext makes uniqueness "
                "meaningless and unenforceable."
            )
        super().__init__(*args, **kwargs)

    def contribute_to_class(
        self,
        cls: type[models.Model],
        name: str,
        private_only: bool = False,
    ) -> None:
        """Store the deterministic per-column label once Django binds the field."""

        super().contribute_to_class(cls, name, private_only=private_only)
        self._angee_fernet_label = f"{cls._meta.label_lower}.{name}"
        self._angee_fernet = None

    def get_db_prep_save(self, value: Any, connection: Any) -> str | None:
        """Encrypt plaintext for storage in the database column."""

        if isinstance(value, _InvalidEncryptedValue):
            raise value.error()
        prepared = super().get_db_prep_save(value, connection=connection)
        if prepared is None:
            return None
        if hasattr(prepared, "as_sql"):
            raise FieldError(
                "EncryptedField stores only plaintext scalar assignments; "
                "it does not support expression writes "
                "(F(), Concat, Value()) or bulk_update()."
            )
        return self._fernet().encrypt(prepared.encode()).decode()

    def from_db_value(
        self,
        value: str | None,
        expression: Any,
        connection: Any,
    ) -> str | _InvalidEncryptedValue | None:
        """Decrypt database tokens back to plaintext."""

        del expression, connection
        if value is None:
            return None
        try:
            return self._fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            return _InvalidEncryptedValue(self._angee_fernet_label)

    def get_lookup(self, lookup_name: str) -> Any:
        """Allow null checks only; encrypted values are not comparable."""

        if lookup_name == "isnull":
            return super().get_lookup(lookup_name)
        raise FieldError("EncryptedField column is not queryable by value.")

    def _fernet(self) -> Fernet:
        """Return the Fernet instance for this bound model field."""

        if self._angee_fernet_label is None:
            raise ImproperlyConfigured("EncryptedField must be bound to a model before use.")
        if self._angee_fernet is None:
            self._angee_fernet = _derive_fernet(self._angee_fernet_label)
        return self._angee_fernet


# ``ImplClassField`` moved to ``angee.base.impl`` ("one impl owner"); historical
# migrations (a preserved artifact) still import it from here. A lazy re-export
# keeps them loadable without a top-level import cycle (``impl`` imports
# ``enum_member_for`` from this module). New migrations deconstruct through the
# live class, so they carry the canonical ``angee.base.impl`` path and no drift
# appears — this alias serves only the frozen files.
_RELOCATED = {"ImplClassField": "angee.base.impl"}


def __getattr__(name: str) -> Any:
    """Resolve a field class that moved to another base module (PEP 562)."""

    module_path = _RELOCATED.get(name)
    if module_path is not None:
        from importlib import import_module

        return getattr(import_module(module_path), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def enum_member_for(choices_enum: Any, value: Any) -> Any | None:
    """Return the enum member represented by ``value`` or ``None`` when unknown."""

    if isinstance(value, choices_enum):
        return value
    raw = getattr(value, "value", value)
    text = str(raw).strip()
    if not text:
        return None
    member = getattr(choices_enum, "__members__", {}).get(text)
    if member is not None:
        return member
    try:
        return choices_enum(raw)
    except ValueError:
        return None
