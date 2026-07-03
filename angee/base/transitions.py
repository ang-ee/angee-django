"""Guarded transition methods for ``StateField`` columns.

API contract:

``StateTransitions(field, allowed)`` opts one ``StateField`` into guarding and
binds it to the allowed source-to-target map. Map keys are source values or
source lists; map values are target values or target lists. Values are
normalized through the field, so callers may use enum members, stored values, or
the enum member names the field accepts.

``@transition(field, source=..., target=..., conditions=[...],
on_success=...)`` decorates the model's own transition methods. ``source`` is a
single value or a list of values. Conditions are pure
``condition(instance)`` callables; a false condition raises
``TransitionNotAllowed`` and the method body does not run. ``on_success`` is an
explicit ``hook(instance, source, target)`` callback; there is no signal dispatch.

The decorated method body runs after the source/map/condition checks and before
the target write. The primitive owns the state write and then calls
``on_success``. It does not save the model; transition methods remain ordinary
model methods and own any persistence of non-state fields. Illegal transitions
raise ``TransitionNotAllowed`` with the field, source, and target in the message.

Direct Python assignment to a guarded field is rejected at descriptor level after
initial model construction. The descriptor still permits initial loading,
idempotent field normalization, and the primitive's own target write, so existing
``StateField`` users are untouched unless they declare ``StateTransitions``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import wraps
from typing import Any, cast

from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models.query_utils import DeferredAttribute

from angee.base.fields import StateField

Condition = Callable[[models.Model], bool]
SuccessHook = Callable[[models.Model, Any, Any], None]
TransitionMethod = Callable[..., Any]


class TransitionNotAllowed(Exception):
    """Raised when a guarded transition or direct guarded-field write is illegal."""


@dataclass
class _TransitionSpec:
    """Model-method transition declaration captured by ``transition``."""

    field: StateField
    source: Any
    target: Any
    conditions: tuple[Condition, ...]
    on_success: SuccessHook | None
    name: str = ""
    declaration: StateTransitions | None = None


def transition(
    field: StateField,
    *,
    source: Any,
    target: Any,
    conditions: list[Condition] | tuple[Condition, ...] | None = None,
    on_success: SuccessHook | None = None,
) -> Callable[[TransitionMethod], TransitionMethod]:
    """Decorate a model method as a guarded transition for ``field``.

    The matching ``StateTransitions`` declaration validates the source and target
    against its allowed map when the model class is built. At call time the
    wrapper checks the current source, evaluates pure conditions, runs the method
    body, writes the target state, and invokes the explicit success hook. The
    ``save_state`` is the common ``on_success`` hook for ordinary models that
    persist the transitioned state plus fields touched by the method body.
    """

    spec = _TransitionSpec(
        field=field,
        source=source,
        target=target,
        conditions=tuple(conditions or ()),
        on_success=on_success,
    )

    def decorate(method: TransitionMethod) -> TransitionMethod:
        @wraps(method)
        def wrapped(instance: models.Model, *args: Any, **kwargs: Any) -> Any:
            declaration = spec.declaration
            if declaration is None:
                raise ImproperlyConfigured(
                    f"{method.__qualname__} is decorated as a transition but no "
                    "StateTransitions declaration guards its field."
                )
            return declaration.run(instance, spec, method, args, kwargs)

        setattr(wrapped, "_angee_transition_spec", spec)
        return wrapped

    return decorate


class StateTransitions:
    """Declaration that guards one ``StateField`` and its model methods.

    Declare this in the model body after the field it guards:

    ``status_transitions = StateTransitions(status, {Status.DRAFT: [Status.READY]})``

    Then decorate the model's own methods with ``@transition(status, ...)``.
    The declaration installs the guarded descriptor only for that opted-in field
    and validates decorated methods against the allowed source-to-target map. It
    is intentionally local to the model class: no global registry, no off-model
    flow object, and no hidden success dispatch.
    """

    def __init__(self, field: StateField, allowed: Mapping[Any, Any]) -> None:
        """Store the field and source-to-target map for class construction."""

        if not isinstance(field, StateField):
            raise TypeError("StateTransitions can guard only a StateField.")
        self.field = field
        self.allowed = allowed
        self.name = ""
        self._allowed: dict[str, set[str]] = {}

    def contribute_to_class(self, cls: type[models.Model], name: str) -> None:
        """Attach the declaration, descriptor guard, method metadata, and helper."""

        self.name = name
        setattr(cls, name, self)
        if getattr(self.field, "model", None) is not cls:
            raise ImproperlyConfigured(f"{cls.__name__}.{name} must be declared after the StateField it guards.")

        self._allowed = self._normalize_allowed()
        setattr(cls, self.field.attname, _GuardedStateDescriptor(self.field))

        method_map = self._method_map_for_class(cls)
        for method_name, value in cls.__dict__.items():
            spec = cast(_TransitionSpec | None, getattr(value, "_angee_transition_spec", None))
            if spec is None or not self._matches_field(spec.field):
                continue
            spec.name = method_name
            spec.declaration = self
            self._validate_declared_transition(spec)
            method_map[method_name] = spec

    def run(
        self,
        instance: models.Model,
        spec: _TransitionSpec,
        method: TransitionMethod,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        """Execute one decorated transition method under this declaration."""

        source = self.field.to_python(getattr(instance, self.field.attname))
        target = self.field.to_python(spec.target)
        source_key = self._state_key(source)
        target_key = self._state_key(target)

        if not self._source_matches(spec.source, source_key) or not self._target_allowed(source_key, target_key):
            self._raise_not_allowed(source, target, "source-to-target pair is not declared")
        for condition in spec.conditions:
            if not condition(instance):
                self._raise_not_allowed(source, target, "condition returned false")

        result = method(instance, *args, **kwargs)
        self._write_target(instance, target)
        if spec.on_success is not None:
            setattr(instance, "_angee_transition_save_field", self.field.attname)
            try:
                spec.on_success(instance, source, target)
            finally:
                delattr(instance, "_angee_transition_save_field")
        return result

    def _normalize_allowed(self) -> dict[str, set[str]]:
        allowed: dict[str, set[str]] = {}
        for source_spec, target_spec in self.allowed.items():
            target_keys = {self._state_key(target) for target in _as_values(target_spec)}
            for source_key in self._source_keys(source_spec):
                allowed.setdefault(source_key, set()).update(target_keys)
        return allowed

    def _validate_declared_transition(self, spec: _TransitionSpec) -> None:
        target_key = self._state_key(spec.target)
        for source_key in self._source_keys(spec.source):
            if not self._target_allowed(source_key, target_key):
                raise ImproperlyConfigured(
                    f"{spec.name} declares {self.field.name} from {source_key} "
                    f"to {target_key}, but that pair is not in {self.name}."
                )

    def _method_map_for_class(self, cls: type[models.Model]) -> dict[str, _TransitionSpec]:
        existing = cast(dict[str, _TransitionSpec], getattr(cls, "_angee_transition_specs", {}))
        method_map = dict(existing)
        setattr(cls, "_angee_transition_specs", method_map)
        return method_map

    def _spec_for(self, instance: models.Model, method_name: str) -> _TransitionSpec:
        specs = cast(dict[str, _TransitionSpec], getattr(instance.__class__, "_angee_transition_specs", {}))
        try:
            return specs[method_name]
        except KeyError as error:
            raise AttributeError(f"{instance.__class__.__name__} has no transition method {method_name!r}.") from error

    def _matches_field(self, field: StateField) -> bool:
        return field is self.field or (
            getattr(field, "name", None) == self.field.name and getattr(field, "attname", None) == self.field.attname
        )

    def _source_matches(self, source_spec: Any, source_key: str) -> bool:
        source_keys = self._source_keys(source_spec)
        return source_key in source_keys

    def _target_allowed(self, source_key: str, target_key: str) -> bool:
        return target_key in self._allowed.get(source_key, set())

    def _source_keys(self, source_spec: Any) -> tuple[str, ...]:
        return tuple(self._state_key(source) for source in _as_values(source_spec))

    def _state_key(self, value: Any) -> str:
        return _state_key(self.field, value)

    def _write_target(self, instance: models.Model, target: Any) -> None:
        active = cast(set[str] | None, getattr(instance, "_angee_transition_write_fields", None))
        created = active is None
        if active is None:
            active = set()
            setattr(instance, "_angee_transition_write_fields", active)
        active.add(self.field.attname)
        try:
            setattr(instance, self.field.attname, target)
        finally:
            active.discard(self.field.attname)
            if created:
                delattr(instance, "_angee_transition_write_fields")

    def _raise_not_allowed(self, source: Any, target: Any, reason: str) -> None:
        raise TransitionNotAllowed(_message(self.field, source, target, reason))


class _GuardedStateDescriptor(DeferredAttribute):
    """Descriptor that blocks direct changes to an opted-in state field."""

    def __set__(self, instance: models.Model, value: Any) -> None:
        """Store only initial, idempotent, or transition-owned values."""

        field = cast(StateField, self.field)
        target = field.to_python(value)
        if field.attname not in instance.__dict__:
            instance.__dict__[field.attname] = target
            return

        source = instance.__dict__[field.attname]
        active = cast(set[str] | None, getattr(instance, "_angee_transition_write_fields", None))
        if (active is not None and field.attname in active) or _state_key(field, source) == _state_key(field, target):
            instance.__dict__[field.attname] = target
            return

        raise TransitionNotAllowed(_message(field, source, target, "direct assignment is not allowed"))


def _as_values(value: Any) -> tuple[Any, ...]:
    if isinstance(value, list | tuple | set | frozenset):
        return tuple(value)
    return (value,)


def save_state(instance: models.Model, source: Any, target: Any) -> None:
    """Persist a transition-owned state change plus method-touched fields."""

    del source, target
    field_name = cast(str | None, getattr(instance, "_angee_transition_save_field", None))
    if field_name is None:
        raise ImproperlyConfigured("save_state must run as a StateTransitions on_success hook.")
    fields = {field_name, *cast(set[str], getattr(instance, "_transition_fields", set()))}
    try:
        instance.save(update_fields=fields)
    finally:
        if hasattr(instance, "_transition_fields"):
            delattr(instance, "_transition_fields")


def _state_key(field: StateField, value: Any) -> str:
    return str(field.to_python(value))


def _message(field: StateField, source: Any, target: Any, reason: str) -> str:
    field_name = field.name or field.attname
    return (
        f"{field_name} transition from {_state_key(field, source)} "
        f"to {_state_key(field, target)} is not allowed: {reason}."
    )
