"""REBAC declarations and authorization boundaries shared by framework layers."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from functools import lru_cache
from os import PathLike
from pathlib import Path
from typing import Any, cast, overload

from django.apps import AppConfig, apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models
from django.db.models.query import EmptyQuerySet
from rebac import (
    PermissionDenied,
    SubjectRef,
    check_new,
    current_actor,
    proposed_forward_relationships,
    to_subject_ref,
)
from rebac import user_subject_type as _user_subject_type
from rebac.backends import backend
from rebac.links import inherits_through_links, is_generated
from rebac.managers import RebacQuerySet
from rebac.relation_loading import relation_actor
from rebac.resources import model_for_resource_type, model_resource_type
from rebac.schema import Definition, PermArrow, PermBinOp, PermExpr, PermRef, Schema, resolve_schema_path
from rebac.schema.introspection import relation_dependencies
from rebac.schema.parser import parse_zed


def declared_grants(model: type[models.Model]) -> dict[str, str]:
    """Validate the record-share relations declared on this model itself."""

    raw = model.__dict__.get("rebac_grantable", {})
    if not isinstance(raw, Mapping):
        raise ImproperlyConfigured(f"{model._meta.label}.rebac_grantable must be a mapping.")
    declaration: dict[str, str] = {}
    for relation, permission in raw.items():
        if not isinstance(relation, str) or not relation:
            raise ImproperlyConfigured(
                f"{model._meta.label}.rebac_grantable relation names must be non-empty strings."
            )
        if not isinstance(permission, str) or not permission:
            raise ImproperlyConfigured(f"{model._meta.label}.rebac_grantable[{relation!r}] must name a permission.")
        declaration[relation] = permission
    return declaration


_AMBIENT: Any = object()
"""``require_rebac_queryset``'s default: no actor argument, so REBAC's ambient scope answers."""


def require_rebac_queryset[M: models.Model](model: type[M], *, actor: Any = _AMBIENT) -> RebacQuerySet[M]:
    """Preserve the default manager's filters and require its native REBAC query contract.

    A given ``actor`` is pinned, and an explicit ``None`` fails closed: it reads
    nothing, whatever the ambient actor or system context. Without the argument,
    REBAC's ambient scope answers: system context reads unscoped and an actorless
    read fails closed.
    """

    queryset = model._default_manager.get_queryset()
    if not isinstance(queryset, RebacQuerySet):
        raise ImproperlyConfigured(f"{model._meta.label} requires a RebacQuerySet default manager.")
    if actor is _AMBIENT:
        return queryset
    return queryset.with_actor(actor) if actor is not None else queryset.none()


@overload
def reader_queryset[Q: RebacQuerySet[Any]](source: Q, actor: Any) -> Q: ...


@overload
def reader_queryset[M: models.Model](source: type[M], actor: Any) -> RebacQuerySet[M]: ...


def reader_queryset(source: type[models.Model] | RebacQuerySet[Any], actor: Any) -> RebacQuerySet[Any]:
    """Read as a pinned actor when given, otherwise through REBAC's ambient scope.

    An existing queryset retains its subtype and filters. Unlike
    ``require_rebac_queryset(..., actor=None)``, an absent reader permits ambient
    system work; without an ambient actor or system scope, reads fail closed.
    """

    rows = source if isinstance(source, RebacQuerySet) else require_rebac_queryset(source)
    return (rows.with_actor(actor) if actor is not None else rows).with_action("read")


def relation_read_queryset[M: models.Model](model: type[M], source: models.QuerySet[Any]) -> RebacQuerySet[M]:
    """``model``'s ``read`` queryset for the rows ``source`` relates to, read as ``source`` reads.

    This is the one rule for an actorless relation read. The reader is REBAC's
    ``relation_actor(source)``: the source's pinned actor, else the ambient one.
    With neither, REBAC's ambient scope answers, as it does for ``source``
    itself: system context reads unscoped and an actorless read fails closed.
    An emptied ``source`` reads nothing, as it does itself. A read that names no
    reader on purpose (``require_rebac_queryset(..., actor=None)``) is policy
    only where its owner's docstring says so, as message pointers and permission
    projections do.
    """

    if isinstance(source, EmptyQuerySet):
        return require_rebac_queryset(model, actor=None).with_action("read").none()
    return reader_queryset(model, relation_actor(source))


def scoped_relation_read[M: models.Model](model: type[M], source: models.QuerySet[Any]) -> RebacQuerySet[M]:
    """:func:`relation_read_queryset`, eagerly scoped for SQL subquery consumers.

    An emptied ``source`` stays an empty, unscoped queryset: there is no reader to
    resolve and no bypass to audit, so declaration-time expression probes compose
    without a database.
    """

    related = relation_read_queryset(model, source)
    return related if isinstance(source, EmptyQuerySet) else related.scoped()


@dataclass(frozen=True, slots=True)
class _RebacSchemaBinding:
    source: Path | None
    declaration: str | PathLike[str] | None
    effective: str | PathLike[str] | None
    signature: tuple[Path, int, int, int, int, int] | None = None
    schema: Schema | None = None


SCHEMA_BINDING = "_angee_rebac_schema_binding"
"""The ``AppConfig.__dict__`` key holding its schema binding; test fixtures restore it with ``rebac_schema``."""


def declared_schema_path(app_config: AppConfig) -> Path | None:
    """Retain the native authored declaration before binding generated output."""

    binding = app_config.__dict__.get(SCHEMA_BINDING)
    declaration = getattr(app_config, "rebac_schema", None)
    if not isinstance(binding, _RebacSchemaBinding) or declaration != binding.effective:
        binding = _RebacSchemaBinding(resolve_schema_path(app_config), declaration, declaration)
        app_config.__dict__[SCHEMA_BINDING] = binding
    return binding.source


def bind_rebac_schema(app_config: AppConfig, path: Path | None) -> None:
    """Bind generated input; ``None`` restores the original native declaration."""

    source = declared_schema_path(app_config)
    binding = app_config.__dict__[SCHEMA_BINDING]
    assert isinstance(binding, _RebacSchemaBinding)
    effective = path if path is not None else binding.declaration
    setattr(app_config, "rebac_schema", effective)
    app_config.__dict__[SCHEMA_BINDING] = _RebacSchemaBinding(source, binding.declaration, effective)


def merged_schema_relpath(package: str) -> Path:
    """Return the permission artifact address relative to compiler output."""

    return Path('permissions') / f'{package}.zed'


def effective_rebac_definition(model: type[models.Model]) -> Definition | None:
    """Read request policy through the native synced, scope-pinned backend.

    This is the published policy: its declared names and relations are what a
    caller lists, renders and validates. A proof about what REBAC decides reads
    :func:`evaluated_rebac_definition` instead.
    """

    resource_type = model_resource_type(model)
    return backend().schema().get_definition(resource_type) if resource_type else None


def evaluated_rebac_schema() -> Schema:
    """The policy REBAC decides with: ``rebac.backend().evaluated_schema()``.

    It is the published policy unless ``REBAC_INHERIT_THROUGH_LINKS = False``
    narrows it, so that an arrow through a link lends only what the linked record
    grants directly. Framework code that proves something about decisions (that
    one action stands in for another, that a rule answers alike for every row of
    a record, that reading a record reads another) reads this policy; a proof made
    over the published one can be false here and then shows what REBAC denies.
    It may hold permissions the narrowing generates (``rebac.links.is_generated``):
    they are not actions, so a proof never names one to a user or a REBAC entry.
    """

    return backend().evaluated_schema()


def evaluated_rebac_definition(model: type[models.Model]) -> Definition | None:
    """``model``'s definition in the policy REBAC decides with (:func:`evaluated_rebac_schema`)."""

    resource_type = model_resource_type(model)
    return evaluated_rebac_schema().get_definition(resource_type) if resource_type else None


def _published_and_evaluated() -> tuple[Schema, Schema]:
    """The published policy and the one REBAC decides with, read once while they are one policy."""

    published = backend().schema()
    return published, published if inherits_through_links() else evaluated_rebac_schema()


# ---------- The policy narrowed at links (``REBAC_INHERIT_THROUGH_LINKS = False``) ----------

EVALUATED_POLICY_CAPABILITY = "angee.rebac"
"""The addon table that declares ``evaluated_policy = true`` (:func:`undeclared_policy_apps`)."""

EVALUATED_POLICY_ATTRIBUTE = "rebac_evaluated_policy"
"""The ``AppConfig`` attribute an app that is no Angee addon sets to ``True`` to declare the same."""


def links_narrowed() -> bool:
    """Whether REBAC decides with the policy narrowed at links: ``REBAC_INHERIT_THROUGH_LINKS = False``.

    The framework reads the library's setting here only. While it is ``False`` a
    reader that reached a record only through a link reaches it no more, and
    framework code lets REBAC decide wherever it would otherwise lend a row
    through a link (a record's chatter to the record's readers, a party's name to
    a task's readers). Reading ``False`` refuses (``ImproperlyConfigured``,
    ``angee.E055``) while an installed app that has not declared its reads follow
    that policy is installed (:func:`undeclared_policy_apps`), so a path that
    consults the setting fails rather than decide with rules nobody read; the
    base app refuses to start the same way. A value that is not a bool is the
    library's ``rebac.E028`` and raises too. With the default ``True`` nothing
    else is read.
    """

    if inherits_through_links():
        return False
    missing = undeclared_policy_apps()
    if missing:
        raise ImproperlyConfigured(
            "REBAC_INHERIT_THROUGH_LINKS = False needs every installed addon, and every other app that binds a "
            f"REBAC policy, to declare that its reads follow the policy REBAC decides with; these do not: "
            f"{', '.join(missing)} (angee.E055). Audit each one's rules that lend a row through a link, then "
            f"declare `evaluated_policy = true` under [tool.angee.rebac] in its pyproject.toml (an app that is no "
            f"addon sets `{EVALUATED_POLICY_ATTRIBUTE} = True` on its AppConfig); or keep the setting True."
        )
    return True


def undeclared_policy_apps() -> tuple[str, ...]:
    """The installed apps that have not declared their reads follow the policy REBAC decides with.

    Every Angee addon declares it in its ``pyproject.toml``
    (``[tool.angee.rebac] evaluated_policy = true``); every other installed app
    that binds a REBAC policy declares it on its ``AppConfig``
    (``rebac_evaluated_policy = True``). The framework cannot vouch for code it
    has not read: such an app may lend rows through links by rules of its own.
    """

    return _undeclared_policy_apps(tuple(config.name for config in apps.get_app_configs()))


@lru_cache(maxsize=8)
def _undeclared_policy_apps(names: tuple[str, ...]) -> tuple[str, ...]:
    # Addon identity reads installed metadata; this module imports before the addon index.
    from django_compose.components import installed_components

    from angee.base.addons import addon_facts, is_angee_addon

    del names
    index = installed_components("angee.addons")
    missing = []
    for config in apps.get_app_configs():
        if is_angee_addon(config, index=index):
            declared = addon_facts(config.name, EVALUATED_POLICY_CAPABILITY, index=index).get("evaluated_policy")
        elif declared_schema_path(config) is not None:
            declared = getattr(config, EVALUATED_POLICY_ATTRIBUTE, None)
        else:
            continue
        if declared is not True:
            missing.append(config.name)
    return tuple(sorted(missing))


@dataclass(frozen=True)
class PolicyArm:
    """One arm of the published policy an elevated read relies on, by the identity ``rebac.links`` reports.

    ``resource_type#permission`` is the permission the arm is written in;
    ``relation->target`` is the arrow (``rebac.links.NarrowedArrow``: resource
    type, permission, relation, target), or ``relation`` itself when ``target``
    is empty. An elevated read in a reader path names the arm its rows are
    disclosed by: "every holder of ``target`` on the record ``relation`` names
    may read these rows". Arms are declared once, at module level, so that the
    framework's tests can enumerate every one (:data:`POLICY_ARMS`).
    """

    resource_type: str
    permission: str
    relation: str
    target: str = ""

    def __post_init__(self) -> None:
        POLICY_ARMS.add(self)

    def __str__(self) -> str:
        arm = f"{self.relation}->{self.target}" if self.target else self.relation
        return f"{self.resource_type}#{self.permission}: {arm}"


POLICY_ARMS: set[PolicyArm] = set()
"""Every :class:`PolicyArm` declared in an imported module."""


@dataclass(frozen=True)
class ShownFacts:
    """Facts of ``resource_type`` rows the framework shows beyond the rows' own read, to whom, and why.

    An elevated read that discloses only such facts (a connection's vendor branding
    beside the connection, a record's own change history to the record's readers)
    relies on no arrow of the policy: whoever sees them was admitted by another
    REBAC decision, so the policy narrowed at links does not change it. Declared
    once, at module level, so that the framework's tests can enumerate every one
    (:data:`SHOWN_FACTS`).
    """

    resource_type: str
    facts: str

    def __post_init__(self) -> None:
        SHOWN_FACTS.add(self)

    def __str__(self) -> str:
        return f"{self.resource_type}: {self.facts}"


SHOWN_FACTS: set[ShownFacts] = set()
"""Every :class:`ShownFacts` declared in an imported module."""


def arm_holds(arm: PolicyArm | ShownFacts) -> bool:
    """Whether REBAC still lends ``arm`` whole: it is written, unchanged, in the policy REBAC decides with.

    With the published policy evaluated it holds, and no policy is read. Under
    ``REBAC_INHERIT_THROUGH_LINKS = False`` the evaluated policy is read
    (``rebac.backend().evaluated_schema()``, the very object decisions read): an
    arrow the narrowing lends only in part is renamed there (``relation->lent(P)``),
    so it is not found, and neither is an arm the policy spells otherwise (through
    another permission, a relation of another name) or does not state. Only an arm
    found in a granting position of ``resource_type#permission`` holds. Nothing is
    kept between calls: the answer is the evaluated policy's now.
    """

    if isinstance(arm, ShownFacts) or not links_narrowed():
        return True
    definition = evaluated_rebac_schema().get_definition(arm.resource_type)
    permission = next((p for p in definition.permissions if p.name == arm.permission), None) if definition else None
    if permission is None:
        return False
    return any(
        (isinstance(node, PermArrow) and (node.via, node.target) == (arm.relation, arm.target))
        or (not arm.target and isinstance(node, PermRef) and node.name == arm.relation)
        for node in _granting_leaves(permission.expression)
    )


def _granting_leaves(expression: PermExpr) -> Iterator[PermExpr]:
    """The leaves of ``expression`` under an even number of right-hand sides of ``-``."""

    pending: list[tuple[PermExpr, bool]] = [(expression, False)]
    while pending:
        node, negative = pending.pop()
        if isinstance(node, PermBinOp):
            pending.append((node.left, negative))
            pending.append((node.right, negative ^ (node.op == "-")))
        elif not negative:
            yield node


_READ_OPERATION: ContextVar[bool] = ContextVar("angee_read_operation", default=False)
"""Set while a read operation (a GraphQL query) resolves: :func:`read_operation`."""

_ELEVATING_FOR: ContextVar[PolicyArm | ShownFacts | None] = ContextVar("angee_elevating_for", default=None)
"""The arm :func:`elevated_read` names while it elevates a queryset."""


class _Unnamed:
    """A queryset elevated by ``sudo()`` or ``system_context()`` that names no policy arm."""

    def __repr__(self) -> str:
        return "UNNAMED_ELEVATION"


UNNAMED_ELEVATION: Any = _Unnamed()
"""The elevation mark of a framework queryset elevated outside :func:`elevated_read`."""


def elevation_mark() -> PolicyArm | ShownFacts | Any:
    """The mark a framework queryset's ``sudo()`` or ``system_context()`` carries: its arm, else unnamed."""

    return _ELEVATING_FOR.get() or UNNAMED_ELEVATION


@contextmanager
def read_operation() -> Iterator[None]:
    """Resolve the block as a read operation: under the narrowed policy an elevated read in it names its arm.

    The GraphQL schema wraps each query operation in it. While
    :func:`links_narrowed`, a framework queryset (``AngeeQuerySet``) elevated by
    its ``sudo()`` or ``system_context()`` refuses to read inside the block unless
    it was elevated by :func:`elevated_read`, which names the policy arm the read
    relies on and elevates only while that arm holds: a reader path that forgets
    fails instead of showing rows REBAC denies. With the default nothing changes.
    """

    token = _READ_OPERATION.set(True)
    try:
        yield
    finally:
        _READ_OPERATION.reset(token)


def refuse_unnamed_elevation(queryset: models.QuerySet[Any]) -> None:
    """Refuse to read a queryset elevated without a named arm inside a read operation under the narrowed policy.

    Framework querysets call it before they read. Outside a read operation, and
    with the published policy evaluated, it reads nothing and returns.
    """

    if not _READ_OPERATION.get() or getattr(queryset, "_angee_elevation", None) is not UNNAMED_ELEVATION:
        return
    if not cast(RebacQuerySet[Any], queryset).is_sudo() or not links_narrowed():
        return
    raise PermissionDenied(
        f"An elevated read of {queryset.model._meta.label} in a read operation names no policy arm "
        "(REBAC_INHERIT_THROUGH_LINKS = False; angee.base.permissions.elevated_read)."
    )


def elevated_read[Q: models.QuerySet[Any]](
    queryset: Q, arm: PolicyArm | ShownFacts, *, reason: str, system: bool = False,
) -> Q:
    """``queryset`` read elevated because ``arm`` discloses its rows; else as its actor reads it.

    While ``arm`` holds (:func:`arm_holds`) this is ``queryset.sudo(reason=...)``,
    marked as relying on ``arm``. Where it does not, the queryset is returned as
    it is: REBAC decides each row for its actor, so a reader the arm no longer
    lends to sees what REBAC lets them see and nothing more. A read that shows
    only :class:`ShownFacts` relies on no arm and always elevates. ``system``
    elevates with ``system_context()`` instead, which holds where sudo is disabled.
    """

    if not arm_holds(arm):
        return queryset
    token = _ELEVATING_FOR.set(arm)
    try:
        rows = cast(Any, queryset)
        return cast(Q, rows.system_context(reason=reason) if system else rows.sudo(reason=reason))
    finally:
        _ELEVATING_FOR.reset(token)


def declared_rebac_schema(app_config: AppConfig) -> Schema | None:
    """The declaration this AppConfig binds (a host's composed file, else the authored one), parsed once.

    The parse is reused while its source identity is unchanged. State lives only
    on this AppConfig's binding. Native path discovery still observes missing files
    and declaration changes; nanosecond modification/change times and inode identity
    invalidate same-path rewrites and atomic replacement. Request policy reads
    ``effective_rebac_definition`` (the synced store with its overrides), never this
    startup snapshot.
    """

    declared_schema_path(app_config)
    binding = app_config.__dict__[SCHEMA_BINDING]
    assert isinstance(binding, _RebacSchemaBinding)
    schema_path = resolve_schema_path(app_config)
    if schema_path is None:
        if binding.signature is not None:
            app_config.__dict__[SCHEMA_BINDING] = replace(binding, signature=None, schema=None)
        return None
    status = schema_path.stat()
    signature = (
        schema_path, status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns, status.st_ctime_ns,
    )
    if signature != binding.signature:
        binding = replace(
            binding, signature=signature, schema=parse_zed(schema_path.read_text(encoding="utf-8")),
        )
        app_config.__dict__[SCHEMA_BINDING] = binding
    return binding.schema


def declared_rebac_definition(model: type[models.Model]) -> Definition | None:
    """Read one model declaration without consulting database-backed policy."""

    resource_type = model_resource_type(model)
    if not resource_type:
        return None
    try:
        config = apps.get_app_config(model._meta.app_label)
    except LookupError:
        return None
    schema = declared_rebac_schema(config)
    return schema.get_definition(resource_type) if schema is not None else None


def contextual_action_holds(model: type[models.Model], action: str) -> bool:
    """Whether a listing constrained to readable parents may scope ``model`` by ``action`` instead of ``read``.

    The policy REBAC decides with (:func:`evaluated_rebac_schema`: the synced
    schema with its overrides, narrowed where ``REBAC_INHERIT_THROUGH_LINKS`` says
    so) must state the contextual child action as exactly one arrow,
    ``relation->parent_action``, while ``read`` stays a union with an arrow arm
    through the same relation: the child's own actor-scoped read checks its live
    parent, so the listing selects the parents with joins instead of a second,
    separately scoped parent subquery. Any other arm on the action (a direct grant
    beside the arrow) could admit a child of an unreadable parent and leaves it
    unproved, as does an exclusion or intersection over ``read`` (a tightening
    override); extending ``read`` does not. A ``parent_action`` narrower than the
    parent's permission the arm names must hold on the parent as a purpose read
    (:func:`purpose_read_holds`). Where the narrowing lends the parent's actions
    through the arrow under generated names, the arms no longer name the same
    action and the proof fails closed. Unproved, the caller keeps ``read`` inside
    the scoped parent population. Field gates are unaffected; they keep their own
    ``read__<field>`` checks under either action.
    """

    resource_type = model_resource_type(model)
    return resource_type is not None and _contextual_holds(*_published_and_evaluated(), resource_type, action)


def purpose_read_holds(model: type[models.Model], action: str) -> bool:
    """Whether a read of ``model``'s own population may check its purpose read ``action`` instead of ``read``.

    A purpose read, such as ``messaging/thread#read_inbox``, holds while the
    published effective policy states it and ``read`` exactly as the model's
    composed declaration does, whose owner proves the purpose read answers as
    ``read`` within the population its read selects; an override on either, or a
    store not yet synced to the declaration, leaves it unproved. The arrows are
    read from the policy REBAC decides with (:func:`evaluated_rebac_schema`): an
    arrow to a parent's narrower action, where ``read`` takes the parent's
    ``read``, holds only while that parent's purpose read holds in turn, and an
    arrow the narrowing renames proves nothing. Unproved, the caller keeps ``read``.
    """

    resource_type = model_resource_type(model)
    return resource_type is not None and _purpose_holds(*_published_and_evaluated(), resource_type, action)


def _narrow_and_read(schema: Schema, resource_type: str, action: str) -> tuple[Definition, PermExpr, PermExpr] | None:
    """The effective definition with its ``action`` and ``read`` expressions, when it states both."""

    definition = schema.get_definition(resource_type)
    if definition is None:
        return None
    expressions = {permission.name: permission.expression for permission in definition.permissions}
    narrow, read = expressions.get(action), expressions.get("read")
    return (definition, narrow, read) if narrow is not None and read is not None else None


def _contextual_holds(published: Schema, evaluated: Schema, resource_type: str, action: str) -> bool:
    found = _narrow_and_read(evaluated, resource_type, action)
    if found is None:
        return False
    definition, narrow, read = found
    if not isinstance(narrow, PermArrow):
        return False
    arms = {arm.via: arm.target for arm in _union_arms(read) if isinstance(arm, PermArrow)}
    return _parent_holds(published, evaluated, definition, narrow, arms.get(narrow.via))


def _purpose_holds(published: Schema, evaluated: Schema, resource_type: str, action: str) -> bool:
    """The override detection reads the published policy; the arrows, the evaluated one."""

    effective = _narrow_and_read(published, resource_type, action)
    found = _narrow_and_read(evaluated, resource_type, action)
    if effective is None or found is None:
        return False
    model = model_for_resource_type(resource_type)
    declared = declared_rebac_definition(model) if model is not None else None
    stated = {permission.name: permission.expression for permission in declared.permissions} if declared else {}
    if stated.get(action) != effective[1] or stated.get("read") != effective[2]:
        return False
    definition, narrow, read = found
    reads = {(arrow.via, arrow.target) for arrow in _arrows(read)}
    return all(
        _parent_holds(published, evaluated, definition, arrow, "read" if (arrow.via, "read") in reads else None)
        for arrow in _arrows(narrow)
        if (arrow.via, arrow.target) not in reads
    )


def _parent_holds(
    published: Schema, evaluated: Schema, definition: Definition, arrow: PermArrow, target: str | None,
) -> bool:
    """Whether ``arrow``'s parent action stands in for ``target``, the parent permission ``read`` takes.

    A generated target is no declared action, so it is a purpose read of nothing.
    """

    return target is not None and (
        arrow.target == target
        or target == "read"
        and not is_generated(arrow.target)
        and _purpose_holds(published, evaluated, _parent_type(definition, arrow.via), arrow.target)
    )


def _parent_type(definition: Definition, via: str) -> str:
    """The one resource type the relation ``via`` stores, else an empty name that no definition has."""

    relation = next((relation for relation in definition.relations if relation.name == via), None)
    types = {subject.type for subject in relation.allowed_subjects} if relation is not None else set()
    return types.pop() if len(types) == 1 else ""


def _arrows(expression: PermExpr) -> Iterator[PermArrow]:
    """Yield the arrows anywhere in ``expression``, without following sub-permissions."""

    if isinstance(expression, PermArrow):
        yield expression
    elif isinstance(expression, PermBinOp):
        yield from _arrows(expression.left)
        yield from _arrows(expression.right)


def _union_arms(expression: PermExpr) -> Iterator[PermExpr]:
    """Yield the operands of a top-level union; any other expression is one arm."""

    if isinstance(expression, PermBinOp) and expression.op == "+":
        yield from _union_arms(expression.left)
        yield from _union_arms(expression.right)
    else:
        yield expression


def instance_actor(instance: models.Model) -> SubjectRef | None:
    """Return the instance's pinned actor, falling back to the ambient actor.

    Instance verbs and audit defaults share this resolution, including during
    elevated writes. Plain Django models have only the ambient actor.
    """

    # Schema declarations also load before Django setup; the native mixin is model-defined.
    from rebac import RebacMixin

    actor = instance.actor() if isinstance(instance, RebacMixin) else None
    return actor if actor is not None else current_actor()


def user_subject_ref(*, user: models.Model | None = None, user_id: Any = None) -> SubjectRef:
    """Return a user's canonical subject identity without loading a bare primary key.

    A supplied row uses REBAC's native conversion. A bare key uses REBAC's user
    subject type, the inverse of :func:`actor_user_id`. Callers requiring proof
    that the user exists must read that row explicitly before constructing its ref.
    """

    if user is not None:
        return to_subject_ref(user)
    if user_id is None:
        raise ValueError("A user or user_id is required to build a subject reference.")
    return SubjectRef.of(_user_subject_type(), str(user_id))


def actor_user_id(actor: Any) -> Any | None:
    """Return ``actor``'s user primary key, or ``None`` when no user backs it.

    Model-backed REBAC subjects use the database primary key, so the canonical
    actor id is already the value required by user foreign-key columns.
    Non-user subjects return ``None``.
    """

    if actor is None or not actor.subject_id:
        return None
    if actor.subject_type != _user_subject_type():
        return None
    pk = get_user_model()._meta.pk
    if pk is None:
        return None
    try:
        return pk.to_python(actor.subject_id)
    except (TypeError, ValueError, ValidationError):
        return None


def subject_reaches_user(subject: models.Model | SubjectRef, user_id: Any) -> bool:
    """Return whether ``subject`` resolves to, or contains, the user with ``user_id``.

    A subject reaches a user when it is that user's own reference, a wildcard,
    or a userset such as ``auth/group:7#member`` whose relation the user holds on
    the referenced object. A record invariant that keeps one user out of its
    holders composes this before a grant, an assignment, or an admission and
    raises :class:`angee.base.exceptions.RecordAccessSubjectRefused` on ``True``.
    ``user_id`` of ``None`` names nobody and is never reached.
    """

    if user_id is None:
        return False
    reference = to_subject_ref(subject)
    if reference.subject_id == "*":
        return True
    user = user_subject_ref(user_id=user_id)
    if reference == user:
        return True
    if not reference.optional_relation:
        return False

    return backend().check_access(subject=user, action=reference.optional_relation, resource=reference.object).allowed


@dataclass(frozen=True, slots=True)
class DirectRecordAccess:
    """One direct declared-relation tuple on a shareable record."""

    relation: str
    subject: SubjectRef


@dataclass(frozen=True, slots=True)
class CandidateRelationships:
    """The relations a row would carry for one action, as rebac's candidate preflight reads them.

    This adapter composes rebac's public field-backed candidate projection.
    A caller asks about a row that is not, or not yet, the stored one (an
    unsaved task's assignee, a retained history snapshot) and checks a subject
    against the relations its fields and ledger would give it.
    """

    resource_type: str
    definition: Definition
    required: frozenset[str]
    relationships: dict[str, tuple[SubjectRef, ...] | None]

    def allows(self, subject: Any, action: str) -> bool:
        """Whether ``subject`` may perform ``action`` on the candidate these relations describe."""

        return check_new(
            subject=to_subject_ref(subject), action=action, resource_type=self.resource_type,
            relationships=self.relationships,
        ).allowed


def candidate_relationships(instance: models.Model, action: str) -> CandidateRelationships | None:
    """Read the relations ``instance`` would carry for ``action``; ``None`` when its type has no definition."""

    resource_type = model_resource_type(type(instance))
    if resource_type is None:
        return None

    schema = backend().schema()
    definition = schema.get_definition(resource_type)
    if definition is None:
        return None
    required = frozenset(relation_dependencies(schema, resource_type, action))
    relationships = proposed_forward_relationships(instance, definition, required_relations=required)
    return CandidateRelationships(resource_type, definition, required, relationships)
