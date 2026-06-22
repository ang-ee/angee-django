"""GraphQL schema contributions for Angee IAM.

Pure identity: the user projection, the password session login, and the REBAC
permission hub. The OAuth/OIDC connection substrate (clients, external accounts,
credentials, connect/disconnect) lives in ``integrate``; OIDC *login* lives in
``iam_integrate_oidc``.
"""

from __future__ import annotations

import importlib.util
from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.models import Group as DjangoGroup
from django.db import transaction
from django.db.models import QuerySet
from rebac import (
    ObjectRef,
    app_settings,
    system_context,
)
from rebac import (
    backend as rebac_backend,
)
from rebac.models import active_relationship_model
from rebac.roles import (
    ROLE_RELATION,
)
from rebac.roles import (
    grant as rebac_grant,
)
from rebac.roles import (
    revoke as rebac_revoke,
)
from rebac.roles import (
    roles_of as rebac_roles_of,
)
from rebac.schema.ast import PermArrow, PermBinOp, PermNil, PermRef
from strawberry import auto
from strawberry.scalars import JSON
from strawberry_django.pagination import OffsetPaginated

from angee.base.models import SqidPublicIdentity
from angee.graphql.data import data_query
from angee.graphql.deletion import DeletePreview, delete_by_public_id
from angee.graphql.ids import PublicID, require_instance_for_id
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
from angee.iam.identity import user_display_label as _user_display_label
from angee.iam.identity import user_principal
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES as _ADMIN_PERMISSION_CLASSES
from angee.iam.permissions import request_from_info as _request


def _has_module_spec(dotted_path: str) -> bool:
    """Return whether ``dotted_path`` and each parent module can be found."""

    parts = dotted_path.split(".")
    for index in range(1, len(parts) + 1):
        if importlib.util.find_spec(".".join(parts[:index])) is None:
            return False
    return True


def _runtime_iam_models_built() -> bool:
    """Return whether the generated IAM runtime model module is importable."""

    runtime_module = str(getattr(settings, "ANGEE_RUNTIME_MODULE", "runtime"))
    return _has_module_spec(f"{runtime_module}.iam.models")


def _iam_model(name: str) -> type[Any]:
    """Return an IAM model, using the auth user while source tests are not built."""

    if name == "User" and not _runtime_iam_models_built():
        return cast(type[Any], get_user_model())
    return cast(type[Any], apps.get_model("iam", name))


User = _iam_model("User")
Group = DjangoGroup
GROUP_PUBLIC_IDENTITY = SqidPublicIdentity(prefix="grp_", model_label="iam.Group")
"""Public data identity for Django auth groups exposed by IAM."""

_IAM_OVERVIEW_DEFAULT_PEEK_LIMIT = 6
_IAM_OVERVIEW_MAX_PEEK_LIMIT = 100
_PERMISSION_HUB_LIST_CAP = 1000
_PRIVILEGED_PERMISSION_NAMES = frozenset({"create", "write", "delete"})
_ROLE_SUFFIX = "/role"


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
    is_staff: auto
    is_active: auto

    @strawberry_django.field
    def full_name(self) -> str:
        """Return the user's display name assembled by Django's auth contract."""

        return str(cast(Any, self).get_full_name())

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
    is_staff: auto
    is_active: auto

    @strawberry_django.field
    def preferences(self) -> JSON:
        """Return the current user's private UI preference object."""

        return _preference_object(cast(Any, self))

    @strawberry_django.field
    def role_refs(self) -> list[str]:
        """Return direct REBAC role grants for the current session user.

        There is no synchronous dataloader idiom in this repo. Keep role refs on
        the singleton ``currentUser`` path instead of exposing an N+1 admin-list
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


@strawberry.type
class IAMRoleType:
    """Tuple-derived role exposed by the IAM permission hub."""

    id: str
    namespace: str
    label: str
    description: str = ""


@strawberry_django.type(active_relationship_model())
class IAMGrantType:
    """Direct role grant for a user principal."""

    @strawberry_django.field
    def principal_id(self) -> str:
        """Return the granted user principal id."""

        return str(cast(Any, self).subject_id)

    @strawberry_django.field
    def principal_type(self) -> str:
        """Return the granted user principal type."""

        return str(cast(Any, self).subject_type)

    @strawberry_django.field
    def principal_label(self) -> str | None:
        """Return the principal's display name - no user object exposed."""

        return _user_display_label(cast(Any, self).subject_id)

    @strawberry_django.field
    def role(self) -> str:
        """Return the canonical granted role ref."""

        row = cast(Any, self)
        return _role_ref(str(row.resource_type), str(row.resource_id))


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


@strawberry.input
class UserInput:
    """Admin-write fields accepted when creating a user. ``password`` is write-only."""

    username: str
    password: str
    email: str = ""
    first_name: str = ""
    last_name: str = ""
    is_staff: bool = False
    is_active: bool = True


@strawberry.input
class UserPatch:
    """Admin-write fields accepted when updating a user. ``password`` re-hashes when set."""

    id: PublicID
    username: str | None = strawberry.UNSET
    password: str | None = strawberry.UNSET
    email: str | None = strawberry.UNSET
    first_name: str | None = strawberry.UNSET
    last_name: str | None = strawberry.UNSET
    is_staff: bool | None = strawberry.UNSET
    is_active: bool | None = strawberry.UNSET


def _relationship_ordering(*, include_relation: bool = False) -> tuple[str, ...]:
    """Return concrete field names for deterministic relationship ordering."""

    lookups = _relationship_storage_lookups()
    fields = [
        lookups["resource_type"],
        lookups["resource_id"],
        lookups["subject_type"],
        lookups["subject_id"],
    ]
    if include_relation:
        fields.insert(2, "relation")
    fields.extend(["optional_subject_relation", "caveat_name", "pk"])
    return tuple(fields)


def _relationship_storage_lookups() -> dict[str, str]:
    """Return ORM lookup names for the active REBAC relationship storage."""

    model = active_relationship_model()
    if any(field.name == "resource_fk" for field in model._meta.fields):
        return {
            "resource_type": "resource_fk__resource_type",
            "resource_id": "resource_fk__resource_id",
            "subject_type": "subject_fk__resource_type",
            "subject_id": "subject_fk__resource_id",
        }
    return {
        "resource_type": "resource_type",
        "resource_id": "resource_id",
        "subject_type": "subject_type",
        "subject_id": "subject_id",
    }


def _role_namespace(resource_type: str) -> str:
    """Return the namespace portion of a role resource type."""

    return resource_type.removesuffix(_ROLE_SUFFIX)


def _is_role_type(resource_type: str) -> bool:
    """Return whether ``resource_type`` names a role resource."""

    return resource_type.endswith(_ROLE_SUFFIX)


def _role_label(role_id: str) -> str:
    """Return a display label for a role id."""

    return role_id.replace("_", " ").replace("-", " ").title()


def _role_ref(resource_type: str, resource_id: str) -> str:
    """Return the canonical role object ref string."""

    return f"{resource_type}:{resource_id}"


def _validate_role(value: str) -> ObjectRef:
    """Return ``value`` as a role object ref or raise."""

    role = ObjectRef.parse(value)
    if not _is_role_type(role.resource_type):
        raise ValueError("Role must use '<namespace>/role:<id>' format.")
    return role


def _relationship_rows() -> QuerySet[Any]:
    """Return active relationship rows in stable order."""

    return (
        active_relationship_model()
        .objects.all()
        .order_by(
            *_relationship_ordering(include_relation=True),
        )
    )


def _permission_hub_roles() -> list[IAMRoleType]:
    """Return roles visible from active role relationship rows."""

    return _role_types_from_relationships(
        _permission_hub_role_rows(limit=_PERMISSION_HUB_LIST_CAP),
    )


def _permission_hub_role_rows(limit: int | None = None) -> QuerySet[Any]:
    """Return relationship rows that mention schema-declared role objects."""

    rows = (
        active_relationship_model()
        .objects.filter(resource_type__in=_schema_role_resource_types())
        .order_by(*_relationship_ordering())
    )
    if limit is not None:
        rows = rows[:limit]
    return cast(QuerySet[Any], rows)


def _role_types_from_relationships(rows: QuerySet[Any]) -> list[IAMRoleType]:
    """Return distinct role types from relationship rows."""

    roles: dict[tuple[str, str], IAMRoleType] = {}
    for row in rows:
        key = (row.resource_type, row.resource_id)
        if key in roles:
            continue
        roles[key] = IAMRoleType(
            id=row.resource_id,
            namespace=_role_namespace(row.resource_type),
            label=_role_label(row.resource_id),
        )
    return sorted(roles.values(), key=lambda role: (role.namespace, role.id))


def _permission_hub_grant_rows() -> QuerySet[Any]:
    """Return direct user role-grant rows in stable order."""

    return cast(
        QuerySet[Any],
        active_relationship_model()
        .objects.filter(
            resource_type__in=_schema_role_resource_types(),
            relation=ROLE_RELATION,
            subject_type=app_settings.REBAC_USER_TYPE,
            optional_subject_relation="",
        )
        .order_by(*_relationship_ordering()),
    )


def _permission_hub_grants(info: strawberry.Info) -> QuerySet[Any]:
    """Return direct user role-grant rows in stable order."""

    del info
    return _permission_hub_grant_rows()


def _schema_role_resource_types() -> set[str]:
    """Return role resource types declared by the installed REBAC schema."""

    return {
        definition.resource_type
        for definition in rebac_backend().schema().definitions
        if _is_role_type(definition.resource_type)
    }


def _schema_allowed_subject_name(allowed: Any) -> str:
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


def _permission_conditions(expression: Any) -> list[IAMPermCondition]:
    """Flatten a REBAC permission expression into leaf condition names."""

    names: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        names.append(name)

    def walk(node: Any) -> None:
        if isinstance(node, PermBinOp):
            walk(node.left)
            walk(node.right)
        elif isinstance(node, PermRef):
            add(node.name)
        elif isinstance(node, PermArrow):
            add(f"{node.via}->{node.target}")
        elif isinstance(node, PermNil):
            add("nil")
        else:
            add(type(node).__name__)

    walk(expression)
    return [IAMPermCondition(name=name) for name in names] or [IAMPermCondition(name="nil")]


def _permission_schema() -> list[IAMResourceSchemaType]:
    """Return the installed REBAC schema projected for the IAM console."""

    schema = rebac_backend().schema()
    resources: list[IAMResourceSchemaType] = []
    definitions = sorted(schema.definitions, key=lambda item: item.resource_type)
    for definition in definitions[:_PERMISSION_HUB_LIST_CAP]:
        relations = [
            IAMRelationType(
                name=relation.name,
                allowed_subject_types=[_schema_allowed_subject_name(allowed) for allowed in relation.allowed_subjects],
            )
            for relation in sorted(definition.relations, key=lambda item: item.name)
        ]
        permissions = [
            IAMPermissionType(
                name=permission.name,
                conditions=_permission_conditions(permission.expression),
            )
            for permission in sorted(definition.permissions, key=lambda item: item.name)
        ]
        resources.append(
            IAMResourceSchemaType(
                resource_type=definition.resource_type,
                relations=relations,
                permissions=permissions,
            )
        )
    return resources


def _iam_overview(peek_limit: int) -> IAMOverviewType:
    """Return IAM dashboard facts independent of paginated list rows."""

    peek_limit = _clamped_peek_limit(peek_limit)
    with system_context(reason="iam.graphql.overview"):
        role_rows = _role_types_from_relationships(_permission_hub_role_rows())
        grant_rows = list(_permission_hub_grant_rows())
        privileged_role_refs = _privileged_role_refs()
        privileged_grants = [
            row for row in grant_rows if _role_ref(str(row.resource_type), str(row.resource_id)) in privileged_role_refs
        ]
        unassigned_queryset = _unassigned_user_queryset(grant_rows)
        unassigned_users = list(unassigned_queryset[:peek_limit])
        namespaces = _overview_namespaces(role_rows, grant_rows)
        return IAMOverviewType(
            user_count=User._default_manager.count(),
            role_count=len(role_rows),
            grant_count=len(grant_rows),
            relationship_count=_relationship_rows().count(),
            privileged_grant_count=len(privileged_grants),
            unassigned_user_count=unassigned_queryset.count(),
            namespaces=namespaces,
            privileged_grants=cast(list[IAMGrantType], privileged_grants[:peek_limit]),
            unassigned_users=cast(list[UserType], unassigned_users),
        )


def _clamped_peek_limit(value: int) -> int:
    """Return a bounded overview preview size."""

    return max(0, min(value, _IAM_OVERVIEW_MAX_PEEK_LIMIT))


def _overview_namespaces(
    roles: list[IAMRoleType],
    grants: list[Any],
) -> list[IAMOverviewNamespaceType]:
    """Return namespace-level role and direct-grant counts."""

    counts: dict[str, dict[str, int]] = {}
    for role in roles:
        entry = counts.setdefault(role.namespace, {"roles": 0, "grants": 0})
        entry["roles"] += 1
    for grant in grants:
        namespace = _role_namespace(str(grant.resource_type))
        entry = counts.setdefault(namespace, {"roles": 0, "grants": 0})
        entry["grants"] += 1
    return [
        IAMOverviewNamespaceType(
            namespace=namespace,
            role_count=count["roles"],
            grant_count=count["grants"],
        )
        for namespace, count in sorted(counts.items())
    ]


def _unassigned_user_queryset(grants: list[Any]) -> QuerySet[Any]:
    """Return users without direct role grants."""

    assigned_ids = {str(row.subject_id) for row in grants}
    queryset = User._default_manager.all().order_by(*_user_ordering())
    if assigned_ids:
        queryset = queryset.exclude(**{f"{_user_subject_lookup()}__in": assigned_ids})
    return cast(QuerySet[Any], queryset)


def _user_subject_lookup() -> str:
    """Return the User field lookup used by REBAC actor subject ids."""

    subject_id_attr = str(getattr(User._meta, "rebac_id_attr", None) or app_settings.REBAC_USER_ID_ATTR)
    if subject_id_attr == "pk":
        pk = User._meta.pk
        return pk.name if pk is not None else "pk"
    return subject_id_attr


def _user_ordering() -> tuple[str, ...]:
    """Return deterministic ordering for IAM overview user previews."""

    concrete_fields = {field.name for field in User._meta.fields}
    fields: list[str] = []
    username_field = str(getattr(User, "USERNAME_FIELD", ""))
    if username_field in concrete_fields:
        fields.append(username_field)
    pk = User._meta.pk
    if pk is not None and pk.name not in fields:
        fields.append(pk.name)
    return tuple(fields or ("pk",))


def _privileged_role_refs() -> set[str]:
    """Return role refs that the installed REBAC schema treats as privileged."""

    schema = rebac_backend().schema()
    refs: set[str] = set()
    universal_role = app_settings.REBAC_UNIVERSAL_ADMIN_ROLE
    if universal_role:
        refs.add(str(ObjectRef.parse(universal_role)))
    for definition in schema.definitions:
        relations = {relation.name: relation for relation in definition.relations}
        permissions = {permission.name: permission for permission in definition.permissions}
        admin_relation = relations.get("admin")
        if admin_relation is not None:
            refs.update(_relation_role_refs(admin_relation))
        for permission_name in _PRIVILEGED_PERMISSION_NAMES:
            permission = permissions.get(permission_name)
            if permission is not None:
                refs.update(
                    _permission_role_refs(
                        permission.expression,
                        relations=relations,
                        permissions=permissions,
                    )
                )
    return refs


def _permission_role_refs(
    expression: Any,
    *,
    relations: dict[str, Any],
    permissions: dict[str, Any],
    seen_permissions: set[str] | None = None,
) -> set[str]:
    """Return role refs reachable from one REBAC permission expression."""

    refs: set[str] = set()
    seen = seen_permissions or set()
    if isinstance(expression, PermBinOp):
        refs.update(
            _permission_role_refs(
                expression.left,
                relations=relations,
                permissions=permissions,
                seen_permissions=seen,
            )
        )
        refs.update(
            _permission_role_refs(
                expression.right,
                relations=relations,
                permissions=permissions,
                seen_permissions=seen,
            )
        )
    elif isinstance(expression, PermRef):
        relation = relations.get(expression.name)
        if relation is not None:
            refs.update(_relation_role_refs(relation))
        elif expression.name in permissions and expression.name not in seen:
            seen.add(expression.name)
            refs.update(
                _permission_role_refs(
                    permissions[expression.name].expression,
                    relations=relations,
                    permissions=permissions,
                    seen_permissions=seen,
                )
            )
    elif isinstance(expression, PermArrow):
        relation = relations.get(expression.via)
        if relation is not None:
            refs.update(_relation_role_refs(relation))
    return refs


def _relation_role_refs(relation: Any) -> set[str]:
    """Return concrete role refs named by one REBAC relation declaration."""

    refs: set[str] = set()
    const_id = str(getattr(getattr(relation, "backing", None), "target_id", ""))
    for allowed in relation.allowed_subjects:
        resource_type = str(allowed.type)
        if not _is_role_type(resource_type):
            continue
        role_id = str(getattr(allowed, "id", "") or const_id)
        if role_id:
            refs.add(_role_ref(resource_type, role_id))
    return refs


def _permission_relationships(
    info: strawberry.Info,
    *,
    resource_type: str | None = None,
    subject_type: str | None = None,
    relation: str | None = None,
) -> QuerySet[Any]:
    """Return active relationship rows, optionally narrowed by core columns."""

    del info
    rows = _relationship_rows()
    if resource_type:
        rows = rows.filter(resource_type=resource_type)
    if subject_type:
        rows = rows.filter(subject_type=subject_type)
    if relation:
        rows = rows.filter(relation=relation)
    return cast(QuerySet[Any], rows)


def _user_graphql_type_name() -> str:
    """Return the registered GraphQL type name for console user rows."""

    return str(cast(Any, UserType).__strawberry_definition__.name)


@strawberry_django.filter_type(User, lookups=True)
class UserFilter:
    """Field lookups accepted when filtering the admin users list."""

    username: auto
    email: auto
    first_name: auto
    last_name: auto
    is_staff: auto
    is_active: auto


@strawberry_django.order_type(User)
class UserOrder:
    """Orderings accepted by the admin users list."""

    username: auto
    email: auto
    first_name: auto
    last_name: auto
    is_staff: auto
    is_active: auto


@strawberry_django.filter_type(Group, lookups=True)
class GroupFilter:
    """Field lookups accepted when filtering Django auth groups."""

    name: auto


@strawberry_django.order_type(Group)
class GroupOrder:
    """Orderings accepted by the auth groups list."""

    name: auto


UserDataQuery, _USER_DATA_TYPES = data_query(
    UserType,
    type_name="UserDataQuery",
    filters=UserFilter,
    order=UserOrder,
    list_name="users",
    detail_name="user",
    aggregate_name="user_aggregate",
    group_name="user_groups",
    aggregate_fields=["id"],
    group_by_fields=["is_staff", "is_active"],
    enable_filter_echo=True,
    permission_classes=_ADMIN_PERMISSION_CLASSES,
    aggregate_kwargs={"name_prefix": "UserAggregate", "pagination_style": "offset"},
    # Bare source tests use django.contrib.auth.User before the composer emits iam.User.
    allow_raw_pk_compat=not _runtime_iam_models_built(),
)


GroupDataQuery, _GROUP_DATA_TYPES = data_query(
    GroupType,
    type_name="GroupDataQuery",
    filters=GroupFilter,
    order=GroupOrder,
    list_name="groups",
    detail_name="group",
    aggregate_name="group_aggregate",
    group_name="group_groups",
    aggregate_fields=["id"],
    group_by_fields=["name"],
    enable_filter_echo=True,
    permission_classes=_ADMIN_PERMISSION_CLASSES,
    aggregate_kwargs={"name_prefix": "GroupAggregate", "pagination_style": "offset"},
    public_identity=GROUP_PUBLIC_IDENTITY,
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

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def roles(self) -> list[IAMRoleType]:
        """Return active tuple-derived roles."""

        return _permission_hub_roles()

    grants: OffsetPaginated[IAMGrantType] = strawberry_django.offset_paginated(
        resolver=_permission_hub_grants,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def rebac_schema(self) -> list[IAMResourceSchemaType]:
        """Return the installed REBAC schema projection."""

        return _permission_schema()

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def iam_overview(
        self,
        peek_limit: int = _IAM_OVERVIEW_DEFAULT_PEEK_LIMIT,
    ) -> IAMOverviewType:
        """Return IAM dashboard aggregates and peek rows."""

        return _iam_overview(peek_limit)

    relationships: OffsetPaginated[IAMRelationshipType] = strawberry_django.offset_paginated(
        resolver=_permission_relationships,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )


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
        user = authenticate(
            request,
            username=username,
            password=password,
        )
        if user is None:
            return LoginPayload(ok=False)
        with system_context(reason="iam.login"):
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

        request = _request(info)
        user = getattr(request, "user", None)
        if isinstance(user, AnonymousUser) or not getattr(user, "is_authenticated", False):
            raise ValueError("Authentication required")
        user = cast(Any, user)
        if not isinstance(preferences, dict):
            raise ValueError("preferences must be a JSON object")
        if not hasattr(user, "preferences"):
            raise ValueError("The active user model does not support preferences")
        with system_context(reason="iam.preferences.update"), transaction.atomic():
            user.preferences = dict(preferences)
            user.save(update_fields=["preferences"])
        return cast(CurrentUserType, user)


@strawberry.type
class IAMUserMutation:
    """Admin CRUD for users; ``password`` is write-only and hashed via ``set_password``."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def create_user(self, data: UserInput) -> UserType:
        """Create one user with a hashed password."""

        with system_context(reason="iam.graphql.user.create"), transaction.atomic():
            user = User.objects.create_user(
                data.username,
                email=data.email,
                password=data.password,
                first_name=data.first_name,
                last_name=data.last_name,
                is_staff=data.is_staff,
                is_active=data.is_active,
            )
        return cast(UserType, user)

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def update_user(self, data: UserPatch) -> UserType:
        """Update one user; re-hash the password only when a new one is supplied."""

        with system_context(reason="iam.graphql.user.update"), transaction.atomic():
            user = require_instance_for_id(User, data.id, queryset=User._default_manager.all())
            for field in ("username", "email", "first_name", "last_name", "is_staff", "is_active"):
                value = getattr(data, field)
                if value is not strawberry.UNSET:
                    setattr(user, field, value)
            if data.password is not strawberry.UNSET and data.password:
                user.set_password(data.password)
            user.save()
        return cast(UserType, user)

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def delete_user(self, id: PublicID, confirm: bool = False) -> DeletePreview:
        """Delete one user when unblocked."""

        return delete_by_public_id(
            User,
            str(id),
            reason="iam.graphql.user.delete",
            confirm=confirm,
        )


@strawberry.type
class IAMPermissionHubMutation:
    """Admin mutations for tuple-backed IAM role grants."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def grant_role(self, principal_id: str, role: str) -> bool:
        """Grant a role to one user principal."""

        role_ref = _validate_role(role)
        principal = user_principal(principal_id, graphql_type_name=_user_graphql_type_name())
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
        principal = user_principal(principal_id, graphql_type_name=_user_graphql_type_name())
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
            UserDataQuery,
            GroupDataQuery,
        ],
        "mutation": [
            IAMMutation,
            IAMUserMutation,
            IAMPermissionHubMutation,
        ],
        "subscription": [changes(User, field="userChanged")],
        "types": [
            UserType,
            CurrentUserType,
            GroupType,
            IAMRoleType,
            IAMGrantType,
            IAMRelationType,
            IAMPermCondition,
            IAMPermissionType,
            IAMResourceSchemaType,
            IAMOverviewNamespaceType,
            IAMOverviewType,
            *_USER_DATA_TYPES,
            *_GROUP_DATA_TYPES,
            IAMRelationshipType,
        ],
    },
}
"""GraphQL contributions installed by the IAM addon."""
