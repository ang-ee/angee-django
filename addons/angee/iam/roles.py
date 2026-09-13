"""IAM permission-hub role and grant computations."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, cast

from django.contrib.auth import get_user_model
from django.db.models import QuerySet, Subquery
from django.http import HttpRequest
from pydantic import BaseModel
from rebac import ObjectRef, RelationshipTuple, app_settings, system_context
from rebac import backend as rebac_backend
from rebac.actors import to_subject_ref
from rebac.models import active_relationship_model
from rebac.relationships import delete_relationship
from rebac.roles import ROLE_RELATION
from rebac.schema import Definition, Schema, permission_object_sources, permission_sources

from angee.iam.identity import user_display_labels

IAM_OVERVIEW_DEFAULT_PEEK_LIMIT = 6
IAM_OVERVIEW_MAX_PEEK_LIMIT = 100
PERMISSION_HUB_LIST_CAP = 1000
PRIVILEGED_PERMISSION_NAMES = frozenset({"admin", "create", "write", "delete"})
ROLE_SUFFIX = "/role"


class IAMRoleRow(BaseModel):
    """Canonical computed IAM role row derived from relationship tuples."""

    id: str
    role_id: str
    namespace: str
    label: str

    @classmethod
    def from_relationships(cls, rows: QuerySet[Any]) -> list[IAMRoleRow]:
        """Return distinct role types from relationship rows."""

        roles: dict[tuple[str, str], IAMRoleRow] = {}
        for row in rows:
            key = (str(row.resource_type), str(row.resource_id))
            if key in roles:
                continue
            roles[key] = cls(
                id=role_ref(*key),
                role_id=str(row.resource_id),
                namespace=role_namespace(str(row.resource_type)),
                label=role_label(str(row.resource_id)),
            )
        return sorted(roles.values(), key=lambda role: (role.namespace, role.role_id))


class IAMGrantRow(BaseModel):
    """Canonical computed direct-user role grant row."""

    id: str
    principal_id: str
    principal_type: str
    principal_ref: str
    principal_label: str
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
        """Project direct user role-grant tuples with batched principal labels."""

        materialized = list(rows)
        label_ids = [str(row.subject_id) for row in materialized]
        labels = user_display_labels(label_ids, request=request)
        grants: list[IAMGrantRow] = []
        for row in materialized:
            resource_type = str(row.resource_type)
            resource_id = str(row.resource_id)
            subject_type = str(row.subject_type)
            subject_id = str(row.subject_id)
            principal_ref = f"{subject_type}:{subject_id}"
            role = role_ref(resource_type, resource_id)
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
                    principal_id=subject_id,
                    principal_type=subject_type,
                    principal_ref=principal_ref,
                    principal_label=labels.get(subject_id) or principal_ref,
                    role=role,
                    role_name=resource_id,
                    namespace=role_namespace(resource_type),
                    caveat_name=str(row.caveat_name),
                )
            )
        return grants


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
            role_rows = IAMRoleRow.from_relationships(permission_hub_role_rows(limit=None))
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
    optional_subject_relation: str = "",
    caveat_name: str = "",
) -> str:
    """Return a stable public ID for one native role-membership tuple.

    The historical uncaveated direct-user spelling remains stable. Tuple shapes
    requiring extra identity use a versioned, unambiguous encoding of the native
    relationship key.
    """

    principal_ref = f"{subject_type}:{subject_id}"
    role = role_ref(resource_type, resource_id)
    if not optional_subject_relation and not caveat_name:
        return f"{principal_ref}:{role}"
    key = (resource_type, resource_id, ROLE_RELATION, subject_type, subject_id, optional_subject_relation, caveat_name)
    encoded = base64.urlsafe_b64encode(json.dumps(key, separators=(",", ":")).encode()).decode().rstrip("=")
    return f"grant_v1_{encoded}"


def revoke_grant(*, principal: Any, role: ObjectRef, caveat_name: str = "") -> bool:
    """Delete exactly the selected native role-membership tuple."""

    subject = to_subject_ref(principal)
    relationships = active_relationship_model().objects
    lookup = {
        "resource_type": role.resource_type,
        "resource_id": role.resource_id,
        "relation": ROLE_RELATION,
        "subject_type": subject.subject_type,
        "subject_id": subject.subject_id,
        "optional_subject_relation": subject.optional_relation,
        "caveat_name": caveat_name,
    }
    if not relationships.filter(**lookup).exists():
        return False
    delete_relationship(
        RelationshipTuple(
            resource=role,
            relation=ROLE_RELATION,
            subject=subject,
            caveat_name=caveat_name,
        )
    )
    return True


def validate_role(value: str) -> ObjectRef:
    """Return ``value`` as a role object ref or raise."""

    role = ObjectRef.parse(value)
    if not is_role_type(role.resource_type):
        raise ValueError("Role must use '<namespace>/role:<id>' format.")
    return role


def relationship_rows(limit: int | None = PERMISSION_HUB_LIST_CAP) -> QuerySet[Any]:
    """Return active relationship rows in stable order."""

    relationship_model = active_relationship_model()
    rows = relationship_model.objects.order_by_resource()
    if limit is not None:
        rows = relationship_model.objects.filter(pk__in=Subquery(rows.values("pk")[:limit])).order_by_resource()
    return cast(QuerySet[Any], rows)


def permission_hub_roles(limit: int | None = PERMISSION_HUB_LIST_CAP) -> list[IAMRoleRow]:
    """Return roles visible from active role relationship rows."""

    return IAMRoleRow.from_relationships(permission_hub_role_rows(limit=limit))


def permission_hub_role_rows(limit: int | None = PERMISSION_HUB_LIST_CAP) -> QuerySet[Any]:
    """Return relationship rows that mention schema-declared role objects."""

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
    """Return direct user role grants with principal labels batched."""

    return IAMGrantRow.from_relationships(permission_hub_grant_rows(limit=limit), request=request)


def permission_hub_grant_rows(limit: int | None = PERMISSION_HUB_LIST_CAP) -> QuerySet[Any]:
    """Return direct user role-grant rows in stable order."""

    rows = active_relationship_model().objects.filter(
        resource_type__in=schema_role_resource_types(),
        relation=ROLE_RELATION,
        subject_type=app_settings.REBAC_USER_TYPE,
        optional_subject_relation="",
    )
    rows = rows.order_by_resource()
    if limit is not None:
        rows = rows[:limit]
    return cast(QuerySet[Any], rows)


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
