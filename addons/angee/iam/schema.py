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
from strawberry import auto, relay
from strawberry_django.pagination import OffsetPaginated

from angee.base.models import instance_from_public_id
from angee.graphql.deletion import DeletePreview
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
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

_PERMISSION_HUB_LIST_CAP = 1000
_ROLE_SUFFIX = "/role"


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
    def role_refs(self) -> list[str]:
        """Return direct REBAC role grants for the current session user.

        There is no synchronous dataloader idiom in this repo. Keep role refs on
        the singleton ``currentUser`` path instead of exposing an N+1 admin-list
        field that can reveal another user's roles.
        """

        return sorted(str(role) for role in rebac_roles_of(cast(Any, self)))


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

    id: relay.GlobalID
    username: str | None = strawberry.UNSET
    password: str | None = strawberry.UNSET
    email: str | None = strawberry.UNSET
    first_name: str | None = strawberry.UNSET
    last_name: str | None = strawberry.UNSET
    is_staff: bool | None = strawberry.UNSET
    is_active: bool | None = strawberry.UNSET


def _relationship_ordering(*, include_relation: bool = False) -> tuple[str, ...]:
    """Return concrete field names for deterministic relationship ordering."""

    model = active_relationship_model()
    registry_storage = any(field.name == "resource_fk" for field in model._meta.fields)
    if registry_storage:
        fields = [
            "resource_fk__resource_type",
            "resource_fk__resource_id",
            "subject_fk__resource_type",
            "subject_fk__resource_id",
        ]
    else:
        fields = ["resource_type", "resource_id", "subject_type", "subject_id"]
    if include_relation:
        fields.insert(2, "relation")
    fields.extend(["optional_subject_relation", "caveat_name", "pk"])
    return tuple(fields)


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

    return active_relationship_model().objects.all().order_by(
        *_relationship_ordering(include_relation=True),
    )


def _permission_hub_roles() -> list[IAMRoleType]:
    """Return roles visible from active role relationship rows."""

    rows = (
        active_relationship_model()
        .objects.filter(resource_type__in=_schema_role_resource_types())
        .order_by(*_relationship_ordering())[:_PERMISSION_HUB_LIST_CAP]
    )
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


def _permission_hub_grants(info: strawberry.Info) -> QuerySet[Any]:
    """Return direct user role-grant rows in stable order."""

    del info
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
                allowed_subject_types=[
                    _schema_allowed_subject_name(allowed)
                    for allowed in relation.allowed_subjects
                ],
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


def _user_display_label(subject_id: Any) -> str | None:
    """Return a user principal's display name without exposing the user object.

    Delegates to :func:`_user_principal` - the one owner of "grant subject id ->
    user" (REBAC-id-attr aware, read under ``system_context``) - and returns only
    a display string, never the guarded user object.
    """

    if not subject_id:
        return None
    try:
        user = _user_principal(str(subject_id))
    except ValueError:
        return None
    return str(user.get_full_name() or user.username)


def _user_principal(principal_id: str) -> Any:
    """Return the user addressed by a role-grant principal id."""

    resolved_id = principal_id
    try:
        global_id = relay.GlobalID.from_id(principal_id)
    except ValueError:
        pass
    else:
        if global_id.type_name == _user_graphql_type_name():
            resolved_id = global_id.node_id

    lookups: list[dict[str, str]] = []
    subject_id_attr = str(
        getattr(User._meta, "rebac_id_attr", None)
        or app_settings.REBAC_USER_ID_ATTR
    )
    lookups.append({subject_id_attr: resolved_id})
    public_lookup = getattr(User, "public_id_lookup", None)
    if callable(public_lookup):
        lookups.append(public_lookup(resolved_id))
    pk = User._meta.pk
    if pk is not None:
        lookups.append({pk.name: resolved_id})

    tried: set[tuple[tuple[str, str], ...]] = set()
    with system_context(reason="iam.graphql.permission_hub.principal"):
        for lookup in lookups:
            key = tuple(sorted(lookup.items()))
            if key in tried:
                continue
            tried.add(key)
            try:
                user = User._default_manager.filter(**lookup).first()
            except (TypeError, ValueError):
                continue
            if user is not None:
                return user
    raise ValueError(f"User principal {principal_id!r} was not found.")


def _user_graphql_type_name() -> str:
    """Return the registered GraphQL type name for console user rows."""

    return str(cast(Any, UserType).__strawberry_definition__.name)


def _user_from_global_id(user_id: relay.GlobalID) -> Any:
    """Return the user addressed by one GraphQL global id, or raise."""

    with system_context(reason="iam.graphql.user.lookup"):
        user = instance_from_public_id(User, user_id.node_id, queryset=User._default_manager.all())
    if user is None:
        raise ValueError(f"User {user_id!s} was not found.")
    return user


def _admin_delete(
    model: Any,
    node_id: str,
    *,
    reason: str,
    confirm: bool,
    before_delete: Any = None,
) -> DeletePreview:
    """Preview-then-delete one row elevated (mirrors ``crud``'s delete resolver)."""

    with system_context(reason=reason), transaction.atomic():
        instance = instance_from_public_id(model, node_id, queryset=model._default_manager.all())
        if instance is None:
            raise ValueError(f"{model._meta.object_name} {node_id!r} was not found")
        preview = DeletePreview.from_instance(instance)
        if confirm and not preview.has_blockers:
            if before_delete is not None:
                before_delete(instance)
            instance.delete()
    return preview


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

    users: OffsetPaginated[UserType] = strawberry_django.offset_paginated(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    user: UserType | None = strawberry_django.node(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )

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
            user = instance_from_public_id(User, data.id.node_id, queryset=User._default_manager.all())
            if user is None:
                raise ValueError(f"User {data.id!s} was not found")
            for field in ("username", "email", "first_name", "last_name", "is_staff", "is_active"):
                value = getattr(data, field)
                if value is not strawberry.UNSET:
                    setattr(user, field, value)
            if data.password is not strawberry.UNSET and data.password:
                user.set_password(data.password)
            user.save()
        return cast(UserType, user)

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def delete_user(self, id: relay.GlobalID, confirm: bool = False) -> DeletePreview:
        """Delete one user when unblocked."""

        return _admin_delete(User, id.node_id, reason="iam.graphql.user.delete", confirm=confirm)


@strawberry.type
class IAMPermissionHubMutation:
    """Admin mutations for tuple-backed IAM role grants."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def grant_role(self, principal_id: str, role: str) -> bool:
        """Grant a role to one user principal."""

        role_ref = _validate_role(role)
        principal = _user_principal(principal_id)
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
        principal = _user_principal(principal_id)
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
        "query": [IAMQuery, IAMConsoleQuery],
        "mutation": [
            IAMMutation,
            IAMUserMutation,
            IAMPermissionHubMutation,
        ],
        "subscription": [changes(User, field="userChanged")],
        "types": [
            UserType,
            CurrentUserType,
            IAMRoleType,
            IAMGrantType,
            IAMRelationType,
            IAMPermCondition,
            IAMPermissionType,
            IAMResourceSchemaType,
            IAMRelationshipType,
        ],
    },
}
"""GraphQL contributions installed by the IAM addon."""
