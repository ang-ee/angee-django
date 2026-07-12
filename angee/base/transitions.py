"""Guarded transition methods for ``StateField`` columns.

API contract:

``StateTransitions(field, graph, policy_setting=None)`` opts one ``StateField``
into guarding and binds it to the declared source-to-target graph. Graph keys are
source values or source lists; graph values are target values or target lists.
Values are normalized through the field, so callers may use enum members, stored
values, or the enum member names the field accepts.

``policy_setting`` names a composed Django settings key holding a policy overlay
``{"allow": [[source, target], ...], "deny": [[source, target], ...]}`` whose edge
values are enum values (strings). The overlay is merged over the declared graph
when a transition runs — ``allow`` adds edges, ``deny`` removes them, deny winning
over allow — so a deployment enables or disables specific edges through composed
settings (autoconfig defaults overridden by the project ``settings.yaml``) with no
code change. Reading the overlay at call time is what lets composed settings and
test overrides take effect. A per-company resolution seam (resolving the overlay
through the instance's company) is reserved but not built.

``@transition(field, source=..., target=..., conditions=[...], on_success=...,
policy=...)`` decorates the model's own transition methods. ``source`` is a single
value or a list of values. Conditions are pure ``condition(instance)`` callables;
a false condition raises ``TransitionNotAllowed`` and the method body does not run.
``on_success`` is an explicit ``hook(instance, source, target)`` callback; there is
no signal dispatch. ``policy`` marks a transition whose edge is governed by the
declaration's ``policy_setting``: such an edge need not appear in the declared
graph (it may be default-disabled), so class construction does not require it, and
calling the method while the policy leaves the edge disabled raises
``TransitionNotAllowed``. A policy-enabled edge still runs the declared guards and
conditions. There is no transition-driven verb/surface registry today (transitions
are surfaced by hand-authored mutations), so the marker carries the edge's policy
name for a future surface to exclude a disabled verb; the guard behavior is what
ships now.

The decorated method body runs after the source/graph/condition checks and before
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

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import wraps
from typing import Any, NoReturn, cast

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models, transaction
from django.db.models.query_utils import DeferredAttribute

from angee.base.fields import StateField, enum_member_for

Condition = Callable[[models.Model], bool]
SuccessHook = Callable[[models.Model, Any, Any], None]
TransitionMethod = Callable[..., Any]


class TransitionNotAllowed(Exception):
    """Raised when a guarded transition or direct guarded-field write is illegal."""


@dataclass(frozen=True, slots=True)
class TransitionActionSpec:
    """Public projection of one declared transition method."""

    name: str
    field: str
    sources: tuple[str, ...]
    target: str
    policy: str | None


@dataclass
class _TransitionSpec:
    """Model-method transition declaration captured by ``transition``."""

    field: StateField
    source: Any
    target: Any
    conditions: tuple[Condition, ...]
    on_success: SuccessHook | None
    policy: str | None = None
    name: str = ""
    declaration: StateTransitions | None = None


def transition(
    field: StateField,
    *,
    source: Any,
    target: Any,
    conditions: list[Condition] | tuple[Condition, ...] | None = None,
    on_success: SuccessHook | None = None,
    policy: str | None = None,
) -> Callable[[TransitionMethod], TransitionMethod]:
    """Decorate a model method as a guarded transition for ``field``.

    The matching ``StateTransitions`` declaration validates the source and target
    against its declared graph when the model class is built. At call time the
    wrapper checks the current source against the policy-merged graph, evaluates
    pure conditions, runs the method body, writes the target state, and invokes the
    explicit success hook. The ``save_state`` is the common ``on_success`` hook for
    ordinary models that persist the transitioned state plus fields touched by the
    method body. ``policy`` marks a policy-governed edge (see the module docstring):
    it is validated against the declaration's ``policy_setting`` overlay at call
    time rather than required in the declared graph at class-build time.
    """

    spec = _TransitionSpec(
        field=field,
        source=source,
        target=target,
        conditions=tuple(conditions or ()),
        on_success=on_success,
        policy=policy,
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
    and validates decorated methods against the declared source-to-target graph. An
    optional ``policy_setting`` names a composed settings key whose overlay enables
    or disables edges over that graph at call time (see the module docstring). It
    is intentionally local to the model class: no global registry, no off-model
    flow object, and no hidden success dispatch.
    """

    def __init__(
        self,
        field: StateField,
        graph: Mapping[Any, Any],
        policy_setting: str | None = None,
    ) -> None:
        """Store the field, declared graph, and optional policy-overlay setting."""

        if not isinstance(field, StateField):
            raise TypeError("StateTransitions can guard only a StateField.")
        self.field = field
        self.graph = graph
        self.policy_setting = policy_setting
        self.name = ""
        self._declared: dict[str, set[str]] = {}

    def contribute_to_class(self, cls: type[models.Model], name: str) -> None:
        """Attach the declaration, descriptor guard, method metadata, and helper."""

        self.name = name
        setattr(cls, name, self)
        if getattr(self.field, "model", None) is not cls:
            raise ImproperlyConfigured(f"{cls.__name__}.{name} must be declared after the StateField it guards.")

        self._declared = self._normalize_graph()
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

    @classmethod
    def action_specs(cls, model: type[models.Model]) -> tuple[TransitionActionSpec, ...]:
        """Return frozen action specs declared on ``model``, sorted by method name."""

        specs = cast(dict[str, _TransitionSpec], getattr(model, "_angee_transition_specs", {}))
        return tuple(
            TransitionActionSpec(
                name=name,
                field=spec.field.name or spec.field.attname,
                sources=tuple(_state_key(spec.field, source) for source in _as_values(spec.source)),
                target=_state_key(spec.field, spec.target),
                policy=spec.policy,
            )
            for name, spec in sorted(specs.items(), key=lambda item: item[0])
        )

    def revalidate_for(self, cls: type[models.Model]) -> None:
        """Re-run this declaration's class-build validation against ``cls``'s MRO.

        ``contribute_to_class`` validates only the *declaring* class's own methods
        and installs the guarded descriptor. The composer calls this instead after
        it reorders a materialized child's bases (``child_overrides_parent``), so
        the flipped MRO is proven to still satisfy the same class-build checks —
        every reachable ``@transition`` method still guards a declared or policy
        edge. It validates without mutating ``cls`` (no descriptor install).

        Deliberately stricter than runtime dispatch: at call time a decorated
        method runs under its *own* bound declaration (``spec.declaration``), but
        here every reachable spec that matches this declaration's field is checked
        against *this* declaration's graph — including one bound to another
        declaration for the same field. This over-approximation is what gives the
        flip guard teeth: it rejects a reorder that brings a transition method and
        a narrower same-field declaration together, at build time, rather than
        trusting the emitted MRO to dispatch it to a graph that happens to allow
        it. With the usual single declaration per field the two are equivalent (a
        field's specs are all bound to the one declaration that guards it).
        """

        self._declared = self._normalize_graph()
        seen: set[str] = set()
        for klass in cls.__mro__:
            for method_name, value in vars(klass).items():
                if method_name in seen:
                    continue
                spec = cast(_TransitionSpec | None, getattr(value, "_angee_transition_spec", None))
                if spec is None or not self._matches_field(spec.field):
                    continue
                seen.add(method_name)
                self._validate_declared_transition(spec)

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

        effective = self._effective_allowed()
        if not self._source_matches(spec.source, source_key) or target_key not in effective.get(source_key, set()):
            self._raise_not_allowed(source, target, "source-to-target pair is not allowed")
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

    def force_state(self, instance: models.Model, target: Any, *, reason: str) -> None:
        """Force this field to ``target`` while bypassing the declared graph.

        This is the explicit escape hatch for state targets that are data-dependent
        rather than graph-derived: recovery code may need to reconcile a lifecycle
        from persisted external resource names, and unsaved instances may need an
        initial target before the row exists. It still uses the same guarded field
        write and, for saved rows, the same ``save_state`` concurrency check as a
        declared transition. Callers must pass a concrete ``reason`` so every graph
        bypass is greppable and intentional.
        """

        if not str(reason).strip():
            raise ValueError("StateTransitions.force_state() requires a reason.")
        source = self.field.to_python(getattr(instance, self.field.attname))
        target_value = self.field.to_python(target)
        self._write_target(instance, target_value)
        if instance.pk is None:
            # Unsaved row: the in-memory write is the whole operation, so a
            # caller-set _transition_fields must not leak into a later
            # save_state on the same instance (saved-row-only contract).
            if hasattr(instance, "_transition_fields"):
                delattr(instance, "_transition_fields")
            return
        setattr(instance, "_angee_transition_save_field", self.field.attname)
        try:
            save_state(instance, source, target_value)
        except Exception:
            self._write_target(instance, source)
            raise
        finally:
            if hasattr(instance, "_angee_transition_save_field"):
                delattr(instance, "_angee_transition_save_field")

    def not_allowed(self, source: Any, target: Any) -> NoReturn:
        """Raise the primitive's standard ``TransitionNotAllowed`` for this field."""

        self._raise_not_allowed(source, target, "source-to-target pair is not allowed")

    def _normalize_graph(self) -> dict[str, set[str]]:
        declared: dict[str, set[str]] = {}
        for source_spec, target_spec in self.graph.items():
            target_keys = {self._state_key(target) for target in _as_values(target_spec)}
            for source_key in self._source_keys(source_spec):
                declared.setdefault(source_key, set()).update(target_keys)
        return declared

    def _validate_declared_transition(self, spec: _TransitionSpec) -> None:
        if spec.policy is not None:
            if not self.policy_setting:
                raise ImproperlyConfigured(
                    f"{spec.name} marks {self.field.name} as a policy edge, but {self.name} "
                    "declares no policy_setting to resolve it."
                )
            return
        target_key = self._state_key(spec.target)
        for source_key in self._source_keys(spec.source):
            if target_key not in self._declared.get(source_key, set()):
                raise ImproperlyConfigured(
                    f"{spec.name} declares {self.field.name} from {source_key} "
                    f"to {target_key}, but that pair is not in {self.name}."
                )

    def _effective_allowed(self) -> dict[str, set[str]]:
        """Return the declared graph merged with the policy overlay.

        ``allow`` edges are added and ``deny`` edges removed (deny winning), read
        from composed settings on each call so ``settings.yaml`` overrides and test
        overrides take effect without rebuilding the declaration.
        """

        effective = {source_key: set(targets) for source_key, targets in self._declared.items()}
        overlay = self._policy_overlay()
        for source, target in overlay.get("allow", ()):
            effective.setdefault(self._state_key(source), set()).add(self._state_key(target))
        for source, target in overlay.get("deny", ()):
            targets = effective.get(self._state_key(source))
            if targets is not None:
                targets.discard(self._state_key(target))
        return effective

    def _policy_overlay(self) -> Mapping[str, Any]:
        """Return the composed ``{allow, deny}`` overlay, validated, or empty when unset.

        The overlay is settings-supplied, so its shape is validated here — before
        ``_effective_allowed`` unpacks it mid-transition — and every edge value is
        checked against the guarded field's enum, so a malformed pair or an unknown
        state fails fast with an ``ImproperlyConfigured`` that names the setting
        rather than raising an opaque error while a transition is running.
        """

        if not self.policy_setting:
            return {}
        overlay = getattr(settings, self.policy_setting, None)
        if overlay is None:
            return {}
        if not isinstance(overlay, Mapping):
            raise ImproperlyConfigured(
                f"settings.{self.policy_setting} must be a mapping with 'allow'/'deny' "
                "edge lists, one [source, target] pair each."
            )
        for verb in ("allow", "deny"):
            self._validate_overlay_edges(verb, overlay.get(verb, ()))
        return overlay

    def _validate_overlay_edges(self, verb: str, edges: Any) -> None:
        """Validate one overlay edge list: a sequence of ``[source, target]`` enum pairs."""

        if isinstance(edges, str) or not isinstance(edges, Sequence):
            raise ImproperlyConfigured(
                f"settings.{self.policy_setting}[{verb!r}] must be a list of [source, target] pairs."
            )
        choices_enum = self.field.choices_enum
        for edge in edges:
            if isinstance(edge, str) or not isinstance(edge, Sequence) or len(edge) != 2:
                raise ImproperlyConfigured(
                    f"settings.{self.policy_setting}[{verb!r}] edge {edge!r} must be a [source, target] pair."
                )
            for value in edge:
                if enum_member_for(choices_enum, value) is None:
                    raise ImproperlyConfigured(
                        f"settings.{self.policy_setting}[{verb!r}] edge {edge!r} names unknown state "
                        f"{value!r} for {self.field.name}."
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

    def _raise_not_allowed(self, source: Any, target: Any, reason: str) -> NoReturn:
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
    """Persist a transition-owned state change plus method-touched fields.

    An optimistic concurrency guard brackets the write: the row is locked and its
    committed state re-read, and a divergence from ``source`` (a concurrent
    transition already advanced the row) raises :class:`TransitionNotAllowed` rather
    than silently double-applying the transition — the document-row race that would
    post a ledger entry twice. The guard and the ``save`` share one transaction, and
    the guard only *verifies*; ``instance.save`` still performs the ``source ->
    target`` column write, so ``post_save`` receivers, audit stamping, ``changes``
    publishers, and pre-save change trackers observe the real old->new transition.
    On a locking backend the row lock serializes racers (the loser's re-read blocks
    until the winner commits, then sees the moved state); SQLite has no row lock and
    verifies unlocked — the documented floor.
    """

    field_name = cast(str | None, getattr(instance, "_angee_transition_save_field", None))
    if field_name is None:
        raise ImproperlyConfigured("save_state must run as a StateTransitions on_success hook.")
    fields = {field_name, *cast(set[str], getattr(instance, "_transition_fields", set()))}
    try:
        with transaction.atomic():
            _verify_uncontended_source(instance, field_name, source, target)
            instance.save(update_fields=fields)
    finally:
        if hasattr(instance, "_transition_fields"):
            delattr(instance, "_transition_fields")


def _verify_uncontended_source(instance: models.Model, field_name: str, source: Any, target: Any) -> None:
    """Lock the row and confirm its committed state still equals ``source``.

    Raises :class:`TransitionNotAllowed` when a concurrent transition already left
    ``source``. Runs unscoped (the base manager, elevated when it supports ``sudo``)
    because reading committed state for an integrity guard is a system read on one
    addressed row, mirroring the mixin path-maintenance writer, and routes the lock
    through the queryset's ``lock_if_supported`` owner (backend-gated, SQLite-safe).
    """

    writer = type(instance)._base_manager.all()
    sudo = getattr(writer, "sudo", None)
    if callable(sudo):
        writer = sudo()
    locker = getattr(writer, "lock_if_supported", None)
    reader = locker(of=()) if callable(locker) else writer
    committed = reader.filter(pk=instance.pk).values_list(field_name, flat=True).first()
    field = cast(StateField, instance._meta.get_field(field_name))
    if _state_key(field, committed) != _state_key(field, source):
        raise TransitionNotAllowed(
            _message(field, source, target, "state changed under a concurrent transition")
        )


def revalidate_transition_metadata(cls: type[models.Model]) -> None:
    """Re-validate every ``StateTransitions`` declaration reachable on ``cls``.

    The composer calls this for a materialized child whose base order it flipped
    (``child_overrides_parent``): each declaration was validated when the class
    that *declared* it was built, but the reordered MRO must still satisfy the
    same class-build checks. Raises ``ImproperlyConfigured`` (via the declaration)
    when the reorder leaves a transition method guarding an undeclared edge.
    """

    seen: set[int] = set()
    for klass in cls.__mro__:
        for value in vars(klass).values():
            if isinstance(value, StateTransitions) and id(value) not in seen:
                seen.add(id(value))
                value.revalidate_for(cls)


def _state_key(field: StateField, value: Any) -> str:
    return str(field.to_python(value))


def _message(field: StateField, source: Any, target: Any, reason: str) -> str:
    field_name = field.name or field.attname
    return (
        f"{field_name} transition from {_state_key(field, source)} "
        f"to {_state_key(field, target)} is not allowed: {reason}."
    )
