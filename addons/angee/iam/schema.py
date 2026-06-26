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
from pydantic import BaseModel
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

from angee.base.models import SqidPublicIdentity, instance_from_public_id
from angee.graphql.data import aggregate_queryset, hasura_model_resource, hasura_pydantic_resource
from angee.graphql.deletion import DeletePreview, attach_delete_preview_metadata
from angee.graphql.ids import PublicID
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
from angee.graphql.writes import write_queryset
from angee.iam.identity import user_display_label as _user_display_label
from angee.iam.identity import user_label, user_principal
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES as _ADMIN_PERMISSION_CLASSES
from angee.iam.permissions import is_platform_admin, require_platform_admin
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
GROUP_PUBLIC_IDENTITY = SqidPublicIdentity(prefix="grp_")
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
    def principal_ref(self) -> str:
        """Return the canonical ``<type>:<id>`` principal ref."""

        row = cast(Any, self)
        return f"{row.subject_type}:{row.subject_id}"

    @strawberry_django.field
    def role(self) -> str:
        """Return the canonical granted role ref."""

        row = cast(Any, self)
        return _role_ref(str(row.resource_type), str(row.resource_id))

    @strawberry_django.field
    def role_name(self) -> str:
        """Return the short granted role id."""

        return str(cast(Any, self).resource_id)

    @strawberry_django.field
    def namespace(self) -> str:
        """Return the namespace portion of the granted role type."""

        return _role_namespace(str(cast(Any, self).resource_type))


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


def _admin_relationship_queryset(info: strawberry.Info) -> QuerySet[Any]:
    """Return active REBAC relationship rows scoped to platform admins.

    The Hasura ``relationships`` resource replaces the authored ``relationships``
    query; like the other permission-hub surfaces it is admin-only, so a
    non-admin actor reads the empty set (``.none()``) rather than a forbidden
    error — admin-only navigation already gates the console.
    """

    if not _admin_actor(info):
        return cast(QuerySet[Any], active_relationship_model().objects.none())
    return _relationship_rows()


def _user_graphql_type_name() -> str:
    """Return the registered GraphQL type name for console user rows."""

    return str(cast(Any, UserType).__strawberry_definition__.name)


def _user_resource_id_column() -> str:
    """Return the ORM column backing the public ``users`` id argument."""

    # Bare source tests can load this schema before the composer emits the
    # runtime sqid-bearing IAM user model. That fallback is a source-test model
    # boundary, not a public API compatibility layer.
    return "sqid" if _runtime_iam_models_built() else "pk"


def _admin_user_queryset(info: strawberry.Info) -> QuerySet[Any]:
    """Return the admin-scoped user queryset for console resources."""

    require_platform_admin(info)
    return cast(QuerySet[Any], User.objects.all())


def _admin_user_aggregate_queryset(info: strawberry.Info) -> QuerySet[Any]:
    """Return the user queryset safe for aggregate and grouped math."""

    return aggregate_queryset(_admin_user_queryset(info))


def _admin_group_queryset(info: strawberry.Info) -> QuerySet[Any]:
    """Return the admin-scoped Django auth-group catalogue queryset."""

    require_platform_admin(info)
    return cast(QuerySet[Any], Group.objects.all())


def _user_for_resource_id(value: str, queryset: QuerySet[Any]) -> Any:
    """Return one user addressed by the Hasura resource id boundary."""

    if _user_resource_id_column() == "sqid":
        instance = instance_from_public_id(User, str(value), queryset=queryset)
    else:
        instance = queryset.filter(pk=value).first()
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
            if password:
                user.set_password(password)
            user.save()
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
    description: str = ""


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
            description=role.description,
        )
        for role in _permission_hub_roles()
    ]


def _grant_rows() -> list[IAMGrantRow]:
    """Project direct user role-grant tuples as computed resource rows."""

    rows: list[IAMGrantRow] = []
    for row in _permission_hub_grant_rows():
        resource_type = str(row.resource_type)
        resource_id = str(row.resource_id)
        subject_type = str(row.subject_type)
        subject_id = str(row.subject_id)
        principal_ref = f"{subject_type}:{subject_id}"
        role = _role_ref(resource_type, resource_id)
        rows.append(
            IAMGrantRow(
                id=f"{principal_ref}:{role}",
                principal_id=subject_id,
                principal_type=subject_type,
                principal_ref=principal_ref,
                principal_label=_user_display_label(subject_id) or principal_ref,
                role=role,
                role_name=resource_id,
                namespace=_role_namespace(resource_type),
            )
        )
    return rows


def _admin_actor(info: strawberry.Info) -> bool:
    """Return whether the request actor reaches IAM's platform-admin role."""

    return is_platform_admin(getattr(_request(info), "user", None))


def _role_rows_for(info: strawberry.Info) -> list[IAMRoleRow]:
    """Row provider gated on the same platform-admin reach the authored query had.

    The admin gate is evaluated BEFORE entering ``system_context``: sudo bypasses
    the REBAC ``auth/user`` read scoping, so an in-sudo ``is_platform_admin``
    resolves True for any authenticated user. Gate first under the real actor,
    then sudo only the untyped tuple computation.
    """

    if not _admin_actor(info):
        return []
    with system_context(reason="iam.graphql.roles"):
        return _role_rows()


def _grant_rows_for(info: strawberry.Info) -> list[IAMGrantRow]:
    """Row provider gated on the same platform-admin reach the authored query had.

    Gate outside ``system_context`` for the same reason as
    :func:`_role_rows_for` — sudo would defeat the REBAC user-read scoping and
    grant any authenticated user admin reach.
    """

    if not _admin_actor(info):
        return []
    with system_context(reason="iam.graphql.grants"):
        return _grant_rows()


_ROLE_RESOURCE = hasura_pydantic_resource(
    IAMRoleRow,
    name="iam_roles",
    model_label="iam.Role",
    filterable=["id", "role_id", "namespace", "label", "description"],
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
    id_column=_user_resource_id_column(),
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
        peek_limit: int = _IAM_OVERVIEW_DEFAULT_PEEK_LIMIT,
    ) -> IAMOverviewType:
        """Return IAM dashboard aggregates and peek rows."""

        return _iam_overview(peek_limit)


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
class IAMUserDeletePreviewMutation:
    """Authored cascade delete preview for users."""

    @strawberry.mutation(name="delete_user")
    def delete_user(self, info: strawberry.Info, id: PublicID, confirm: bool = False) -> DeletePreview:
        """Preview or confirm deletion of one user by public id."""

        require_platform_admin(info)
        return _delete_user_preview(str(id), confirm=confirm)


IAMUserDeletePreviewMutation = attach_delete_preview_metadata(
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
            _USER_RESOURCE.query,
            _GROUP_RESOURCE.query,
            _ROLE_RESOURCE.query,
            _GRANT_RESOURCE.query,
            _RELATIONSHIP_RESOURCE.query,
        ],
        "mutation": [
            IAMMutation,
            _USER_RESOURCE.mutation,
            _GROUP_RESOURCE.mutation,
            IAMUserDeletePreviewMutation,
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
            *_USER_RESOURCE.types,
            *_GROUP_RESOURCE.types,
            *_ROLE_RESOURCE.types,
            *_GRANT_RESOURCE.types,
            *_RELATIONSHIP_RESOURCE.types,
        ],
    },
}
"""GraphQL contributions installed by the IAM addon."""
