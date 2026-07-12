"""GraphQL schema contributions for Angee IAM.

Pure identity: the user projection, the password session login, and the REBAC
permission hub. The OAuth/OIDC connection substrate (clients, external accounts,
credentials, connect/disconnect) lives in ``integrate``; OIDC *login* lives in
``iam_integrate_oidc``.
"""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.models import Group as DjangoGroup
from django.db import transaction
from django.db.models import Q, QuerySet
from django.http import HttpRequest
from pydantic import BaseModel
from rebac import (
    system_context,
)
from rebac.models import active_relationship_model
from rebac.roles import (
    grant as rebac_grant,
)
from rebac.roles import (
    revoke as rebac_revoke,
)
from rebac.roles import (
    roles_of as rebac_roles_of,
)
from strawberry import auto
from strawberry.scalars import JSON

from angee.base.models import SqidPublicIdentity, instance_from_public_id
from angee.graphql.data import (
    AngeeHasuraWriteBackend,
    aggregate_queryset,
    hasura_model_resource,
    hasura_pydantic_resource,
    public_pk_decoder,
)
from angee.graphql.deletion import DeletePreview, attach_delete_preview_metadata
from angee.graphql.ids import PublicID, to_public_id
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
from angee.graphql.writes import write_queryset
from angee.iam.identity import user_label, user_principal
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES as _ADMIN_PERMISSION_CLASSES
from angee.iam.permissions import is_platform_admin, require_platform_admin, session_user
from angee.iam.permissions import request_from_info as _request
from angee.iam.roles import (
    IAM_OVERVIEW_DEFAULT_PEEK_LIMIT as _IAM_OVERVIEW_DEFAULT_PEEK_LIMIT,
)
from angee.iam.roles import (
    GrantInfo,
    OverviewInfo,
    PermissionConditionInfo,
    PermissionInfo,
    RelationInfo,
    ResourceSchemaInfo,
    RoleInfo,
    user_ordering,
    user_subject_filter,
)
from angee.iam.roles import (
    iam_overview as _iam_overview_owner,
)
from angee.iam.roles import (
    permission_hub_grants as _permission_hub_grants_owner,
)
from angee.iam.roles import (
    permission_hub_roles as _permission_hub_roles_owner,
)
from angee.iam.roles import (
    permission_schema as _permission_schema_owner,
)
from angee.iam.roles import (
    relationship_rows as _relationship_rows_owner,
)
from angee.iam.roles import (
    validate_role as _validate_role,
)

User = cast(type[Any], get_user_model())
Group = DjangoGroup
Company = apps.get_model("iam", "Company")
GROUP_PUBLIC_IDENTITY = SqidPublicIdentity(prefix="grp_", min_length=8)
"""Public data identity for Django auth groups exposed by IAM."""

_ROLE_SUFFIX = "/role"

COLLEAGUES_DEFAULT_LIMIT = 20
"""Default page size for the member-scoped ``colleagues`` people surface."""

COLLEAGUES_MAX_LIMIT = 100
"""Upper bound a ``colleagues`` caller's ``limit`` is clamped to."""


def _preference_object(user: Any) -> JSON:
    """Return a safe UI preference object for user projections."""

    preferences = getattr(user, "preferences", {})
    return cast(JSON, preferences if isinstance(preferences, dict) else {})


@strawberry_django.type(User)
class UserType(AngeeNode):
    """GraphQL projection of an Angee user for shared/admin lists."""

    username: auto
    first_name: auto
    last_name: auto
    email: auto
    kind: auto
    is_staff: auto
    is_active: auto

    @strawberry_django.field(only=["first_name", "last_name", "username"])
    def display_name(self) -> str:
        """Return the user's human label, overriding the username Node default."""

        return user_label(cast(Any, self))

    @strawberry_django.field
    def full_name(self) -> str:
        """Return the user's display name assembled by Django's auth contract."""

        return user_label(cast(Any, self))

    @strawberry_django.field
    def preferences(self) -> JSON:
        """Return the user's private UI preference object."""

        return _preference_object(cast(Any, self))


@strawberry_django.type(User)
class CurrentUserType(AngeeNode):
    """GraphQL projection of the session user, including private role refs."""

    username: auto
    first_name: auto
    last_name: auto
    email: auto
    kind: auto
    is_staff: auto
    is_active: auto

    @strawberry_django.field(only=["first_name", "last_name", "username"])
    def display_name(self) -> str:
        """Return the user's human label, overriding the username Node default."""

        return user_label(cast(Any, self))

    @strawberry_django.field
    def preferences(self) -> JSON:
        """Return the current user's private UI preference object."""

        return _preference_object(cast(Any, self))

    @strawberry_django.field
    def role_refs(self) -> list[str]:
        """Return direct REBAC role grants for the current session user.

        There is no synchronous dataloader idiom in this repo. Keep role refs on
        the singleton ``current_user`` path instead of exposing an N+1 admin-list
        field that can reveal another user's roles.
        """

        return sorted(str(role) for role in rebac_roles_of(cast(Any, self)))


@strawberry_django.type(Group)
class GroupType:
    """GraphQL projection of Django auth groups with Angee public ids."""

    name: auto

    @strawberry.field(description="The public ID of this object.")
    def id(self) -> PublicID:
        """Return this group row's IAM public id."""

        return PublicID(GROUP_PUBLIC_IDENTITY.public_id_from_pk(cast(Any, self).pk))


@strawberry_django.type(Company)
class CompanyType(AngeeNode):
    """GraphQL projection of a company of record for the console admin page."""

    name: auto
    is_archived: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["parent_id"])
    def parent(self) -> strawberry.ID | None:
        """Return the parent company's public id, if this company has one."""

        return to_public_id(Company, cast(Any, self).parent_id)


@strawberry.type
class IAMRoleType:
    """Tuple-derived role exposed by the IAM permission hub."""

    id: str
    namespace: str
    label: str


@strawberry.type
class IAMGrantType:
    """Direct role grant for a user principal."""

    principal_id: str
    principal_type: str
    principal_label: str
    principal_ref: str
    role: str
    role_name: str
    namespace: str


@strawberry.type
class IAMRelationType:
    """Relation declaration from the installed REBAC schema."""

    name: str
    allowed_subject_types: list[str]


@strawberry.type
class IAMPermCondition:
    """Flattened permission expression leaf."""

    name: str


@strawberry.type
class IAMPermissionType:
    """Permission declaration from the installed REBAC schema."""

    name: str
    conditions: list[IAMPermCondition]


@strawberry.type
class IAMResourceSchemaType:
    """Resource definition projected from the installed REBAC schema."""

    resource_type: str
    relations: list[IAMRelationType]
    permissions: list[IAMPermissionType]


@strawberry.type
class IAMOverviewNamespaceType:
    """Role namespace aggregate shown by the IAM overview."""

    namespace: str
    role_count: int
    grant_count: int


@strawberry_django.type(active_relationship_model())
class IAMRelationshipType:
    """Raw active REBAC relationship tuple."""

    @strawberry_django.field
    def id(self) -> str:
        """Return the relationship row's primary-key identity."""

        return str(cast(Any, self).pk)

    @strawberry_django.field
    def resource_type(self) -> str:
        """Return the relationship resource type."""

        return str(cast(Any, self).resource_type)

    @strawberry_django.field
    def resource_id(self) -> str:
        """Return the relationship resource id."""

        return str(cast(Any, self).resource_id)

    @strawberry_django.field
    def relation(self) -> str:
        """Return the relationship name."""

        return str(cast(Any, self).relation)

    @strawberry_django.field
    def subject_type(self) -> str:
        """Return the relationship subject type."""

        return str(cast(Any, self).subject_type)

    @strawberry_django.field
    def subject_id(self) -> str:
        """Return the relationship subject id."""

        return str(cast(Any, self).subject_id)

    @strawberry_django.field
    def subject_relation(self) -> str:
        """Return the optional subject-set relation."""

        return str(cast(Any, self).optional_subject_relation)

    @strawberry_django.field
    def caveat_name(self) -> str:
        """Return the relationship caveat name."""

        return str(cast(Any, self).caveat_name)


@strawberry.type
class IAMOverviewType:
    """IAM dashboard facts computed by the IAM backend owner."""

    user_count: int
    role_count: int
    grant_count: int
    relationship_count: int
    privileged_grant_count: int
    unassigned_user_count: int
    namespaces: list[IAMOverviewNamespaceType]
    privileged_grants: list[IAMGrantType]
    unassigned_users: list[UserType]


@strawberry.type
class LoginPayload:
    """Result returned by the session login mutation."""

    ok: bool
    user: UserType | None = None


def _role_type(role: RoleInfo) -> IAMRoleType:
    """Project a permission-hub role value to its Strawberry type."""

    return IAMRoleType(
        id=role.id,
        namespace=role.namespace,
        label=role.label,
    )


def _grant_type(grant: GrantInfo) -> IAMGrantType:
    """Project a permission-hub grant value to its Strawberry type."""

    return IAMGrantType(
        principal_id=grant.principal_id,
        principal_type=grant.principal_type,
        principal_label=grant.principal_label,
        principal_ref=grant.principal_ref,
        role=grant.role,
        role_name=grant.role_name,
        namespace=grant.namespace,
    )


def _relation_type(relation: RelationInfo) -> IAMRelationType:
    """Project a REBAC relation declaration to its Strawberry type."""

    return IAMRelationType(
        name=relation.name,
        allowed_subject_types=relation.allowed_subject_types,
    )


def _permission_condition_type(condition: PermissionConditionInfo) -> IAMPermCondition:
    """Project a REBAC permission expression leaf to its Strawberry type."""

    return IAMPermCondition(name=condition.name)


def _permission_type(permission: PermissionInfo) -> IAMPermissionType:
    """Project a REBAC permission declaration to its Strawberry type."""

    return IAMPermissionType(
        name=permission.name,
        conditions=[_permission_condition_type(condition) for condition in permission.conditions],
    )


def _resource_schema_type(resource: ResourceSchemaInfo) -> IAMResourceSchemaType:
    """Project one REBAC resource declaration to its Strawberry type."""

    return IAMResourceSchemaType(
        resource_type=resource.resource_type,
        relations=[_relation_type(relation) for relation in resource.relations],
        permissions=[_permission_type(permission) for permission in resource.permissions],
    )


def _overview_namespace_type(namespace: Any) -> IAMOverviewNamespaceType:
    """Project one IAM overview namespace value to its Strawberry type."""

    return IAMOverviewNamespaceType(
        namespace=namespace.namespace,
        role_count=namespace.role_count,
        grant_count=namespace.grant_count,
    )


def _overview_type(overview: OverviewInfo) -> IAMOverviewType:
    """Project IAM overview values to the GraphQL return type."""

    return IAMOverviewType(
        user_count=overview.user_count,
        role_count=overview.role_count,
        grant_count=overview.grant_count,
        relationship_count=overview.relationship_count,
        privileged_grant_count=overview.privileged_grant_count,
        unassigned_user_count=overview.unassigned_user_count,
        namespaces=[_overview_namespace_type(namespace) for namespace in overview.namespaces],
        privileged_grants=[_grant_type(grant) for grant in overview.privileged_grants],
        unassigned_users=cast(list[UserType], overview.unassigned_users),
    )


def _permission_hub_roles() -> list[IAMRoleType]:
    """Return roles visible from active role relationship rows."""

    return [_role_type(role) for role in _permission_hub_roles_owner()]


def _permission_schema() -> list[IAMResourceSchemaType]:
    """Return the installed REBAC schema projected for the IAM console."""

    return [_resource_schema_type(resource) for resource in _permission_schema_owner()]


def _iam_overview(peek_limit: int, *, request: HttpRequest | None = None) -> IAMOverviewType:
    """Return IAM dashboard facts independent of paginated list rows."""

    return _overview_type(_iam_overview_owner(peek_limit, request=request))


def _admin_relationship_queryset(info: strawberry.Info) -> QuerySet[Any]:
    """Return active REBAC relationship rows scoped to platform admins.

    The Hasura ``relationships`` resource replaces the authored ``relationships``
    query; like the other permission-hub surfaces it is admin-only, so a
    non-admin actor reads the empty set (``.none()``) rather than a forbidden
    error — admin-only navigation already gates the console.
    """

    if not _admin_actor(info):
        return cast(QuerySet[Any], active_relationship_model().objects.none())
    return _relationship_rows_owner()


def _admin_user_queryset(info: strawberry.Info) -> QuerySet[Any]:
    """Return the admin-scoped user queryset for console resources."""

    require_platform_admin(info)
    return cast(QuerySet[Any], User.objects.people())


def _colleagues(actor: Any, *, search: str, limit: int) -> list[Any]:
    """Return the actor's active co-workers for the member-scoped people surface.

    Scoped to the actor's own companies' direct members through the IAM membership
    owner (``Company.co_member_subject_ids``) — never platform-wide. The actor is
    excluded (a picker of *other* people), inactive users are dropped, and the
    matched rows are read elevated because the shared-company membership tuple is
    itself the authorization (``auth/user`` read is otherwise admin-only). Ordering
    is deterministic and the result is capped at :data:`COLLEAGUES_MAX_LIMIT`.
    """

    subject_ids = apps.get_model("iam", "Company").objects.co_member_subject_ids(actor)
    if not subject_ids:
        return []
    bounded = max(1, min(limit, COLLEAGUES_MAX_LIMIT))
    queryset = (
        User.objects.system_context(reason="iam.colleagues: shared company membership is the authorization")
        .people()
        .filter(user_subject_filter(User, subject_ids), is_active=True)
        .exclude(pk=actor.pk)
    )
    term = search.strip()
    if term:
        queryset = queryset.filter(
            Q(username__icontains=term)
            | Q(first_name__icontains=term)
            | Q(last_name__icontains=term)
            | Q(email__icontains=term)
        )
    return list(queryset.order_by(*user_ordering(User))[:bounded])


def _admin_user_aggregate_queryset(info: strawberry.Info) -> QuerySet[Any]:
    """Return the user queryset safe for aggregate and grouped math."""

    return aggregate_queryset(_admin_user_queryset(info))


def _admin_group_queryset(info: strawberry.Info) -> QuerySet[Any]:
    """Return the admin-scoped Django auth-group catalogue queryset."""

    require_platform_admin(info)
    return cast(QuerySet[Any], Group.objects.all())


def _user_for_resource_id(value: str, queryset: QuerySet[Any]) -> Any:
    """Return one user addressed by the Hasura resource id boundary."""

    instance = instance_from_public_id(User, str(value), queryset=queryset)
    if instance is None:
        raise ValueError(f"User {value!r} was not found")
    return instance


def _group_pk_from_public_id(value: Any) -> int | None:
    """Decode the IAM group public id to its Django primary key."""

    return GROUP_PUBLIC_IDENTITY.public_id_to_pk(str(value))


def _group_for_resource_id(value: str, queryset: QuerySet[Any]) -> Any:
    """Return one Django auth group addressed by its IAM public id."""

    instance = instance_from_public_id(
        Group,
        str(value),
        queryset=queryset,
        public_identity=GROUP_PUBLIC_IDENTITY,
    )
    if instance is None:
        raise ValueError(f"Group {value!r} was not found")
    return instance


def _delete_instance(instance: Any) -> Any | None:
    """Delete ``instance`` in Hasura ``delete_<res>_by_pk`` form."""

    preview = DeletePreview.from_instance(instance)
    if preview.has_blockers:
        return None
    pk = instance.pk
    instance.delete()
    instance.pk = pk
    return instance


def _delete_user_preview(value: str, *, confirm: bool) -> DeletePreview:
    """Return or apply the authored user cascade delete preview."""

    with transaction.atomic():
        instance = _user_for_resource_id(str(value), write_queryset(User))
        preview = DeletePreview.from_instance(instance)
        if confirm and not preview.has_blockers:
            instance.delete()
        return preview


class IAMUserWriteBackend:
    """Admin write semantics for the Hasura ``users`` resource."""

    def create(self, info: strawberry.Info, data: dict[str, Any]) -> Any:
        """Create one user through Django's password-hashing manager."""

        require_platform_admin(info)
        payload = dict(data)
        password = payload.pop("password")
        with transaction.atomic():
            return User.objects.create_user(password=password, **payload)

    def update(self, info: strawberry.Info, pk: str, data: dict[str, Any]) -> Any:
        """Patch one user, hashing ``password`` when supplied."""

        require_platform_admin(info)
        payload = dict(data)
        password = payload.pop("password", None)
        with transaction.atomic():
            user = _user_for_resource_id(pk, write_queryset(User))
            for field, value in payload.items():
                setattr(user, field, value)
            update_fields = set(payload)
            if password:
                user.set_password(password)
                update_fields.add("password")
            user.full_clean()
            if not update_fields:
                return user
            user.save(update_fields=update_fields)
            return user

    def delete(self, info: strawberry.Info, pk: str) -> Any | None:
        """Delete one user by public id and return the deleted row."""

        require_platform_admin(info)
        with transaction.atomic():
            return _delete_instance(_user_for_resource_id(pk, write_queryset(User)))


class IAMGroupWriteBackend:
    """Admin write semantics for the Hasura ``groups`` resource."""

    def create(self, info: strawberry.Info, data: dict[str, Any]) -> Any:
        """Create one Django auth group."""

        require_platform_admin(info)
        with transaction.atomic():
            group = Group(**data)
            group.full_clean()
            group.save()
            return group

    def update(self, info: strawberry.Info, pk: str, data: dict[str, Any]) -> Any:
        """Patch one Django auth group."""

        require_platform_admin(info)
        with transaction.atomic():
            group = _group_for_resource_id(pk, Group.objects.all())
            for field, value in data.items():
                setattr(group, field, value)
            group.full_clean()
            group.save()
            return group

    def delete(self, info: strawberry.Info, pk: str) -> Any | None:
        """Delete one Django auth group by public id."""

        require_platform_admin(info)
        with transaction.atomic():
            return _delete_instance(_group_for_resource_id(pk, Group.objects.all()))


class IAMRoleRow(BaseModel):
    """Computed IAM role row (no Django table behind it).

    The row-shape SSOT for the ``iam.Role`` Hasura resource. Roles are deduped
    from active role-relationship tuples and labelled from the REBAC schema AST
    (the same computation the authored ``roles`` query exposed). ``IAMRoleType``
    keys by the short ``resource_id`` (``role_id``), which is not unique across
    namespaces; the row adds an explicit ``id`` (the canonical ``<namespace>/role:<id>``
    ref) for by-pk addressing.
    """

    id: str
    role_id: str
    namespace: str
    label: str


class IAMGrantRow(BaseModel):
    """Computed IAM role-grant row (no Django table behind it).

    The row-shape SSOT for the ``iam.Grant`` Hasura resource, projected from the
    direct user role-grant tuples (the same rows the authored ``grants`` query
    paginated). The principal/role pair is unique, so ``id`` is the
    ``<principal_ref>:<role>`` composite for by-pk addressing.
    """

    id: str
    principal_id: str
    principal_type: str
    principal_ref: str
    principal_label: str
    role: str
    role_name: str
    namespace: str


def _role_rows() -> list[IAMRoleRow]:
    """Project active tuple-derived roles as computed resource rows."""

    return [
        IAMRoleRow(
            id=f"{role.namespace}{_ROLE_SUFFIX}:{role.id}",
            role_id=role.id,
            namespace=role.namespace,
            label=role.label,
        )
        for role in _permission_hub_roles()
    ]


def _grant_rows(request: HttpRequest | None = None) -> list[IAMGrantRow]:
    """Project direct user role-grant tuples as computed resource rows."""

    return [
        IAMGrantRow(
            id=f"{grant.principal_ref}:{grant.role}",
            principal_id=grant.principal_id,
            principal_type=grant.principal_type,
            principal_ref=grant.principal_ref,
            principal_label=grant.principal_label,
            role=grant.role,
            role_name=grant.role_name,
            namespace=grant.namespace,
        )
        for grant in _permission_hub_grants_owner(request=request)
    ]


def _admin_actor(info: strawberry.Info) -> bool:
    """Return whether the request actor reaches IAM's platform-admin role."""

    return is_platform_admin(getattr(_request(info), "user", None))


def _role_rows_for(info: strawberry.Info) -> list[IAMRoleRow]:
    """Row provider gated on the same platform-admin reach the authored query had."""

    if not _admin_actor(info):
        return []
    with system_context(reason="iam.graphql.roles"):
        return _role_rows()


def _grant_rows_for(info: strawberry.Info) -> list[IAMGrantRow]:
    """Row provider gated on the same platform-admin reach the authored query had."""

    if not _admin_actor(info):
        return []
    with system_context(reason="iam.graphql.grants"):
        return _grant_rows(_request(info))


_ROLE_RESOURCE = hasura_pydantic_resource(
    IAMRoleRow,
    name="iam_roles",
    model_label="iam.Role",
    filterable=["id", "role_id", "namespace", "label"],
    sortable=["role_id", "namespace", "label"],
    rows=_role_rows_for,
)


_GRANT_RESOURCE = hasura_pydantic_resource(
    IAMGrantRow,
    name="iam_grants",
    model_label="iam.Grant",
    filterable=["id", "principal_id", "principal_label", "role", "role_name", "namespace"],
    sortable=["principal_label", "role", "role_name", "namespace"],
    rows=_grant_rows_for,
)


_USER_RESOURCE = hasura_model_resource(
    UserType,
    model=User,
    name="users",
    filterable=["id", "username", "email", "first_name", "last_name", "is_staff", "is_active"],
    sortable=["username", "email", "first_name", "last_name", "is_staff", "is_active"],
    aggregatable=["id"],
    groupable=["is_staff", "is_active"],
    writable=["username", "password", "email", "first_name", "last_name", "is_staff", "is_active"],
    get_queryset=_admin_user_queryset,
    get_aggregate_queryset=_admin_user_aggregate_queryset,
    write_backend=IAMUserWriteBackend(),
    id_column="sqid",
    model_label="iam.User",
)


_GROUP_RESOURCE = hasura_model_resource(
    GroupType,
    model=Group,
    name="groups",
    filterable=["id", "name"],
    sortable=["name"],
    aggregatable=["id"],
    groupable=["name"],
    writable=["name"],
    get_queryset=_admin_group_queryset,
    get_aggregate_queryset=_admin_group_queryset,
    write_backend=IAMGroupWriteBackend(),
    id_decode=_group_pk_from_public_id,
    id_column="pk",
    model_label="iam.Group",
    public_id_field="id",
)


_COMPANY_RESOURCE = hasura_model_resource(
    CompanyType,
    model=Company,
    name="companies",
    filterable=["id", "name", "parent", "is_archived"],
    sortable=["name", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["parent", "is_archived"],
    insertable=["name", "parent", "is_archived"],
    updatable=["name", "parent", "is_archived"],
    field_id_decode={"parent": public_pk_decoder(Company)},
    write_backend=AngeeHasuraWriteBackend(Company, public_id_fields=("parent",)),
    model_label="iam.Company",
)


# Filter/sort only on columns the active relationship store materializes as
# direct concrete fields. The denormalized ``resource_type``/``subject_type``
# strings live behind ``resource_fk``/``subject_fk`` in registry storage mode, so
# they are not ORM-addressable single-field columns; ``relation`` and the caveat/
# subject-relation columns are concrete in both storage modes.
_RELATIONSHIP_FILTER_FIELDS = ("relation", "optional_subject_relation", "caveat_name")

_RELATIONSHIP_RESOURCE = hasura_model_resource(
    IAMRelationshipType,
    model=active_relationship_model(),
    name="relationships",
    filterable=list(_RELATIONSHIP_FILTER_FIELDS),
    sortable=list(_RELATIONSHIP_FILTER_FIELDS),
    aggregatable=["id"],
    get_queryset=_admin_relationship_queryset,
    insert=False,
    update=False,
    delete=False,
    id_decode=lambda value: value,
    id_column="id",
    model_label="iam.Relationship",
    # The group axes (resource_type/subject_type/relation) are denormalized
    # *display* strings on the node, not RelationshipRegistry columns, so there
    # is no server _groups over them. Like the original authored page, fetch the
    # (bounded, admin-only) tuple set once and group/filter/sort in the browser.
    row_model="client",
)


@strawberry.type
class IAMQuery:
    """Session-backed IAM queries."""

    @strawberry.field
    def current_user(self, info: strawberry.Info) -> CurrentUserType | None:
        """Return the authenticated session user, if any."""

        user = getattr(_request(info), "user", None)
        if isinstance(user, AnonymousUser) or not getattr(
            user,
            "is_authenticated",
            False,
        ):
            return None
        return cast(CurrentUserType, user)


@strawberry.type
class IAMConsoleQuery:
    """Admin IAM user and permission-hub queries."""

    @strawberry.field
    def colleagues(
        self,
        info: strawberry.Info,
        search: str = "",
        limit: int = COLLEAGUES_DEFAULT_LIMIT,
    ) -> list[UserType]:
        """Return the signed-in actor's co-workers for member-scoped people pickers.

        The member surface the admin-only ``users`` catalogue cannot serve: the
        active users who share a company of record with the actor (recipient
        suggestions, the discuss person picker). Requires a signed-in actor and is
        scoped to that actor's own companies' members — not platform-wide.
        """

        return cast(list[UserType], _colleagues(session_user(info), search=search, limit=limit))

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def roles(self) -> list[IAMRoleType]:
        """Return active tuple-derived roles."""

        return _permission_hub_roles()

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def rebac_schema(self) -> list[IAMResourceSchemaType]:
        """Return the installed REBAC schema projection."""

        return _permission_schema()

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def iam_overview(
        self,
        info: strawberry.Info,
        peek_limit: int = _IAM_OVERVIEW_DEFAULT_PEEK_LIMIT,
    ) -> IAMOverviewType:
        """Return IAM dashboard aggregates and peek rows."""

        return _iam_overview(peek_limit, request=_request(info))


@strawberry.type
class IAMMutation:
    """Session-backed IAM mutations."""

    @strawberry.mutation
    def login(
        self,
        info: strawberry.Info,
        username: str,
        password: str,
    ) -> LoginPayload:
        """Authenticate credentials and bind the user to the session."""

        request = _request(info)
        # Elevate the whole credential-verification flow: Django upgrades a
        # stale password hash inside ``check_password`` by saving the row
        # (``must_update`` — an iteration bump, salt-entropy policy, or algorithm
        # change), a system maintenance write the login itself sanctions. An
        # anonymous request has no actor, so under REBAC fail-closed that save
        # would raise, ``authenticate`` would swallow it as a backend refusal,
        # and a user with valid credentials would be denied.
        with system_context(reason="iam.login"):
            user = authenticate(
                request,
                username=username,
                password=password,
            )
            if user is None:
                return LoginPayload(ok=False)
            auth_login(request, user)
        return LoginPayload(ok=True, user=cast(UserType, user))

    @strawberry.mutation
    def logout(self, info: strawberry.Info) -> bool:
        """Clear the current session."""

        auth_logout(_request(info))
        return True

    @strawberry.mutation
    def update_preferences(
        self,
        info: strawberry.Info,
        preferences: JSON,
    ) -> CurrentUserType:
        """Replace the authenticated user's private UI preference object."""

        user = session_user(info)
        user.update_preferences(cast(dict[str, Any], preferences))
        return cast(CurrentUserType, user)


@strawberry.type
class IAMUserDeletePreviewMutation:
    """Authored cascade delete preview for users."""

    @strawberry.mutation(name="delete_user")
    def delete_user(self, info: strawberry.Info, id: PublicID, confirm: bool = False) -> DeletePreview:
        """Preview or confirm deletion of one user by public id."""

        require_platform_admin(info)
        return _delete_user_preview(str(id), confirm=confirm)


attach_delete_preview_metadata(
    IAMUserDeletePreviewMutation,
    model=User,
    node=UserType,
    field="delete_user",
)


@strawberry.type
class IAMPermissionHubMutation:
    """Admin mutations for tuple-backed IAM role grants."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def grant_role(self, principal_id: str, role: str) -> bool:
        """Grant a role to one user principal."""

        role_ref = _validate_role(role)
        principal = user_principal(principal_id)
        with (
            system_context(reason="iam.graphql.permission_hub.grant_role"),
            transaction.atomic(),
        ):
            rebac_grant(actor=principal, role=role_ref)
            return True

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def revoke_role(self, principal_id: str, role: str) -> bool:
        """Revoke a role from one user principal."""

        role_ref = _validate_role(role)
        principal = user_principal(principal_id)
        with (
            system_context(reason="iam.graphql.permission_hub.revoke_role"),
            transaction.atomic(),
        ):
            return bool(rebac_revoke(actor=principal, role=role_ref))


schemas = {
    "public": {
        "query": [IAMQuery],
        "mutation": [IAMMutation],
        "types": [
            UserType,
            CurrentUserType,
        ],
    },
    "console": {
        "query": [
            IAMQuery,
            IAMConsoleQuery,
            _USER_RESOURCE.query,
            _GROUP_RESOURCE.query,
            _COMPANY_RESOURCE.query,
            _ROLE_RESOURCE.query,
            _GRANT_RESOURCE.query,
            _RELATIONSHIP_RESOURCE.query,
        ],
        "mutation": [
            IAMMutation,
            _USER_RESOURCE.mutation,
            _GROUP_RESOURCE.mutation,
            _COMPANY_RESOURCE.mutation,
            IAMUserDeletePreviewMutation,
            IAMPermissionHubMutation,
        ],
        "subscription": [changes(User, field="userChanged")],
        "types": [
            UserType,
            CurrentUserType,
            GroupType,
            CompanyType,
            IAMRoleType,
            IAMGrantType,
            IAMRelationType,
            IAMPermCondition,
            IAMPermissionType,
            IAMResourceSchemaType,
            IAMOverviewNamespaceType,
            IAMOverviewType,
            *_USER_RESOURCE.types,
            *_GROUP_RESOURCE.types,
            *_COMPANY_RESOURCE.types,
            *_ROLE_RESOURCE.types,
            *_GRANT_RESOURCE.types,
            *_RELATIONSHIP_RESOURCE.types,
        ],
    },
}
"""GraphQL contributions installed by the IAM addon."""
