"""Angee model field types.

Thin semantic wrappers over the libraries ``docs/stack.md`` names as the owner
of each concern. Angee adds only the naming and the framework default; the
library owns the behavior.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.core import checks
from django.core.exceptions import FieldError, ImproperlyConfigured, ValidationError
from django.db import models
from django.utils.module_loading import import_string
from django_choices_field import TextChoicesField
from django_sqids import SqidsField


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


class SqidField(SqidsField):
    """Angee's opaque public id column, declared as ``django-sqids`` glue.

    ``docs/stack.md`` names ``django-sqids`` the owner of opaque external ids;
    this wrapper only makes the decoder total: ``from_db_value`` receives
    ``None`` when the encoded column arrives through a nullable join — e.g.
    ``values_list("parent__sqid")`` over a nullable self-FK, the shape REBAC
    field-backed arrows query — and upstream encodes unconditionally there.
    """

    def from_db_value(self, value: Any, expression: Any, connection: Any, *args: Any) -> Any:
        """Return the encoded public id, passing NULL columns through."""

        if value is None:
            return None
        return super().from_db_value(value, expression, connection, *args)


class StateField(TextChoicesField):
    """A finite-state column backed by a ``TextChoices`` enum.

    ``docs/stack.md`` names ``django-choices-field`` the owner of enum-backed
    model fields; this is the ``StateField`` semantic wrapper it lists. The
    enum is the single source of truth — ``strawberry-django`` emits the
    GraphQL enum straight from ``choices_enum`` and the column ``max_length``
    is derived from it, so a state column never restates its choices. Declared
    natively, e.g. ``StateField(choices_enum=Note.Status, default=...)``.
    """

    def __init__(self, **kwargs: Any) -> None:
        """Default a state column to indexed; it is what queries filter on."""

        kwargs.setdefault("db_index", True)
        super().__init__(**kwargs)


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

    _angee_fernet_label: str | None = None

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

    def get_db_prep_save(self, value: Any, connection: Any) -> str | None:
        """Encrypt plaintext for storage in the database column."""

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
    ) -> str | None:
        """Decrypt database tokens back to plaintext."""

        del expression, connection
        if value is None:
            return None
        try:
            return self._fernet().decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise ImproperlyConfigured(
                f"Cannot decrypt {self._angee_fernet_label}: ciphertext is not valid for the current "
                "SECRET_KEY-derived key (rotated SECRET_KEY or non-encrypted data)."
            ) from exc

    def get_lookup(self, lookup_name: str) -> Any:
        """Allow null checks only; encrypted values are not comparable."""

        if lookup_name == "isnull":
            return super().get_lookup(lookup_name)
        raise FieldError("EncryptedField column is not queryable by value.")

    def _fernet(self) -> Fernet:
        """Return the Fernet instance for this bound model field."""

        if self._angee_fernet_label is None:
            raise ImproperlyConfigured("EncryptedField must be bound to a model before use.")
        return _derive_fernet(self._angee_fernet_label)


class ImplClassField(models.CharField):
    """A column naming a non-model implementation class by a short key.

    The open-set tool from ``docs/backend/guidelines.md``: one concrete model
    whose row selects a strategy/client/backend class that differs only in
    behaviour (e.g. a ``storage.Backend`` row → a ``StorageBackend`` subclass).
    The row stores a short key; ``registry_setting`` names the Django setting
    that maps keys to dotted import paths
    (``{"local": "angee.storage.backends.LocalBackend"}``). Addons contribute
    their impls into that setting through ``autoconfig`` — the framework's
    composition seam — so the available impls are a composition fact, not a
    base-model import. The field resolves the row's key against that mapping and
    ``import_string``s the **composed, trusted** path (never row text), checking
    it is a ``base_class`` subclass; this is the shape Angee already uses to
    resolve an addon's declared ``schemas`` reference. Parameterized like
    ``StateField(choices_enum=...)``: ``ImplClassField(base_class=StorageBackend,
    registry_setting="ANGEE_STORAGE_BACKEND_CLASSES")``. Resolution returns the
    class; the owning model instantiates it, because the constructor contract —
    what the impl receives — belongs with the row's config and identity.
    """

    def __init__(self, *, base_class: type | None = None, registry_setting: str = "", **kwargs: Any) -> None:
        """Bind the implementation base and the setting mapping keys to dotted paths."""

        if base_class is not None and not isinstance(base_class, type):
            raise ImproperlyConfigured("ImplClassField base_class must be a type.")
        self.base_class = base_class
        self.registry_setting = registry_setting
        kwargs.setdefault("max_length", 100)
        super().__init__(**kwargs)

    def check(self, **kwargs: Any) -> list[checks.CheckMessage]:
        """Validate the declaration and every configured impl path.

        ``base_class``/``registry_setting`` are not database facts, so they do
        not ride through ``deconstruct``; migration-state copies carry the
        defaults while the live model field (kept through ``deepcopy``
        inheritance) is the one ``check`` runs against. Every dotted path in the
        configured mapping is imported and checked against ``base_class`` here,
        so a typo or a non-subclass fails ``manage.py check`` rather than a later
        row resolution.
        """

        errors = super().check(**kwargs)
        if not isinstance(self.base_class, type):
            errors.append(
                checks.Error(
                    "ImplClassField requires a base_class type.",
                    hint="Pass base_class=… naming the implementation base.",
                    obj=self,
                    id="angee.E001",
                )
            )
        if not self.registry_setting:
            errors.append(
                checks.Error(
                    "ImplClassField requires registry_setting naming the key→path mapping.",
                    obj=self,
                    id="angee.E002",
                )
            )
        elif isinstance(self.base_class, type):
            for key, dotted in self._registry().items():
                try:
                    impl = import_string(dotted)
                except ImportError as error:
                    errors.append(
                        checks.Error(
                            f"settings.{self.registry_setting}[{key!r}] = {dotted!r} does not import: {error}",
                            obj=self,
                            id="angee.E003",
                        )
                    )
                    continue
                if not (isinstance(impl, type) and issubclass(impl, self.base_class)):
                    errors.append(
                        checks.Error(
                            f"settings.{self.registry_setting}[{key!r}] = {dotted!r} "
                            f"is not a {self.base_class.__name__} subclass.",
                            obj=self,
                            id="angee.E004",
                        )
                    )
        return errors

    def validate(self, value: Any, model_instance: Any) -> None:
        """Reject a stored key that resolves to no configured impl."""

        super().validate(value, model_instance)
        if not value:
            return
        try:
            self.resolve_class(value)
        except ImproperlyConfigured as error:
            raise ValidationError(str(error), code="invalid_impl") from error

    def resolve_class(self, key: str) -> type:
        """Return the impl class the configured mapping binds to ``key``."""

        registry = self._registry()
        try:
            dotted = registry[key]
        except KeyError as error:
            known = ", ".join(sorted(registry)) or "none configured"
            raise ImproperlyConfigured(
                f"No impl for key {key!r} in settings.{self.registry_setting} (known: {known})."
            ) from error
        impl = import_string(dotted)
        if not (isinstance(self.base_class, type) and isinstance(impl, type) and issubclass(impl, self.base_class)):
            base_name = getattr(self.base_class, "__name__", self.base_class)
            raise ImproperlyConfigured(
                f"settings.{self.registry_setting}[{key!r}] = {dotted!r} is not a {base_name}."
            )
        return impl

    def _registry(self) -> dict[str, str]:
        """Return the configured ``key → dotted path`` mapping for this field."""

        mapping = getattr(settings, self.registry_setting, {}) if self.registry_setting else {}
        if not isinstance(mapping, Mapping):
            raise ImproperlyConfigured(f"settings.{self.registry_setting} must be a mapping of key to dotted path.")
        return {str(key): str(value) for key, value in mapping.items()}
