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
from django.core.exceptions import FieldDoesNotExist, FieldError, ImproperlyConfigured
from django.db import models
from django.db.models.query_utils import DeferredAttribute
from django.utils.module_loading import import_string
from django_choices_field import TextChoicesField
from django_sqids import SqidsField
from sqids import Sqids

from angee.base.impl_types import ImplBase, ImplChoice
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

    def resolve_for(self, instance: models.Model) -> type:
        """Return the impl class selected by this field on ``instance``."""

        return self.resolve_class(getattr(instance, self.attname))

    def key_for(self, value: Any) -> str:
        """Return the canonical registry key for a stored/input enum-ish value.

        GraphQL reads ``TextChoices`` fields as enum member names (``GITHUB``) while
        the database and registry use the member values (``github``). The field owns
        that mapping, so callers canonicalize here before resolving or storing.
        """

        member = enum_member_for(cast(Any, self.choices_enum), value)
        if member is not None:
            return str(member.value)
        return str(getattr(value, "value", value)).strip()

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

    def impl_choices(self) -> list[ImplChoice]:
        """Return pickable choices (``key``/``label``/``icon``/``category``/``defaults``) for the registry.

        The registry key is authoritative — it is the enum value the column stores;
        the rest comes from the resolved ``ImplBase`` subclass. A non-``ImplBase``
        impl (a bare behaviour class) degrades to a label-only choice.
        """

        choices: list[ImplChoice] = []
        for key in sorted(self._registry()):
            impl = self.resolve_class(key)
            if isinstance(impl, type) and issubclass(impl, ImplBase):
                choice = impl.choice()
                choices.append(
                    ImplChoice(
                        key=key,
                        label=choice.label,
                        icon=choice.icon,
                        category=choice.category,
                        defaults=choice.defaults,
                    )
                )
            else:
                choices.append(ImplChoice(key=key, label=key, icon="", category="", defaults={}))
        return choices

    def _registry(self) -> dict[str, str]:
        """Return the configured ``key → dotted path`` mapping for this field."""

        return impl_registry(self.registry_setting)


class MoneyField(models.DecimalField):
    """A decimal amount paired with the currency its row is denominated in.

    ``docs/stack.md`` keeps money native — a ``DecimalField`` (default
    ``max_digits=18, decimal_places=6``), never a money library. The single fact a
    money column adds over a plain decimal is *which currency the amount is in*:
    ``currency_field`` names the path to the ``money.Currency`` foreign key that
    owns the row's currency, either a **sibling** FK on the same model
    (``"currency"``, the default) or a **one-hop** related path
    (``"order.currency"``) when the currency lives on a parent document.
    :meth:`check` validates that path by field introspection and refers to the
    currency model by its ``"money.Currency"`` label, so the field ships
    independently of where the currency addon is hosted (label-based coupling, no
    import of the sibling addon).

    ``currency_field`` is a semantic declaration, not a database fact: Django's
    ``Field.deconstruct`` serializes only the tracked column kwargs (the field's
    class path plus ``max_digits`` / ``decimal_places``) and never a custom
    constructor attribute, so ``currency_field`` stays out of migration state on
    its own — no ``deconstruct`` override — while the currency path rides through
    ``deepcopy`` inheritance onto the live field. Changing ``currency_field``
    therefore never writes a migration. Rendering the amount with its currency
    (resolved through the metadata's currency path) is the ``"money"`` widget's
    job, registered by the currency addon's web package; the field only owns the
    backend vocabulary.
    """

    CURRENCY_MODEL_LABEL = "money.Currency"

    def __init__(self, *args: Any, currency_field: str = "currency", **kwargs: Any) -> None:
        """Record the currency path and default the money decimal precision."""

        self.currency_field = currency_field
        kwargs.setdefault("max_digits", 18)
        kwargs.setdefault("decimal_places", 6)
        super().__init__(*args, **kwargs)

    def check(self, **kwargs: Any) -> list[checks.CheckMessage]:
        """Validate that ``currency_field`` resolves to a ``money.Currency`` FK.

        Runs at system-check time — after the app registry is populated and Django
        has resolved lazy relations — so it introspects the sibling or one-hop
        related field directly (``base_class``-style late resolution, never in
        ``__init__``/``contribute_to_class`` where relations may still be strings).
        A path segment whose relation is not yet resolvable is deferred to Django's
        own relation checks (``fields.E300`` reports a genuinely broken foreign
        key), so this speaks only to the currency contract.
        """

        errors = super().check(**kwargs)
        errors.extend(self._check_currency_field())
        return errors

    def _check_currency_field(self) -> list[checks.CheckMessage]:
        """Return check errors for the declared ``currency_field`` path."""

        segments = self.currency_field.split(".") if self.currency_field else []
        if not 1 <= len(segments) <= 2:
            return [
                checks.Error(
                    f"MoneyField currency_field={self.currency_field!r} must name a sibling "
                    "foreign key ('currency') or a one-hop related path ('order.currency').",
                    obj=self,
                    id="angee.E010",
                )
            ]
        model = self.model
        for hop in segments[:-1]:
            resolved = self._foreign_key_target(model, hop)
            if isinstance(resolved, checks.Error):
                return [resolved]
            if resolved is None:  # unresolved relation — Django's fields.E300 owns the report
                return []
            model = resolved
        target = self._foreign_key_target(model, segments[-1])
        if isinstance(target, checks.Error):
            return [target]
        if target is None:
            return []
        if target._meta.label != self.CURRENCY_MODEL_LABEL:
            return [
                checks.Error(
                    f"MoneyField currency_field={self.currency_field!r} resolves to "
                    f"{target._meta.label}, not {self.CURRENCY_MODEL_LABEL}.",
                    hint=f"Point currency_field at a foreign key to {self.CURRENCY_MODEL_LABEL}.",
                    obj=self,
                    id="angee.E013",
                )
            ]
        return []

    def _foreign_key_target(
        self, model: type[models.Model], field_name: str
    ) -> type[models.Model] | checks.Error | None:
        """Resolve one path segment to its related model, an error, or ``None`` (defer).

        ``None`` means the segment names a relation Django has not resolved yet
        (the target model is not installed); the caller defers to Django's own
        relation checks rather than double-reporting. A ``ForeignKey`` (``many_to_one``)
        and a ``OneToOneField`` (``one_to_one``, e.g. a parent-link hop) are both
        single-valued forward relations that resolve a currency, so both are accepted.
        """

        try:
            field = model._meta.get_field(field_name)
        except FieldDoesNotExist:
            return checks.Error(
                f"MoneyField currency_field={self.currency_field!r}: "
                f"{model._meta.label} has no field {field_name!r}.",
                obj=self,
                id="angee.E011",
            )
        if not (getattr(field, "many_to_one", False) or getattr(field, "one_to_one", False)):
            return checks.Error(
                f"MoneyField currency_field={self.currency_field!r}: "
                f"{model._meta.label}.{field_name} is not a foreign key.",
                obj=self,
                id="angee.E012",
            )
        related = field.related_model
        if related is None or isinstance(related, str):
            return None
        return related


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
