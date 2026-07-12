"""IAM permission-hub role and grant computations."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, cast

from django.contrib.auth import get_user_model
from django.db.models import Exists, OuterRef, Q, QuerySet, Subquery
from django.http import HttpRequest
from rebac import ObjectRef, app_settings, system_context
from rebac import backend as rebac_backend
from rebac.models import active_relationship_model
from rebac.roles import ROLE_RELATION
from rebac.schema import (
    Definition,
    PermArrow,
    PermBinOp,
    PermExpr,
    PermNil,
    PermRef,
    Relation,
    Schema,
)

from angee.iam.identity import user_display_labels

IAM_OVERVIEW_DEFAULT_PEEK_LIMIT = 6
IAM_OVERVIEW_MAX_PEEK_LIMIT = 100
PERMISSION_HUB_LIST_CAP = 1000
PRIVILEGED_PERMISSION_NAMES = frozenset({"admin", "create", "write", "delete"})
ROLE_SUFFIX = "/role"
REBAC_BUILTIN_PERMISSION_REFS = frozenset({"anonymous", "authenticated"})


@dataclass(frozen=True, slots=True)
class PermissionSources:
    """Flattened REBAC permission-expression leaves for the IAM console."""

    direct_relations: frozenset[str] = frozenset()
    arrows: frozenset[tuple[str, str]] = frozenset()
    builtins: frozenset[str] = frozenset()
    subpermissions: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class RoleInfo:
    """Tuple-derived role projected by the IAM permission hub."""

    id: str
    namespace: str
    label: str

    @classmethod
    def from_relationships(cls, rows: QuerySet[Any]) -> list[RoleInfo]:
        """Return distinct role types from relationship rows."""

        roles: dict[tuple[str, str], RoleInfo] = {}
        for row in rows:
            key = (str(row.resource_type), str(row.resource_id))
            if key in roles:
                continue
            roles[key] = cls(
                id=str(row.resource_id),
                namespace=role_namespace(str(row.resource_type)),
                label=role_label(str(row.resource_id)),
            )
        return sorted(roles.values(), key=lambda role: (role.namespace, role.id))


@dataclass(frozen=True, slots=True)
class GrantInfo:
    """Direct user role grant projected by the IAM permission hub."""

    principal_id: str
    principal_type: str
    principal_ref: str
    principal_label: str
    role: str
    role_name: str
    namespace: str

    @classmethod
    def from_relationships(
        cls,
        rows: QuerySet[Any],
        *,
        request: HttpRequest | None = None,
    ) -> list[GrantInfo]:
        """Project direct user role-grant tuples with batched principal labels."""

        materialized = list(rows)
        label_ids = [str(row.subject_id) for row in materialized]
        labels = user_display_labels(label_ids, request=request)
        grants: list[GrantInfo] = []
        for row in materialized:
            resource_type = str(row.resource_type)
            resource_id = str(row.resource_id)
            subject_type = str(row.subject_type)
            subject_id = str(row.subject_id)
            principal_ref = f"{subject_type}:{subject_id}"
            role = role_ref(resource_type, resource_id)
            grants.append(
                cls(
                    principal_id=subject_id,
                    principal_type=subject_type,
                    principal_ref=principal_ref,
                    principal_label=labels.get(subject_id) or principal_ref,
                    role=role,
                    role_name=resource_id,
                    namespace=role_namespace(resource_type),
                )
            )
        return grants


@dataclass(frozen=True, slots=True)
class RelationInfo:
    """Installed REBAC relation declaration."""

    name: str
    allowed_subject_types: list[str]


@dataclass(frozen=True, slots=True)
class PermissionConditionInfo:
    """Flattened permission expression leaf."""

    name: str


@dataclass(frozen=True, slots=True)
class PermissionInfo:
    """Installed REBAC permission declaration."""

    name: str
    conditions: list[PermissionConditionInfo]


@dataclass(frozen=True, slots=True)
class ResourceSchemaInfo:
    """Installed REBAC resource definition projected for IAM."""

    resource_type: str
    relations: list[RelationInfo]
    permissions: list[PermissionInfo]


@dataclass(frozen=True, slots=True)
class OverviewNamespaceInfo:
    """Namespace aggregate shown by the IAM overview."""

    namespace: str
    role_count: int
    grant_count: int


@dataclass(frozen=True, slots=True)
class OverviewInfo:
    """IAM dashboard facts computed by the IAM role owner."""

    user_count: int
    role_count: int
    grant_count: int
    relationship_count: int
    privileged_grant_count: int
    unassigned_user_count: int
    namespaces: list[OverviewNamespaceInfo]
    privileged_grants: list[GrantInfo]
    unassigned_users: list[Any]

    @classmethod
    def build(
        cls,
        peek_limit: int,
        *,
        request: HttpRequest | None = None,
    ) -> OverviewInfo:
        """Return IAM dashboard facts independent of paginated list rows."""

        peek_limit = clamped_peek_limit(peek_limit)
        with system_context(reason="iam.roles.overview"):
            role_infos = RoleInfo.from_relationships(permission_hub_role_rows(limit=None))
            grant_rows = permission_hub_grant_rows(limit=None)
            privileged_rows = _privileged_grant_rows(grant_rows)
            unassigned_queryset = unassigned_user_queryset()
            return cls(
                user_count=_people_queryset(get_user_model()).count(),
                role_count=len(role_infos),
                grant_count=grant_rows.count(),
                relationship_count=relationship_rows(limit=None).count(),
                privileged_grant_count=privileged_rows.count(),
                unassigned_user_count=unassigned_queryset.count(),
                namespaces=overview_namespaces(role_infos, grant_rows),
                privileged_grants=GrantInfo.from_relationships(privileged_rows[:peek_limit], request=request),
                unassigned_users=list(unassigned_queryset[:peek_limit]),
            )


def role_namespace(resource_type: str) -> str:
    """Return the namespace portion of a role resource type."""

    return resource_type.removesuffix(ROLE_SUFFIX)


def is_role_type(resource_type: str) -> bool:
    """Return whether ``resource_type`` names a role resource."""

    return resource_type.endswith(ROLE_SUFFIX)


def role_label(role_id: str) -> str:
    """Return a display label for a role id."""

    return role_id.replace("_", " ").replace("-", " ").title()


def role_ref(resource_type: str, resource_id: str) -> str:
    """Return the canonical role object ref string."""

    return f"{resource_type}:{resource_id}"


def validate_role(value: str) -> ObjectRef:
    """Return ``value`` as a role object ref or raise."""

    role = ObjectRef.parse(value)
    if not is_role_type(role.resource_type):
        raise ValueError("Role must use '<namespace>/role:<id>' format.")
    return role


def relationship_rows(limit: int | None = PERMISSION_HUB_LIST_CAP) -> QuerySet[Any]:
    """Return active relationship rows in stable order."""

    relationship_model = active_relationship_model()
    rows = _order_relationship_rows(relationship_model.objects.all())
    if limit is not None:
        rows = _order_relationship_rows(relationship_model.objects.filter(pk__in=Subquery(rows.values("pk")[:limit])))
    return cast(QuerySet[Any], rows)


def permission_hub_roles(limit: int | None = PERMISSION_HUB_LIST_CAP) -> list[RoleInfo]:
    """Return roles visible from active role relationship rows."""

    return RoleInfo.from_relationships(permission_hub_role_rows(limit=limit))


def permission_hub_role_rows(limit: int | None = PERMISSION_HUB_LIST_CAP) -> QuerySet[Any]:
    """Return relationship rows that mention schema-declared role objects."""

    rows = (
        active_relationship_model()
        .objects.filter(resource_type__in=schema_role_resource_types())
    )
    rows = _order_relationship_rows(rows)
    if limit is not None:
        rows = rows[:limit]
    return cast(QuerySet[Any], rows)


def permission_hub_grants(
    *,
    request: HttpRequest | None = None,
    limit: int | None = PERMISSION_HUB_LIST_CAP,
) -> list[GrantInfo]:
    """Return direct user role grants with principal labels batched."""

    return GrantInfo.from_relationships(permission_hub_grant_rows(limit=limit), request=request)


def permission_hub_grant_rows(limit: int | None = PERMISSION_HUB_LIST_CAP) -> QuerySet[Any]:
    """Return direct user role-grant rows in stable order."""

    rows = (
        active_relationship_model()
        .objects.filter(
            resource_type__in=schema_role_resource_types(),
            relation=ROLE_RELATION,
            subject_type=app_settings.REBAC_USER_TYPE,
            optional_subject_relation="",
        )
    )
    rows = _order_relationship_rows(rows)
    if limit is not None:
        rows = rows[:limit]
    return cast(QuerySet[Any], rows)


def _order_relationship_rows(rows: QuerySet[Any]) -> QuerySet[Any]:
    """Return relationship rows in canonical wire-order for IAM lists."""

    if any(field.name == "resource_fk" for field in rows.model._meta.fields):
        return cast(
            QuerySet[Any],
            rows.order_by(
                "resource_fk__resource_type",
                "resource_fk__resource_id",
                "relation",
                "subject_fk__resource_type",
                "subject_fk__resource_id",
                "optional_subject_relation",
                "caveat_name",
                "pk",
            ),
        )
    return cast(
        QuerySet[Any],
        rows.order_by(
            "resource_type",
            "resource_id",
            "relation",
            "subject_type",
            "subject_id",
            "optional_subject_relation",
            "caveat_name",
            "pk",
        ),
    )


def schema_role_resource_types() -> set[str]:
    """Return role resource types declared by the installed REBAC schema."""

    return {
        definition.resource_type
        for definition in rebac_backend().schema().definitions
        if is_role_type(definition.resource_type)
    }


def schema_allowed_subject_name(allowed: Any) -> str:
    """Return one relation allowed-subject declaration as a compact string."""

    value = str(allowed.type)
    if getattr(allowed, "wildcard", False):
        value = f"{value}:*"
    elif getattr(allowed, "id", ""):
        value = f"{value}:{allowed.id}"
    if getattr(allowed, "relation", ""):
        value = f"{value}#{allowed.relation}"
    if getattr(allowed, "with_caveat", ""):
        value = f"{value} with {allowed.with_caveat}"
    return value


def permission_sources(schema: Schema, resource_type: str, permission_name: str) -> PermissionSources:
    """Return flattened source labels for a REBAC permission expression."""

    definition = schema.get_definition(resource_type)
    permission = schema.get_permission(resource_type, permission_name)
    if definition is None or permission is None:
        return PermissionSources()
    sources = _MutablePermissionSources()
    _collect_permission_sources(
        schema,
        definition,
        permission.expression,
        sources,
        seen_permissions=frozenset({(resource_type, permission_name)}),
    )
    return sources.freeze()


def roles_reaching_permission(
    schema: Schema,
    resource_type: str,
    permission_name: str,
    *,
    role_resource_type: str,
) -> tuple[ObjectRef, ...]:
    """Return schema-named role objects that can feed one permission."""

    definition = schema.get_definition(resource_type)
    permission = schema.get_permission(resource_type, permission_name)
    if definition is None or permission is None:
        return ()
    refs: set[ObjectRef] = set()
    _collect_role_refs_from_expr(
        schema,
        definition,
        permission.expression,
        role_resource_type=role_resource_type,
        refs=refs,
        seen_permissions=frozenset({(resource_type, permission_name)}),
    )
    return tuple(sorted(refs, key=lambda ref: (ref.resource_type, ref.resource_id)))


@dataclass(slots=True)
class _MutablePermissionSources:
    direct_relations: set[str]
    arrows: set[tuple[str, str]]
    builtins: set[str]
    subpermissions: set[str]

    def __init__(self) -> None:
        self.direct_relations = set()
        self.arrows = set()
        self.builtins = set()
        self.subpermissions = set()

    def freeze(self) -> PermissionSources:
        """Return an immutable projection."""

        return PermissionSources(
            direct_relations=frozenset(self.direct_relations),
            arrows=frozenset(self.arrows),
            builtins=frozenset(self.builtins),
            subpermissions=frozenset(self.subpermissions),
        )


def _collect_permission_sources(
    schema: Schema,
    definition: Definition,
    expression: PermExpr,
    sources: _MutablePermissionSources,
    *,
    seen_permissions: frozenset[tuple[str, str]],
) -> None:
    """Accumulate leaf names from one permission expression."""

    if isinstance(expression, PermNil):
        return
    if isinstance(expression, PermArrow):
        sources.arrows.add((expression.via, expression.target))
        return
    if isinstance(expression, PermBinOp):
        _collect_permission_sources(
            schema,
            definition,
            expression.left,
            sources,
            seen_permissions=seen_permissions,
        )
        _collect_permission_sources(
            schema,
            definition,
            expression.right,
            sources,
            seen_permissions=seen_permissions,
        )
        return
    if isinstance(expression, PermRef):
        if expression.name in REBAC_BUILTIN_PERMISSION_REFS:
            sources.builtins.add(expression.name)
            return
        if _relation_by_name(definition, expression.name) is not None:
            sources.direct_relations.add(expression.name)
            return
        subpermission = schema.get_permission(definition.resource_type, expression.name)
        if subpermission is None:
            return
        key = (definition.resource_type, expression.name)
        if key in seen_permissions:
            return
        sources.subpermissions.add(expression.name)
        _collect_permission_sources(
            schema,
            definition,
            subpermission.expression,
            sources,
            seen_permissions=seen_permissions | {key},
        )


def _collect_role_refs_from_expr(
    schema: Schema,
    definition: Definition,
    expression: PermExpr,
    *,
    role_resource_type: str,
    refs: set[ObjectRef],
    seen_permissions: frozenset[tuple[str, str]],
) -> None:
    """Accumulate statically named role refs from one permission expression."""

    if isinstance(expression, PermNil):
        return
    if isinstance(expression, PermArrow):
        relation = _relation_by_name(definition, expression.via)
        if relation is None:
            return
        refs.update(_role_refs_for_relation(relation, role_resource_type))
        for allowed in relation.allowed_subjects:
            target_definition = schema.get_definition(str(allowed.type))
            target_permission = schema.get_permission(str(allowed.type), expression.target)
            if target_definition is None or target_permission is None:
                continue
            key = (target_definition.resource_type, expression.target)
            if key in seen_permissions:
                continue
            _collect_role_refs_from_expr(
                schema,
                target_definition,
                target_permission.expression,
                role_resource_type=role_resource_type,
                refs=refs,
                seen_permissions=seen_permissions | {key},
            )
        return
    if isinstance(expression, PermBinOp):
        _collect_role_refs_from_expr(
            schema,
            definition,
            expression.left,
            role_resource_type=role_resource_type,
            refs=refs,
            seen_permissions=seen_permissions,
        )
        if expression.op != "-":
            _collect_role_refs_from_expr(
                schema,
                definition,
                expression.right,
                role_resource_type=role_resource_type,
                refs=refs,
                seen_permissions=seen_permissions,
            )
        return
    if isinstance(expression, PermRef):
        relation = _relation_by_name(definition, expression.name)
        if relation is not None:
            refs.update(_role_refs_for_relation(relation, role_resource_type))
            return
        if expression.name in REBAC_BUILTIN_PERMISSION_REFS:
            return
        subpermission = schema.get_permission(definition.resource_type, expression.name)
        if subpermission is None:
            return
        key = (definition.resource_type, expression.name)
        if key in seen_permissions:
            return
        _collect_role_refs_from_expr(
            schema,
            definition,
            subpermission.expression,
            role_resource_type=role_resource_type,
            refs=refs,
            seen_permissions=seen_permissions | {key},
        )


def _role_refs_for_relation(relation: Relation, role_resource_type: str) -> set[ObjectRef]:
    """Return statically named role objects declared by one relation."""

    refs: set[ObjectRef] = set()
    const_id = str(getattr(relation.backing, "target_id", "") or "")
    for allowed in relation.allowed_subjects:
        if str(allowed.type) != role_resource_type:
            continue
        role_id = str(getattr(allowed, "id", "") or const_id)
        if role_id:
            refs.add(ObjectRef(role_resource_type, role_id))
    return refs


def _relation_by_name(definition: Definition, name: str) -> Relation | None:
    """Return a relation declaration from one REBAC definition."""

    for relation in definition.relations:
        if relation.name == name:
            return relation
    return None


def permission_conditions(schema: Any, resource_type: str, permission_name: str) -> list[PermissionConditionInfo]:
    """Return source condition labels for a REBAC permission."""

    sources = permission_sources(schema, resource_type, permission_name)
    names = {
        *sources.direct_relations,
        *(f"{via}->{target}" for via, target in sources.arrows),
        *sources.builtins,
        *sources.subpermissions,
    }
    return [PermissionConditionInfo(name=name) for name in sorted(names)] or [PermissionConditionInfo(name="nil")]


def permission_schema() -> list[ResourceSchemaInfo]:
    """Return the installed REBAC schema projected for the IAM console."""

    schema = rebac_backend().schema()
    resources: list[ResourceSchemaInfo] = []
    definitions = sorted(schema.definitions, key=lambda item: item.resource_type)
    for definition in definitions[:PERMISSION_HUB_LIST_CAP]:
        relations = [
            RelationInfo(
                name=relation.name,
                allowed_subject_types=[schema_allowed_subject_name(allowed) for allowed in relation.allowed_subjects],
            )
            for relation in sorted(definition.relations, key=lambda item: item.name)
        ]
        permissions = [
            PermissionInfo(
                name=permission.name,
                conditions=permission_conditions(schema, definition.resource_type, permission.name),
            )
            for permission in sorted(definition.permissions, key=lambda item: item.name)
        ]
        resources.append(
            ResourceSchemaInfo(
                resource_type=definition.resource_type,
                relations=relations,
                permissions=permissions,
            )
        )
    return resources


def iam_overview(
    peek_limit: int,
    *,
    request: HttpRequest | None = None,
) -> OverviewInfo:
    """Return IAM dashboard facts independent of paginated list rows."""

    return OverviewInfo.build(peek_limit, request=request)


def clamped_peek_limit(value: int) -> int:
    """Return a bounded overview preview size."""

    return max(0, min(value, IAM_OVERVIEW_MAX_PEEK_LIMIT))


def overview_namespaces(
    roles: list[RoleInfo],
    grants: QuerySet[Any],
) -> list[OverviewNamespaceInfo]:
    """Return namespace-level role and direct-grant counts."""

    counts: dict[str, dict[str, int]] = {}
    for role in roles:
        entry = counts.setdefault(role.namespace, {"roles": 0, "grants": 0})
        entry["roles"] += 1

    for row in grants:
        namespace = role_namespace(str(row.resource_type))
        entry = counts.setdefault(namespace, {"roles": 0, "grants": 0})
        entry["grants"] += 1

    return [
        OverviewNamespaceInfo(
            namespace=namespace,
            role_count=count["roles"],
            grant_count=count["grants"],
        )
        for namespace, count in sorted(counts.items())
    ]


def unassigned_user_queryset() -> QuerySet[Any]:
    """Return users without direct role grants."""

    user_model = get_user_model()
    user_queryset = _people_queryset(user_model)
    subject_lookup = user_subject_lookup(user_model)
    if subject_lookup == "sqid":
        # Materialized: the sqid field decodes lookup values in Python, so the
        # id set cannot ride a SQL subquery.
        subject_ids = tuple(
            dict.fromkeys(
                str(subject_id)
                for subject_id in permission_hub_grant_rows(limit=None).values_list("subject_id", flat=True)
                if subject_id
            )
        )
        assigned_user_pks = user_model._default_manager.filter(sqid__in=subject_ids).values("pk")
        return cast(
            QuerySet[Any],
            user_queryset
            .exclude(pk__in=Subquery(assigned_user_pks))
            .order_by(*user_ordering(user_model)),
        )

    assigned_exists = active_relationship_model().objects.filter(
        resource_type__in=schema_role_resource_types(),
        relation=ROLE_RELATION,
        subject_type=app_settings.REBAC_USER_TYPE,
        subject_id=OuterRef(subject_lookup),
        optional_subject_relation="",
    )
    return cast(
        QuerySet[Any],
        user_queryset
        .annotate(_iam_has_role=Exists(assigned_exists))
        .filter(_iam_has_role=False)
        .order_by(*user_ordering(user_model)),
    )


def _people_queryset(user_model: type[Any]) -> QuerySet[Any]:
    """Return the user rows intended for people-facing IAM list surfaces."""

    return cast(QuerySet[Any], user_model._default_manager.all().people())


def user_subject_filter(user_model: type[Any], subject_ids: Iterable[Any]) -> Q:
    """Return a user queryset filter matching REBAC subject ids."""

    ids = tuple(dict.fromkeys(str(subject_id) for subject_id in subject_ids if subject_id))
    if not ids:
        return Q(pk__in=())
    subject_lookup = user_subject_lookup(user_model)
    if subject_lookup == "sqid":
        public_lookup = getattr(user_model, "public_id_lookup", None)
        query = Q(pk__in=())
        if not callable(public_lookup):
            return query
        for subject_id in ids:
            try:
                query |= Q(**public_lookup(subject_id))
            except (TypeError, ValueError):
                continue
        return query
    return Q(**{f"{subject_lookup}__in": ids})


def user_subject_lookup(user_model: type[Any] | None = None) -> str:
    """Return the User field lookup used by REBAC actor subject ids."""

    model = user_model or get_user_model()
    subject_id_attr = str(getattr(model._meta, "rebac_id_attr", None) or app_settings.REBAC_USER_ID_ATTR)
    if subject_id_attr == "pk":
        pk = model._meta.pk
        return pk.name if pk is not None else "pk"
    return subject_id_attr


def user_ordering(user_model: type[Any] | None = None) -> tuple[str, ...]:
    """Return deterministic ordering for IAM overview user previews."""

    model = user_model or get_user_model()
    concrete_fields = {field.name for field in model._meta.fields}
    fields: list[str] = []
    username_field = str(getattr(model, "USERNAME_FIELD", ""))
    if username_field in concrete_fields:
        fields.append(username_field)
    pk = model._meta.pk
    if pk is not None and pk.name not in fields:
        fields.append(pk.name)
    return tuple(fields or ("pk",))


def privileged_role_refs() -> set[str]:
    """Return role refs that the installed REBAC schema treats as privileged."""

    schema = rebac_backend().schema()
    refs: set[str] = set()
    universal_role = app_settings.REBAC_UNIVERSAL_ADMIN_ROLE
    if universal_role:
        refs.add(str(ObjectRef.parse(universal_role)))
    role_resource_types = schema_role_resource_types()
    for definition in schema.definitions:
        for permission_name in PRIVILEGED_PERMISSION_NAMES:
            for role_resource_type in role_resource_types:
                refs.update(
                    str(role)
                    for role in roles_reaching_permission(
                        schema,
                        definition.resource_type,
                        permission_name,
                        role_resource_type=role_resource_type,
                    )
                )
    return refs


def _privileged_grant_rows(grant_rows: QuerySet[Any]) -> QuerySet[Any]:
    """Return grant rows whose role is privileged by the installed schema.

    Matching goes through the queryset's own ``for_resource`` (both
    relationship storage modes translate it): a raw ``Q(resource_type=…,
    resource_id=…)`` would bypass the registry mode's kwarg translation and
    fail on the FK-backed model.
    """

    rows: QuerySet[Any] | None = None
    for role in sorted(privileged_role_refs()):
        role_object = ObjectRef.parse(role)
        matched = grant_rows.for_resource(
            role_object.resource_type,
            role_object.resource_id,
        )
        rows = matched if rows is None else rows | matched
    if rows is None:
        return cast(QuerySet[Any], grant_rows.none())
    return cast(QuerySet[Any], rows)
