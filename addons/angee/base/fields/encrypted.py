"""Fernet-at-rest field and isolated decryption failures."""

from __future__ import annotations

import base64
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.core.exceptions import FieldError, ImproperlyConfigured
from django.db import models
from django.db.models.query_utils import DeferredAttribute
from django.utils.encoding import force_bytes


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
    ).derive(force_bytes(secret_key))
    return Fernet(base64.urlsafe_b64encode(key))


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
