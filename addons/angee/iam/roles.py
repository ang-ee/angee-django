"""IAM permission-hub role and grant computations."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, cast

from django.contrib.auth import get_user_model
from django.db.models import QuerySet, Subquery
from django.http import HttpRequest
from pydantic import BaseModel
from rebac import (
    ObjectRef,
    SubjectRef,
    app_settings,
    resolve_subjects,
    system_context,
    to_object_ref,
    to_subject_ref,
)
from rebac import backend as rebac_backend
from rebac.memberships import grant as grant_membership
from rebac.memberships import revoke as revoke_membership
from rebac.models import active_relationship_model
from rebac.resources import model_for_resource_type
from rebac.roles import ROLE_RELATION
from rebac.schema import (
    Definition,
    Schema,
    named_object_refs,
    permission_object_sources,
    permission_sources,
    relation_is_writable,
)

from angee.base.identity import canonical_subject_ref, public_id_for, public_subject_ref

IAM_OVERVIEW_DEFAULT_PEEK_LIMIT = 6
IAM_OVERVIEW_MAX_PEEK_LIMIT = 100
PERMISSION_HUB_LIST_CAP = 1000
PRIVILEGED_PERMISSION_NAMES = frozenset({"admin", "create", "write", "delete"})
ROLE_SUFFIX = "/role"


class IAMRoleRow(BaseModel):
    """Canonical IAM role row derived from schema names and retained tuples."""

    id: str
    role_id: str
    namespace: str
    label: str
    declared: bool
    grantable: bool

    @classmethod
    def from_refs(
        cls,
        refs: Iterable[ObjectRef],
        *,
        schema: Schema,
        declared_refs: frozenset[ObjectRef],
    ) -> list[IAMRoleRow]:
        """Return distinct roles from native object references."""

        roles: dict[tuple[str, str], IAMRoleRow] = {}
        for ref in refs:
            key = (str(ref.resource_type), str(ref.resource_id))
            if key in roles:
                continue
            roles[key] = cls(
                id=role_ref(*key),
                role_id=key[1],
                namespace=role_namespace(key[0]),
                label=role_label(key[1]),
                declared=ref in declared_refs,
                grantable=ref in declared_refs
                and relation_is_writable(schema, resource=ref, relation=ROLE_RELATION),
            )
        return sorted(roles.values(), key=lambda role: (role.namespace, role.role_id))


class IAMGrantRow(BaseModel):
    """Canonical computed role grant retaining exact native subject identity."""

    id: str
    subject: str
    subject_id: str
    subject_type: str
    subject_relation: str
    subject_label: str
    role: str
    role_name: str
    namespace: str
    caveat_name: str

    @classmethod
    def from_relationships(
        cls,
        rows: QuerySet[Any],
        *,
        request: HttpRequest | None = None,
    ) -> list[IAMGrantRow]:
        """Project direct user and group-set role grants with batched labels."""

        materialized = list(rows)
        del request
        subjects = [
            SubjectRef.of(
                str(row.subject_type),
                str(row.subject_id),
                str(row.optional_subject_relation),
            )
            for row in materialized
        ]
        labels = {subject: str(instance) for subject, instance in resolve_subjects(subjects).items()}
        grants: list[IAMGrantRow] = []
        for row, subject in zip(materialized, subjects, strict=True):
            resource_type = str(row.resource_type)
            resource_id = str(row.resource_id)
            subject_type = str(row.subject_type)
            subject_id = str(row.subject_id)
            role = role_ref(resource_type, resource_id)
            public_subject = public_subject_ref(subject)
            grants.append(
                cls(
                    id=grant_public_id(
                        resource_type=resource_type,
                        resource_id=resource_id,
                        subject_type=subject_type,
                        subject_id=subject_id,
                        optional_subject_relation=str(row.optional_subject_relation),
                        caveat_name=str(row.caveat_name),
                    ),
                    subject=str(public_subject),
                    subject_id=public_subject.subject_id,
                    subject_type=subject_type,
                    subject_relation=subject.optional_relation,
                    subject_label=labels.get(subject) or str(public_subject),
                    role=role,
                    role_name=resource_id,
                    namespace=role_namespace(resource_type),
                    caveat_name=str(row.caveat_name),
                )
            )
        return grants


class IAMGroupMemberRow(BaseModel):
    """One direct membership tuple on an IAM group."""

    id: str
    subject: str
    subject_type: str
    subject_id: str
    label: str
    caveat_name: str


class IAMGroupBindingRow(BaseModel):
    """One tuple whose subject is an IAM group's member set."""

    id: str
    resource: str
    resource_type: str
    resource_id: str
    relation: str
    caveat_name: str
    target_model: str | None
    target_id: str | None


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
    privileged_grants: list[IAMGrantRow]
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
            role_rows = permission_hub_roles(limit=None)
            grant_rows = permission_hub_grant_rows(limit=None)
            privileged_rows = _privileged_grant_rows(grant_rows)
            users = get_user_model()._default_manager.all()
            unassigned_queryset = users.without_direct_roles(
                grant_rows,
                schema_role_resource_types(),
            ).ordered_users()
            return cls(
                user_count=users.count(),
                role_count=len(role_rows),
                grant_count=grant_rows.count(),
                relationship_count=relationship_rows(limit=None).count(),
                privileged_grant_count=privileged_rows.count(),
                unassigned_user_count=unassigned_queryset.count(),
                namespaces=overview_namespaces(role_rows, grant_rows),
                privileged_grants=IAMGrantRow.from_relationships(privileged_rows[:peek_limit], request=request),
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


def grant_public_id(
    *,
    resource_type: str,
    resource_id: str,
    subject_type: str,
    subject_id: str,
    relation: str = ROLE_RELATION,
    optional_subject_relation: str = "",
    caveat_name: str = "",
) -> str:
    """Return a stable public ID for one native role-membership tuple.

    The historical uncaveated direct-user spelling remains stable. Tuple shapes
    requiring extra identity use a versioned, unambiguous encoding of the native
    relationship key.
    """

    subject_ref = f"{subject_type}:{subject_id}"
    role = role_ref(resource_type, resource_id)
    if relation == ROLE_RELATION and not optional_subject_relation and not caveat_name:
        return f"{subject_ref}:{role}"
    key = (resource_type, resource_id, relation, subject_type, subject_id, optional_subject_relation, caveat_name)
    encoded = base64.urlsafe_b64encode(json.dumps(key, separators=(",", ":")).encode()).decode().rstrip("=")
    return f"grant_v1_{encoded}"


def validate_role(value: str, *, grantable: bool = False) -> ObjectRef:
    """Return ``value`` as a role ref, optionally requiring a writable member relation."""

    role = ObjectRef.parse(value)
    if not is_role_type(role.resource_type):
        raise ValueError("Role must use '<namespace>/role:<id>' format.")
    if grantable:
        schema = rebac_backend().schema()
        if role not in declared_role_refs(schema) or not relation_is_writable(
            schema,
            resource=role,
            relation=ROLE_RELATION,
        ):
            raise ValueError(f"Role {value!r} does not accept direct membership grants.")
    return role


def validate_subject(
    value: str,
    *,
    require_existing: bool = True,
) -> SubjectRef:
    """Return a concrete supported IAM subject for a new membership grant."""

    subject = canonical_subject_ref(value)
    expected_relation = "" if subject.subject_type == "auth/user" else "member"
    supported = subject.subject_type in {"auth/user", "auth/group"}
    if not supported or subject.optional_relation != expected_relation:
        raise ValueError("Subject must be canonical 'auth/user:<id>' or 'auth/group:<id>#member'.")
    if require_existing and subject not in resolve_subjects((subject,)):
        raise ValueError(f"Subject {value!r} was not found.")
    return subject


def grant_role(
    *,
    subject: str,
    role: str,
    caveat_name: str = "",
    caveat_context: dict[str, Any] | None = None,
) -> None:
    """Grant one declared role to one existing supported IAM subject."""

    grant_membership(
        subject=validate_subject(subject),
        container=validate_role(role, grantable=True),
        caveat_name=caveat_name,
        caveat_context=caveat_context,
    )


def revoke_role(*, subject: str, role: str, caveat_name: str = "") -> bool:
    """Revoke an exact role tuple while allowing stale subject and role ids."""

    return bool(
        revoke_membership(
            subject=validate_subject(subject, require_existing=False),
            container=validate_role(role),
            caveat_name=caveat_name,
        )
    )


def relationship_rows(limit: int | None = PERMISSION_HUB_LIST_CAP) -> QuerySet[Any]:
    """Return active relationship rows in stable order."""

    relationship_model = active_relationship_model()
    rows = relationship_model.objects.order_by_resource()
    if limit is not None:
        rows = relationship_model.objects.filter(pk__in=Subquery(rows.values("pk")[:limit])).order_by_resource()
    return cast(QuerySet[Any], rows)


def permission_hub_roles(limit: int | None = PERMISSION_HUB_LIST_CAP) -> list[IAMRoleRow]:
    """Return declared roles plus tuple-only legacy roles."""

    schema = rebac_backend().schema()
    declared = declared_role_refs(schema)
    refs = set(declared)
    refs.update(
        ObjectRef(str(row.resource_type), str(row.resource_id))
        for row in permission_hub_role_rows(limit=None)
    )
    rows = IAMRoleRow.from_refs(refs, schema=schema, declared_refs=declared)
    return rows if limit is None else rows[:limit]


def declared_role_refs(schema: Schema | None = None) -> frozenset[ObjectRef]:
    """Return role objects named by the installed permission schema."""

    schema = schema or rebac_backend().schema()
    refs: set[ObjectRef] = set()
    for resource_type in schema_role_resource_types():
        refs.update(named_object_refs(schema, object_type=resource_type))
    return frozenset(refs)


def permission_hub_role_rows(limit: int | None = PERMISSION_HUB_LIST_CAP) -> QuerySet[Any]:
    """Return relationship rows whose resource type is a declared role type."""

    rows = active_relationship_model().objects.filter(resource_type__in=schema_role_resource_types())
    rows = rows.order_by_resource()
    if limit is not None:
        rows = rows[:limit]
    return cast(QuerySet[Any], rows)


def permission_hub_grants(
    *,
    request: HttpRequest | None = None,
    limit: int | None = PERMISSION_HUB_LIST_CAP,
) -> list[IAMGrantRow]:
    """Return direct user and group-set role grants with labels batched."""

    return IAMGrantRow.from_relationships(permission_hub_grant_rows(limit=limit), request=request)


def permission_hub_grant_rows(limit: int | None = PERMISSION_HUB_LIST_CAP) -> QuerySet[Any]:
    """Return supported direct role-grant rows in stable order."""

    manager = active_relationship_model().objects
    users = manager.filter(
        resource_type__in=schema_role_resource_types(),
        relation=ROLE_RELATION,
        subject_type="auth/user",
        optional_subject_relation="",
    )
    groups = manager.filter(
        resource_type__in=schema_role_resource_types(),
        relation=ROLE_RELATION,
        subject_type="auth/group",
        optional_subject_relation="member",
    )
    rows = users | groups
    rows = rows.order_by_resource()
    if limit is not None:
        rows = rows[:limit]
    return cast(QuerySet[Any], rows)


def group_member_rows(group: Any) -> QuerySet[Any]:
    """Return direct membership tuples for ``group``."""

    group_ref = to_object_ref(group)
    return cast(
        QuerySet[Any],
        active_relationship_model().objects.filter(
            resource_type=group_ref.resource_type,
            resource_id=group_ref.resource_id,
            relation=ROLE_RELATION,
        ).order_by_subject(),
    )


def group_members(group: Any) -> list[IAMGroupMemberRow]:
    """Project a group's direct members without computing effective permissions."""

    rows = list(group_member_rows(group))
    subjects = [
        SubjectRef.of(str(row.subject_type), str(row.subject_id), str(row.optional_subject_relation))
        for row in rows
    ]
    labels = {subject: str(instance) for subject, instance in resolve_subjects(subjects).items()}
    members: list[IAMGroupMemberRow] = []
    for row, subject in zip(rows, subjects, strict=True):
        public_subject = public_subject_ref(subject)
        members.append(
            IAMGroupMemberRow(
                id=grant_public_id(
                    resource_type=str(row.resource_type),
                    resource_id=str(row.resource_id),
                    subject_type=subject.subject_type,
                    subject_id=subject.subject_id,
                    optional_subject_relation=subject.optional_relation,
                    caveat_name=str(row.caveat_name),
                ),
                subject=str(public_subject),
                subject_type=subject.subject_type,
                subject_id=public_subject.subject_id,
                label=labels.get(subject) or str(public_subject),
                caveat_name=str(row.caveat_name),
            )
        )
    return members


def group_bindings(group: Any) -> list[IAMGroupBindingRow]:
    """Project tuples bound directly to a group's member subject set."""

    subject = to_subject_ref(group)
    rows = list(
        active_relationship_model().objects.filter(
            subject_type=subject.subject_type,
            subject_id=subject.subject_id,
            optional_subject_relation=subject.optional_relation,
        ).order_by_resource()
    )
    targets = _binding_targets(rows)
    result: list[IAMGroupBindingRow] = []
    for row in rows:
        resource_type = str(row.resource_type)
        resource_id = str(row.resource_id)
        target_model, target_id = targets[(resource_type, resource_id)]
        public_resource = public_subject_ref(SubjectRef.of(resource_type, resource_id))
        result.append(
            IAMGroupBindingRow(
                id=grant_public_id(
                    resource_type=resource_type,
                    resource_id=resource_id,
                    subject_type=subject.subject_type,
                    subject_id=subject.subject_id,
                    optional_subject_relation=subject.optional_relation,
                    caveat_name=str(row.caveat_name),
                    relation=str(row.relation),
                ),
                resource=str(public_resource),
                resource_type=resource_type,
                resource_id=public_resource.subject_id,
                relation=str(row.relation),
                caveat_name=str(row.caveat_name),
                target_model=target_model,
                target_id=target_id,
            )
        )
    return result


def _binding_targets(rows: Iterable[Any]) -> dict[tuple[str, str], tuple[str | None, str | None]]:
    """Resolve model-backed binding targets in one native batch per resource type."""

    targets: dict[tuple[str, str], tuple[str | None, str | None]] = {}
    candidates: dict[tuple[str, str], tuple[type[Any], SubjectRef]] = {}
    for row in rows:
        resource_type = str(row.resource_type)
        resource_id = str(row.resource_id)
        key = (resource_type, resource_id)
        if is_role_type(resource_type):
            targets[key] = ("iam.Role", role_ref(resource_type, resource_id))
            continue
        model = model_for_resource_type(resource_type)
        if model is None or not model._meta.managed:
            targets[key] = (None, None)
            continue
        candidates[key] = (model, SubjectRef.of(resource_type, resource_id))

    resolved = resolve_subjects(ref for _, ref in candidates.values())
    for key, (model, ref) in candidates.items():
        instance = resolved.get(ref)
        targets[key] = (
            model._meta.label,
            public_id_for(model, instance.pk) if instance is not None else None,
        )
    return targets


def schema_role_resource_types() -> set[str]:
    """Return role resource types declared by the installed REBAC schema."""

    return {
        definition.resource_type
        for definition in rebac_backend().schema().definitions
        if is_role_type(definition.resource_type)
    }


def permission_conditions(schema: Schema, resource_type: str, permission_name: str) -> list[str]:
    """Return source condition labels for a REBAC permission."""

    sources = permission_sources(schema, resource_type, permission_name)
    names = {
        *sources.direct_relations,
        *(f"{via}->{target}" for via, target in sources.arrows),
        *sources.builtins,
        *sources.subpermissions,
    }
    return sorted(names) or ["nil"]


def permission_schema() -> tuple[Schema, list[Definition]]:
    """Return the native installed schema and its deterministically ordered definitions."""

    schema = rebac_backend().schema()
    definitions = sorted(schema.definitions, key=lambda item: item.resource_type)
    return schema, definitions[:PERMISSION_HUB_LIST_CAP]


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
    roles: list[IAMRoleRow],
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
                    for role in permission_object_sources(
                        schema,
                        definition.resource_type,
                        permission_name,
                        object_type=role_resource_type,
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
