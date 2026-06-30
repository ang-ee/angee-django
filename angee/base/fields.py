"""Angee model field types.

Thin semantic wrappers over the libraries ``docs/stack.md`` names as the owner
of each concern. Angee adds only the naming and the framework default; the
library owns the behavior.
"""

from __future__ import annotations

import base64
from typing import Any, cast

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.core import checks
from django.core.exceptions import FieldError, ImproperlyConfigured
from django.db import models
from django.utils.module_loading import import_string
from django_choices_field import TextChoicesField
from django_sqids import SqidsField
from sqids import Sqids

from angee.base.impl import ImplBase
from angee.base.registry import impl_registry, resolve_impl_class


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
    shared body behind ``SqidField.public_id_from_value`` and
    ``SqidPublicIdentity.public_id_from_pk``. ``prefix`` is already canonical.
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
    """

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

        return encode_public_id(self.sqids_instance, self.prefix, value)


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

    def to_python(self, value: Any) -> Any:
        """Accept stored values and GraphQL enum member names for this state."""

        choices_enum = cast(Any, self.choices_enum)
        if isinstance(value, choices_enum):
            return value
        raw = getattr(value, "value", value)
        text = str(raw).strip()
        if text:
            member = getattr(choices_enum, "__members__", {}).get(text)
            if member is not None:
                return member
        return super().to_python(raw)

    def pre_save(self, model_instance: models.Model, add: bool) -> Any:
        """Normalize the in-memory value before Django writes and returns it."""

        value = self.to_python(super().pre_save(model_instance, add))
        setattr(model_instance, self.attname, value)
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


class ImplClassField(TextChoicesField):
    """A column naming a non-model implementation class by a short key.

    The open-set tool from ``docs/backend/guidelines.md``: one concrete model
    whose row selects a strategy/client/backend class that differs only in
    behaviour (e.g. a ``storage.Backend`` row → a ``StorageBackend`` subclass).
    ``registry_setting`` names the Django setting that maps keys to dotted import
    paths (``{"local": "angee.storage.backends.LocalBackend"}``); addons
    contribute their impls into it through ``autoconfig`` — the framework's
    composition seam. Every addon has contributed by the time the schema is
    produced, so the key set is closed: the column is a ``TextChoices`` enum built
    from the registered keys, and ``strawberry-django`` renders the GraphQL enum
    natively (this is a ``TextChoicesField``, exactly like ``StateField``). The
    registry must be **non-empty** — an addon whose impl set could otherwise be
    empty registers a noop/null-object default (storage's ``local``; integrate's
    ``none``) so a composition always has at least one selectable impl. The field
    resolves a row's key against the mapping and ``import_string``s the
    **composed, trusted** path (never row text), checking it is a ``base_class``
    subclass — the shape Angee already uses to resolve an addon's declared
    ``schemas`` reference; ``manage.py check`` validates every configured path up
    front. Keys must be identifier-safe (they become enum members). Parameterized
    like ``StateField``: ``ImplClassField(base_class=StorageBackend,
    registry_setting="ANGEE_STORAGE_BACKEND_CLASSES")``. Resolution returns the
    class; the owning model instantiates it, because the constructor contract —
    what the impl receives — belongs with the row's config and identity.
    """

    def __init__(self, *, base_class: type | None = None, registry_setting: str = "", **kwargs: Any) -> None:
        """Bind the implementation base and build the enum from the registry keys."""

        if base_class is not None and not isinstance(base_class, type):
            raise ImproperlyConfigured("ImplClassField base_class must be a type.")
        self.base_class = base_class
        self.registry_setting = registry_setting
        kwargs.setdefault("max_length", 100)
        super().__init__(choices_enum=self._build_enum(), **kwargs)

    def deconstruct(self) -> tuple[str, str, list[Any], dict[str, Any]]:
        """Emit a plain varchar column; rebuild the enum from the setting on reconstruct.

        The enum is the set of installed impls — a runtime composition fact, not a
        database fact — so the choices are dropped and only ``registry_setting``
        (plus the fixed ``max_length``) rides through. Adding or removing an impl
        therefore never churns a migration; ``base_class`` survives onto the live
        model field through ``deepcopy`` inheritance.
        """

        name, path, args, kwargs = super().deconstruct()
        kwargs.pop("choices", None)
        kwargs["registry_setting"] = self.registry_setting
        return name, path, args, kwargs

    def check(self, **kwargs: Any) -> list[checks.CheckMessage]:
        """Validate the declaration and every configured impl path.

        Imports each dotted path in the mapping and checks it against
        ``base_class``, so a typo or a non-subclass fails ``manage.py check``
        rather than a later row resolution. ``base_class`` is checked on the live
        model field (kept through ``deepcopy`` inheritance), not on migration-state
        copies.
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

    def resolve_class(self, key: Any) -> type:
        """Return the impl class the configured mapping binds to ``key``.

        ``key`` may be a plain string or the enum member this column reads back.
        Delegates the registry lookup + ``base_class`` check to the shared
        :func:`~angee.base.registry.resolve_impl_class` owner after canonicalizing the
        key, so the per-row column and the row-less selectors resolve identically.
        """

        return resolve_impl_class(self.registry_setting, self.key_for(key), cast(type, self.base_class))

    def key_for(self, value: Any) -> str:
        """Return the canonical registry key for a stored/input enum-ish value.

        GraphQL reads ``TextChoices`` fields as enum member names (``GITHUB``) while
        the database and registry use the member values (``github``). The field owns
        that mapping, so callers canonicalize here before resolving or storing.
        """

        registry = self._registry()
        raw = getattr(value, "value", value)
        text = str(raw).strip()
        if text in registry:
            return text
        name = str(getattr(value, "name", "")).strip()
        candidates = [candidate for candidate in (text, name) if candidate]
        for candidate in candidates:
            lower = candidate.lower()
            if lower in registry:
                return lower
            upper = candidate.upper()
            for key in registry:
                if key.upper() == upper:
                    return key
        return text

    def _build_enum(self) -> type[models.TextChoices]:
        """Return a ``TextChoices`` enum over the registered keys, in deterministic order."""

        keys = sorted(self._registry())
        if not keys:
            raise ImproperlyConfigured(
                f"ImplClassField registry settings.{self.registry_setting} is empty; an addon must "
                "contribute at least one impl (e.g. a noop/null-object default) before the field is built."
            )
        members = [(key.upper(), (key, key)) for key in keys]
        return cast("type[models.TextChoices]", models.TextChoices(self._enum_name(), members))

    def _enum_name(self) -> str:
        """Return a stable PascalCase GraphQL enum name derived from ``registry_setting``."""

        core = self.registry_setting.removeprefix("ANGEE_").removesuffix("_CLASSES")
        camel = "".join(part.capitalize() for part in core.split("_") if part)
        return f"{camel or 'Impl'}Impl"

    def impl_choices(self) -> list[dict[str, Any]]:
        """Return pickable choices (``key``/``label``/``icon``/``category``/``defaults``) for the registry.

        The registry key is authoritative — it is the enum value the column stores;
        the rest comes from the resolved ``ImplBase`` subclass. A non-``ImplBase``
        impl (a bare behaviour class) degrades to a label-only choice.
        """

        choices: list[dict[str, Any]] = []
        for key in sorted(self._registry()):
            impl = self.resolve_class(key)
            if isinstance(impl, type) and issubclass(impl, ImplBase):
                choices.append({**impl.choice(), "key": key})
            else:
                choices.append({"key": key, "label": key, "icon": "", "category": "", "defaults": {}})
        return choices

    def _registry(self) -> dict[str, str]:
        """Return the configured ``key → dotted path`` mapping for this field."""

        return impl_registry(self.registry_setting)
