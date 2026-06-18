"""Live GraphQL schema assembly from discovered addon parts."""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import MISSING, dataclass
from typing import Any, ClassVar, cast

import strawberry
from django.apps import AppConfig, apps
from django.core.exceptions import NON_FIELD_ERRORS, ImproperlyConfigured, ValidationError
from django.db import models
from django.utils.functional import cached_property
from rebac import MissingActorError, PermissionDenied, RebacMixin
from rebac.graphql.strawberry import RebacExtension
from rebac.graphql.strawberry_django import RebacDjangoOptimizerExtension
from rebac.managers import RebacManager
from strawberry.tools import merge_types
from strawberry.types.base import get_object_definition
from strawberry.types.execution import ExecutionContext
from strawberry.utils.str_converters import to_camel_case

from angee.addons import resolve_addon_reference
from angee.graphql.extension import extension_target
from angee.graphql.introspection import (
    django_model,
    surface_field_names,
    surface_name,
)
from graphql import GraphQLError, GraphQLSchema

DEFAULT_SCHEMA_NAME = "public"
"""Default GraphQL schema name served by Angee hosts."""

SCHEMA_PART_KEYS: tuple[str, ...] = (
    "query",
    "mutation",
    "subscription",
    "types",
    "extensions",
    "type_extensions",
    "input_extensions",
)
"""GraphQL merge buckets accepted from addon schema declarations."""

_NON_ROOT_KEYS = {"types", "extensions", "type_extensions", "input_extensions"}
_ROOT_TYPE_NAMES = {key: key.title() for key in SCHEMA_PART_KEYS if key not in _NON_ROOT_KEYS}

_APPLIED_EXTENSIONS_ATTR = "__angee_applied_type_extensions__"
_INPUT_EXTENSION_STATE_ATTR = "__angee_input_extension_state__"
"""Marker on a target Strawberry type recording which extensions are already merged."""


@dataclass(slots=True)
class _InputExtensionState:
    """Target-owned state for additively merged Strawberry input extensions."""

    original_init: Any
    base_field_names: frozenset[str]
    added_fields: dict[str, Any]
    applied: set[int]


def _unwrap_validation_error(exc: BaseException | None) -> ValidationError | None:
    """Return the ``ValidationError`` in a resolver's exception chain, or ``None``."""

    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, ValidationError):
            return exc
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return None


class AngeeSchema(strawberry.Schema):
    """Strawberry schema that exposes stable REBAC denial codes."""

    def process_errors(
        self,
        errors: list[GraphQLError],
        execution_context: ExecutionContext | None = None,
    ) -> None:
        """Attach GraphQL error codes before Strawberry logs errors."""

        for error in errors:
            self._apply_rebac_code(error)
            self._apply_validation_error(error)
        super().process_errors(errors, execution_context)

    def _apply_rebac_code(self, error: GraphQLError) -> None:
        """Attach the code owned by a REBAC denial exception."""

        original = error.original_error
        if isinstance(original, MissingActorError):
            code = "UNAUTHENTICATED"
        elif isinstance(original, PermissionDenied):
            code = "PERMISSION_DENIED"
        else:
            return
        error.extensions = {**(error.extensions or {}), "code": code}

    def _apply_validation_error(self, error: GraphQLError) -> None:
        """Expose a Django ``ValidationError`` as a per-field error extension.

        Model validation (``full_clean``) raises a ``ValidationError`` whose
        ``message_dict`` keys are model field names. Surface them as
        ``validationErrors`` (camel-cased to match the SDL field names a form
        binds to) plus a ``formErrors`` list for non-field errors, so the client
        renders each message under its field instead of one opaque banner.
        """

        validation = _unwrap_validation_error(error.original_error)
        if validation is None:
            return
        field_errors: dict[str, list[str]] = {}
        form_errors: list[str] = []
        if hasattr(validation, "error_dict"):
            for field, messages in validation.message_dict.items():
                if field == NON_FIELD_ERRORS:
                    form_errors.extend(messages)
                else:
                    field_errors[to_camel_case(field)] = list(messages)
        else:
            form_errors.extend(validation.messages)
        error.extensions = {
            **(error.extensions or {}),
            "code": "VALIDATION",
            "validationErrors": field_errors,
            "formErrors": form_errors,
        }


@dataclass(frozen=True, slots=True)
class SchemaParts:
    """Normalized GraphQL merge buckets for one schema name."""

    query: tuple[object, ...] = ()
    """Root query surfaces."""

    mutation: tuple[object, ...] = ()
    """Root mutation surfaces."""

    subscription: tuple[object, ...] = ()
    """Root subscription surfaces."""

    types: tuple[object, ...] = ()
    """Additional Strawberry types included in the schema."""

    extensions: tuple[object, ...] = ()
    """Additional Strawberry schema extensions."""

    type_extensions: tuple[object, ...] = ()
    """Strawberry types (marked with ``extends_type``) whose fields are merged onto
    an upstream type at build — the GraphQL parallel to a model ``extends``."""

    input_extensions: tuple[object, ...] = ()
    """Strawberry input subclasses whose added fields are merged onto an upstream
    hand-written crud input at build — the write-side parallel to type extension."""

    @classmethod
    def from_mapping(
        cls,
        app_config: AppConfig,
        name: str,
        raw_entry: Mapping[object, object],
    ) -> SchemaParts:
        """Return normalized schema parts declared by one addon."""

        unknown = set(raw_entry) - set(SCHEMA_PART_KEYS)
        if unknown:
            listed = ", ".join(sorted(str(key) for key in unknown))
            raise ImproperlyConfigured(f"{app_config.name}.schemas[{name!r}] has unknown keys: {listed}")
        return cls(**{key: _schema_part_values(app_config, name, key, raw_entry.get(key)) for key in SCHEMA_PART_KEYS})

    def merge(self, other: SchemaParts) -> SchemaParts:
        """Return these parts folded with ``other`` and deduped by identity."""

        return type(self)(
            query=self._dedupe_by_identity(self.query + other.query),
            mutation=self._dedupe_by_identity(self.mutation + other.mutation),
            subscription=self._dedupe_by_identity(self.subscription + other.subscription),
            types=self._dedupe_by_identity(self.types + other.types),
            extensions=self._dedupe_by_identity(self.extensions + other.extensions),
            type_extensions=self._dedupe_by_identity(self.type_extensions + other.type_extensions),
            input_extensions=self._dedupe_by_identity(self.input_extensions + other.input_extensions),
        )

    @staticmethod
    def _dedupe_by_identity(
        values: tuple[object, ...],
    ) -> tuple[object, ...]:
        """Return values with duplicate identities removed."""

        seen: set[int] = set()
        deduped: list[object] = []
        for value in values:
            marker = id(value)
            if marker in seen:
                continue
            seen.add(marker)
            deduped.append(value)
        return tuple(deduped)


def _field_python_name(field: Any) -> str:
    """Return the Python attribute name for one Strawberry input field."""

    return str(getattr(field, "python_name", "") or field.name)


def _input_field_default(python_name: str, field: Any) -> Any:
    """Return the dataclass-style default for one contributed input field."""

    default_factory = getattr(field, "default_factory", MISSING)
    if default_factory is not MISSING:
        return default_factory()
    default = getattr(field, "default", MISSING)
    if default is MISSING:
        raise TypeError(f"missing required keyword-only argument: {python_name!r}")
    if default is strawberry.UNSET:
        return strawberry.UNSET
    try:
        return copy.deepcopy(default)
    except Exception:  # noqa: BLE001 — a non-copyable immutable default is safe to reuse.
        return default


class GraphQLSchemas:
    """Collection owner for named GraphQL schema parts and builds."""

    _discovered: ClassVar[GraphQLSchemas | None] = None

    def __init__(self, addons: Iterable[AppConfig]) -> None:
        """Store addon configs in deterministic discovery order."""

        self.addons = tuple(addons)
        self._builds: dict[str, strawberry.Schema] = {}
        self._type_extensions_applied = False
        self._input_extensions_applied = False

    @classmethod
    def from_discovery(cls) -> GraphQLSchemas:
        """Return schemas built from installed addon discovery."""

        if cls._discovered is None:
            cls._discovered = cls(apps.get_app_configs())
        return cls._discovered

    @cached_property
    def parts(self) -> dict[str, SchemaParts]:
        """Return deduplicated schema parts folded in addon order."""

        collected: dict[str, SchemaParts] = {}
        for addon in self.addons:
            for name, parts in schema_parts_for(addon).items():
                collected[name] = collected.get(name, SchemaParts()).merge(parts)
        return collected

    def names(self) -> tuple[str, ...]:
        """Return contributed schema names in deterministic order."""

        return tuple(sorted(self.parts))

    def build(
        self,
        name: str = DEFAULT_SCHEMA_NAME,
    ) -> strawberry.Schema:
        """Return the merged live Strawberry schema named ``name``."""

        if name not in self._builds:
            self._builds[name] = self._build(name)
        return self._builds[name]

    def graphql_schema(self, name: str = DEFAULT_SCHEMA_NAME) -> GraphQLSchema:
        """Return the introspectable graphql-core schema for the named bucket.

        The public accessor for callers (e.g. the MCP tool layer) that walk the schema's
        types/fields/args. Owns the one reach into Strawberry's underlying graphql-core
        schema so siblings don't couple to that private attribute.
        """

        return self.build(name)._schema

    def _ensure_type_extensions_applied(self) -> None:
        """Merge every contributed ``type_extensions`` field onto its target type.

        Applied once, before any schema builds, so a target carries its
        downstream-contributed fields wherever it appears. The field objects are
        copied so the donor extension type and the target never share one. The
        target is contributed by an upstream addon (dependency order guarantees it
        is defined first); strawberry-django resolves each appended relation field's
        type from its model registry at build, so no upward import is needed.
        """

        if self._type_extensions_applied:
            return
        self._type_extensions_applied = True
        applied: set[int] = set()
        for parts in self.parts.values():
            for extension in parts.type_extensions:
                if id(extension) in applied:
                    continue
                applied.add(id(extension))
                self._apply_type_extension(extension)

    def _ensure_input_extensions_applied(self) -> None:
        """Merge every contributed ``input_extensions`` field onto its target input.

        The write-side parallel of ``_ensure_type_extensions_applied``: applied once,
        before any schema builds, so an upstream hand-written crud input carries its
        downstream-contributed fields wherever the create/update mutation reads it.
        """

        if self._input_extensions_applied:
            return
        self._input_extensions_applied = True
        applied: set[int] = set()
        for parts in self.parts.values():
            for extension in parts.input_extensions:
                if id(extension) in applied:
                    continue
                applied.add(id(extension))
                self._apply_input_extension(extension)

    def _apply_type_extension(self, extension: object) -> None:
        """Append one ``extends_type`` donor's fields onto its target type's definition."""

        target = extension_target(extension)
        if target is None:
            raise ImproperlyConfigured(
                f"{surface_name(extension)} is listed in type_extensions but is not "
                "marked with @extends_type(TargetType)"
            )
        self._merge_extension_fields(extension, target)

    def _apply_input_extension(self, extension: object) -> None:
        """Append one input subclass donor's added fields onto its base input.

        The donor is a ``@strawberry.input`` subclass of the upstream crud input it
        extends (e.g. ``OAuthClientOidcInput(OAuthClientInput)``) — the write-side
        parallel of ``extends_type``. ``crud`` captured the base input eagerly, so the
        schema assembler merges each donor's *added* fields onto the base object
        definition and installs one composed ``__init__`` that accepts every merged
        field. Multiple donors may extend the same base; field-name collisions fail
        fast and addon discovery order makes the result deterministic.
        """

        target = self._input_extension_base(extension)
        target_def = get_object_definition(target, strict=True)
        state = cast(_InputExtensionState | None, getattr(target, _INPUT_EXTENSION_STATE_ATTR, None))
        if state is None:
            state = _InputExtensionState(
                original_init=cast(Any, target).__init__,
                base_field_names=frozenset(_field_python_name(field) for field in target_def.fields),
                added_fields={},
                applied=set(),
            )
            setattr(target, _INPUT_EXTENSION_STATE_ATTR, state)
            self._install_input_extension_init(target, state)
        if id(extension) in state.applied:
            return

        donor_def = get_object_definition(cast(type, extension), strict=True)
        existing_graphql_names = {field.name for field in target_def.fields}
        for field in donor_def.fields:
            python_name = _field_python_name(field)
            if python_name in state.base_field_names:
                if field.name not in existing_graphql_names:
                    raise ImproperlyConfigured(
                        f"input extension {surface_name(extension)} redefines base field {python_name!r} "
                        f"on {target_def.name}; input_extensions may only add fields"
                    )
                continue  # inherited base field — already on the target
            if python_name in state.added_fields:
                raise ImproperlyConfigured(
                    f"input extension {surface_name(extension)} adds field {python_name!r} "
                    f"already declared on {target_def.name}"
                )
            if field.name in existing_graphql_names:
                raise ImproperlyConfigured(
                    f"input extension {surface_name(extension)} adds field {field.name!r} "
                    f"already declared on {target_def.name}"
                )
            copied = copy.copy(field)
            target_def.fields.append(copied)
            state.added_fields[python_name] = copied
            existing_graphql_names.add(field.name)
        state.applied.add(id(extension))

    def _install_input_extension_init(self, target: object, state: _InputExtensionState) -> None:
        """Install the one target-owned initializer that accepts all added fields."""

        def __init__(self: Any, *args: Any, **kwargs: Any) -> None:
            extension_values: dict[str, Any] = {}
            for python_name, field in state.added_fields.items():
                if python_name in kwargs:
                    extension_values[python_name] = kwargs.pop(python_name)
                else:
                    extension_values[python_name] = _input_field_default(python_name, field)
            state.original_init(self, *args, **kwargs)
            for python_name, value in extension_values.items():
                setattr(self, python_name, value)

        setattr(target, "__init__", __init__)

    def _input_extension_base(self, extension: object) -> object:
        """Return the upstream crud input a donor subclasses (its nearest Strawberry base)."""

        for base in type.mro(cast(type, extension))[1:]:
            if get_object_definition(base, strict=False) is not None:
                return base
        raise ImproperlyConfigured(
            f"{surface_name(extension)} in input_extensions must subclass the crud "
            "input it extends"
        )

    def _merge_extension_fields(self, extension: object, target: object) -> None:
        """Append a donor's fields onto a target type/input definition, once per target.

        Idempotent across schema collections: the target is a global Strawberry object
        shared by every build, so each donor is recorded on the target and applied at
        most once (a second collection skips it rather than re-adding the field). The
        field objects are copied so donor and target never share one. A field name
        already present from a *different* source is a genuine collision and fails fast.
        """

        applied = cast(set, getattr(target, _APPLIED_EXTENSIONS_ATTR, None) or set())
        setattr(target, _APPLIED_EXTENSIONS_ATTR, applied)
        if id(extension) in applied:
            return
        target_def = get_object_definition(target, strict=True)
        donor_def = get_object_definition(cast(type, extension), strict=True)
        existing = {field.name for field in target_def.fields}
        for field in donor_def.fields:
            if field.name in existing:
                raise ImproperlyConfigured(
                    f"extension {surface_name(extension)} adds field {field.name!r} "
                    f"already declared on {target_def.name}"
                )
            target_def.fields.append(copy.copy(field))
            existing.add(field.name)
        applied.add(id(extension))

    def _build(
        self,
        name: str,
    ) -> strawberry.Schema:
        """Build the merged live Strawberry schema named ``name``."""

        self._ensure_type_extensions_applied()
        self._ensure_input_extensions_applied()
        try:
            parts = self.parts[name]
        except KeyError as error:
            available = ", ".join(self.names()) or "none"
            raise ImproperlyConfigured(
                f"GraphQL schema {name!r} has no contributions; available schemas: {available}"
            ) from error

        query = self._merge_root(name, "query", parts.query)
        if query is None:
            raise ImproperlyConfigured(f"GraphQL schema {name!r} has no query root")
        self._assert_rebac_managers(name, parts.types)
        self._describe_choice_enums(parts.types)
        schema = AngeeSchema(
            query=query,
            mutation=self._merge_root(name, "mutation", parts.mutation),
            subscription=self._merge_root(
                name,
                "subscription",
                parts.subscription,
            ),
            types=cast(list[Any], list(parts.types)),
            extensions=cast(
                list[Any],
                [
                    RebacExtension,
                    *parts.extensions,
                    RebacDjangoOptimizerExtension,
                ],
            ),
        )
        return schema

    def render_sdl(self) -> dict[str, str]:
        """Return printed GraphQL SDL for every contributed schema."""

        return {name: self.build(name).as_str() for name in self.names()}

    def _merge_root(
        self,
        schema_name: str,
        key: str,
        surfaces: tuple[object, ...],
    ) -> Any | None:
        """Merge one root bucket after checking field collisions."""

        if not surfaces:
            return None

        owners: dict[str, object] = {}
        for surface in surfaces:
            for field_name in surface_field_names(surface):
                previous = owners.setdefault(field_name, surface)
                if previous is not surface:
                    raise ImproperlyConfigured(
                        f"GraphQL schema {schema_name!r} {key} field "
                        f"{field_name!r} is contributed by both "
                        f"{surface_name(previous)} and {surface_name(surface)}"
                    )
        root = merge_types(
            _ROOT_TYPE_NAMES[key],
            cast(tuple[type, ...], surfaces),
        )
        # Each named schema owns independent field objects: relay field
        # extensions mutate fields in place during build, so a surface shared
        # across schemas must not hand the same field to two schema builds.
        definition = get_object_definition(root, strict=True)
        definition.fields = [copy.copy(field) for field in definition.fields]
        return root

    def _assert_rebac_managers(
        self,
        schema_name: str,
        types: tuple[object, ...],
    ) -> None:
        """Raise when a GraphQL-exposed REBAC model is not manager-scoped."""

        for surface in types:
            model = self._django_model_or_none(surface)
            if model is None or not issubclass(model, RebacMixin):
                continue
            if not isinstance(model._default_manager, RebacManager):
                raise ImproperlyConfigured(
                    f"GraphQL schema {schema_name!r} exposes {model._meta.label} without a RebacManager default manager"
                )

    def _django_model_or_none(
        self,
        surface: object,
    ) -> type[models.Model] | None:
        """Return the strawberry-django model for ``surface`` when present."""

        try:
            return django_model(cast(type, surface))
        except ImproperlyConfigured:
            return None

    def _describe_choice_enums(
        self,
        types: tuple[object, ...],
    ) -> None:
        """Put Django choice labels on Strawberry enum definitions before build."""

        for surface in types:
            model = self._django_model_or_none(surface)
            if model is None:
                continue
            for field in model._meta.get_fields():
                choices_enum = getattr(field, "choices_enum", None)
                if choices_enum is None:
                    continue
                self._describe_choice_enum(cast(Any, choices_enum))

    def _describe_choice_enum(self, choices_enum: Any) -> None:
        """Attach Django choice member labels to the owned Strawberry enum values."""

        definition = getattr(choices_enum, "__strawberry_definition__", None)
        if definition is None:
            strawberry.enum(choices_enum)
            definition = choices_enum.__strawberry_definition__
        labels = {member.value: str(member.label) for member in choices_enum}
        for value in definition.values:
            label = labels.get(value.value)
            if label is not None:
                value.description = label


def schema_parts_for(app_config: AppConfig) -> dict[str, SchemaParts]:
    """Return normalized GraphQL schema parts declared by one addon."""

    raw_schemas = _raw_schemas(app_config)
    if raw_schemas is None:
        return {}
    if not isinstance(raw_schemas, Mapping):
        raise ImproperlyConfigured(f"{app_config.name}.schemas must resolve to a mapping")

    parts: dict[str, SchemaParts] = {}
    for raw_name, raw_entry in raw_schemas.items():
        name = str(raw_name)
        if not isinstance(raw_entry, Mapping):
            raise ImproperlyConfigured(f"{app_config.name}.schemas[{name!r}] must be a mapping")
        parts[name] = SchemaParts.from_mapping(app_config, name, raw_entry)
    return parts


def _raw_schemas(app_config: AppConfig) -> object:
    """Return the raw schema declaration object for one addon, when present."""

    declaration = getattr(app_config, "schemas", None)
    if declaration is None:
        return None
    if isinstance(declaration, Mapping):
        return declaration
    if not isinstance(declaration, str):
        raise ImproperlyConfigured(f"{app_config.name}.schemas must be a mapping or dotted reference")
    return resolve_addon_reference(app_config, declaration, attr="schemas")


def _schema_part_values(
    app_config: AppConfig,
    name: str,
    key: str,
    value: object,
) -> tuple[object, ...]:
    """Return one schema part as a deterministic tuple."""

    if value is None:
        return ()
    if isinstance(value, set | frozenset):
        raise ImproperlyConfigured(f"{app_config.name}.schemas[{name!r}][{key!r}] must be a sequence, not a set")
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return tuple(value)
    return (value,)
