"""Angee model field types.

Thin semantic wrappers over the libraries ``docs/stack.md`` names as the owner
of each concern. Angee adds only the naming and the framework default; the
library owns the behavior.

Fields may also declare projection facts for data-resource metadata:
``angee_widget``, ``angee_scalar_hint``, and ``angee_currency_field``. The
GraphQL classifier reads those inert attributes before falling back to stock
Django field types, so a field's owner states its own wire vocabulary.
"""

from __future__ import annotations

import base64
from typing import Any, cast

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.core.exceptions import FieldError, ImproperlyConfigured
from django.db import models
from django.db.models.query_utils import DeferredAttribute
from django_choices_field import TextChoicesField
from django_sqids import SqidsField
from sqids import Sqids


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
