"""REBAC declarations and authorization boundaries shared by framework layers."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from os import PathLike
from pathlib import Path
from typing import Any

from django.apps import AppConfig, apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models
from rebac import SubjectRef, check_new, current_actor, proposed_forward_relationships, to_subject_ref
from rebac import user_subject_type as _user_subject_type
from rebac.backends import backend
from rebac.managers import RebacQuerySet
from rebac.resources import model_for_resource_type, model_resource_type
from rebac.schema import Definition, PermArrow, PermBinOp, PermExpr, Schema, resolve_schema_path
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


@dataclass(frozen=True, slots=True)
class _RebacSchemaBinding:
    source: Path | None
    declaration: str | PathLike[str] | None
    effective: str | PathLike[str] | None
    signature: tuple[Path, int, int, int, int, int] | None = None
    schema: Schema | None = None


_SCHEMA_BINDING = "_angee_rebac_schema_binding"


def declared_schema_path(app_config: AppConfig) -> Path | None:
    """Retain the native authored declaration before binding generated output."""

    binding = app_config.__dict__.get(_SCHEMA_BINDING)
    declaration = getattr(app_config, "rebac_schema", None)
    if not isinstance(binding, _RebacSchemaBinding) or declaration != binding.effective:
        binding = _RebacSchemaBinding(resolve_schema_path(app_config), declaration, declaration)
        app_config.__dict__[_SCHEMA_BINDING] = binding
    return binding.source


def bind_rebac_schema(app_config: AppConfig, path: Path | None) -> None:
    """Bind generated input; ``None`` restores the original native declaration."""

    source = declared_schema_path(app_config)
    binding = app_config.__dict__[_SCHEMA_BINDING]
    assert isinstance(binding, _RebacSchemaBinding)
    effective = path if path is not None else binding.declaration
    setattr(app_config, "rebac_schema", effective)
    app_config.__dict__[_SCHEMA_BINDING] = _RebacSchemaBinding(source, binding.declaration, effective)


def merged_schema_relpath(package: str) -> Path:
    """Return the permission artifact address relative to compiler output."""

    return Path('permissions') / f'{package}.zed'


def effective_rebac_definition(model: type[models.Model]) -> Definition | None:
    """Read request policy through the native synced, scope-pinned backend."""

    resource_type = model_resource_type(model)
    return backend().schema().get_definition(resource_type) if resource_type else None


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
    binding = app_config.__dict__[_SCHEMA_BINDING]
    assert isinstance(binding, _RebacSchemaBinding)
    schema_path = resolve_schema_path(app_config)
    if schema_path is None:
        if binding.signature is not None:
            app_config.__dict__[_SCHEMA_BINDING] = replace(binding, signature=None, schema=None)
        return None
    status = schema_path.stat()
    signature = (
        schema_path, status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns, status.st_ctime_ns,
    )
    if signature != binding.signature:
        binding = replace(
            binding, signature=signature, schema=parse_zed(schema_path.read_text(encoding="utf-8")),
        )
        app_config.__dict__[_SCHEMA_BINDING] = binding
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

    The effective policy (the synced schema with its overrides) must state the
    contextual child action as exactly one arrow, ``relation->parent_action``, while
    ``read`` stays a union with an arrow arm through the same relation: the child's
    own actor-scoped read checks its live parent, so the listing selects the parents
    with joins instead of a second, separately scoped parent subquery. Any other arm
    on the action (a direct grant beside the arrow) could admit a child of an
    unreadable parent and leaves it unproved, as does an exclusion or intersection
    over ``read`` (a tightening override); extending ``read`` does not. A
    ``parent_action`` narrower than the parent's permission the arm names must hold
    on the parent as a purpose read (:func:`purpose_read_holds`). Unproved, the
    caller keeps ``read`` inside the scoped parent population. Field gates are
    unaffected; they keep their own ``read__<field>`` checks under either action.
    """

    resource_type = model_resource_type(model)
    return resource_type is not None and _contextual_holds(backend().schema(), resource_type, action)


def purpose_read_holds(model: type[models.Model], action: str) -> bool:
    """Whether a read of ``model``'s own population may check its purpose read ``action`` instead of ``read``.

    A purpose read, such as ``messaging/thread#read_inbox``, holds while the
    effective policy states it and ``read`` exactly as the model's composed
    declaration does, whose owner proves the purpose read answers as ``read`` within
    the population its read selects; an override on either, or a store not yet
    synced to the declaration, leaves it unproved. An arrow to a parent's narrower
    action, where ``read`` takes the parent's ``read``, holds only while that
    parent's purpose read holds in turn. Unproved, the caller keeps ``read``.
    """

    resource_type = model_resource_type(model)
    return resource_type is not None and _purpose_holds(backend().schema(), resource_type, action)


def _narrow_and_read(schema: Schema, resource_type: str, action: str) -> tuple[Definition, PermExpr, PermExpr] | None:
    """The effective definition with its ``action`` and ``read`` expressions, when it states both."""

    definition = schema.get_definition(resource_type)
    if definition is None:
        return None
    expressions = {permission.name: permission.expression for permission in definition.permissions}
    narrow, read = expressions.get(action), expressions.get("read")
    return (definition, narrow, read) if narrow is not None and read is not None else None


def _contextual_holds(schema: Schema, resource_type: str, action: str) -> bool:
    found = _narrow_and_read(schema, resource_type, action)
    if found is None:
        return False
    definition, narrow, read = found
    if not isinstance(narrow, PermArrow):
        return False
    arms = {arm.via: arm.target for arm in _union_arms(read) if isinstance(arm, PermArrow)}
    return _parent_holds(schema, definition, narrow, arms.get(narrow.via))


def _purpose_holds(schema: Schema, resource_type: str, action: str) -> bool:
    found = _narrow_and_read(schema, resource_type, action)
    if found is None:
        return False
    definition, narrow, read = found
    model = model_for_resource_type(resource_type)
    declared = declared_rebac_definition(model) if model is not None else None
    stated = {permission.name: permission.expression for permission in declared.permissions} if declared else {}
    if stated.get(action) != narrow or stated.get("read") != read:
        return False
    reads = {(arrow.via, arrow.target) for arrow in _arrows(read)}
    return all(
        _parent_holds(schema, definition, arrow, "read" if (arrow.via, "read") in reads else None)
        for arrow in _arrows(narrow)
        if (arrow.via, arrow.target) not in reads
    )


def _parent_holds(schema: Schema, definition: Definition, arrow: PermArrow, target: str | None) -> bool:
    """Whether ``arrow``'s parent action stands in for ``target``, the parent permission ``read`` takes."""

    return target is not None and (
        arrow.target == target
        or target == "read" and _purpose_holds(schema, _parent_type(definition, arrow.via), arrow.target)
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
    user = SubjectRef.of(_user_subject_type(), str(user_id))
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
    relationships = proposed_forward_relationships(
        instance,
        definition,
        required_relations=required,
        using=instance._state.db,
    )
    return CandidateRelationships(resource_type, definition, required, relationships)
