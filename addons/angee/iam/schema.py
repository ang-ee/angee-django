"""GraphQL schema contributions for Angee IAM.

Pure identity: the user projection, the password session login, and the REBAC
permission hub. The OAuth/OIDC connection substrate (clients, external accounts,
credentials, connect/disconnect) lives in ``integrate``; OIDC *login* lives in
``iam_integrate_oidc``.
"""

from __future__ import annotations

from itertools import islice
from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.models import AnonymousUser
from django.db import transaction
from django.db.models import QuerySet
from rebac import RebacQuerySet, resolve_subjects, system_context, to_object_ref, to_subject_ref
from rebac import backend as rebac_backend
from rebac.models import active_relationship_model
from rebac.resources import model_for_resource_type
from rebac.roles import (
    roles_of as rebac_roles_of,
)
from rebac.schema import Definition, Permission, Relation, Schema, render_allowed_subject
from strawberry import auto
from strawberry.scalars import JSON

from angee.base.identity import instance_from_public_id, public_subject_ref
from angee.base.models import AngeeModel
from angee.graphql.access import ActorSelfChangeReadGate
from angee.graphql.actions import authorized_permission_target
from angee.graphql.data import hasura_model_resource, hasura_pydantic_resource
from angee.graphql.deletion import DeletePreview, attach_delete_preview_metadata, delete_by_public_id
from angee.graphql.ids import PublicID
from angee.graphql.node import AngeeNode
from angee.graphql.sharing import authorized_record_access
from angee.graphql.subscriptions import changes
from angee.graphql.view_as import ViewAs
from angee.graphql.writes import write_queryset
from angee.iam.identity import user_label
from angee.iam.models import VISIBLE_PEOPLE_DEFAULT_LIMIT
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES as _ADMIN_PERMISSION_CLASSES
from angee.iam.permissions import is_platform_admin, require_platform_admin, session_user
from angee.iam.permissions import request_from_info as _request
from angee.iam.roles import (
    IAM_OVERVIEW_DEFAULT_PEEK_LIMIT as _IAM_OVERVIEW_DEFAULT_PEEK_LIMIT,
)
from angee.iam.roles import (
    IAMGrantRow,
    IAMRoleRow,
    validate_subject,
)
from angee.iam.roles import (
    grant_role as _grant_role_owner,
)
from angee.iam.roles import (
    group_bindings as _group_bindings_owner,
)
from angee.iam.roles import (
    group_members as _group_members_owner,
)
from angee.iam.roles import (
    iam_overview as _iam_overview_owner,
)
from angee.iam.roles import (
    permission_conditions as _permission_conditions_owner,
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
from angee.iam.roles import principal_access as _principal_access_owner
from angee.iam.roles import (
    relationship_rows as _relationship_rows_owner,
)
from angee.iam.roles import (
    revoke_role as _revoke_role_owner,
)

User = cast(type[Any], get_user_model())
Group = cast(type[Any], apps.get_model("iam", "Group"))


def _view_as(info: strawberry.Info) -> ViewAs | None:
    """Read the optional view-as carrier owned by the GraphQL transport."""

    return cast(ViewAs | None, getattr(_request(info), "view_as", None))


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

    @strawberry_django.field(only=["kind", "is_active", "is_staff", "is_superuser", "password"])
    def can_issue_password(self) -> bool:
        """Whether the viewer may issue this account's first password."""

        user = cast(Any, self)
        return bool(user.has_access("issue_password") and user.password_issue_error() is None)

    @strawberry_django.field
    def assignment_subject(self) -> str:
        """Public subject used by workflow and approval assignment inputs."""

        return str(public_subject_ref(to_subject_ref(cast(Any, self))))

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
    """GraphQL identity projection, including the identity's private role refs."""

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
class GroupType(AngeeNode):
    """GraphQL projection of IAM-owned principal groups."""

    name: auto
    description: auto

    @strawberry.field
    def assignment_subject(self) -> str:
        """Public member subject for assigning work to this group."""

        return str(public_subject_ref(to_subject_ref(cast(Any, self))))

    @strawberry_django.field
    def members(self) -> list[IAMGroupMemberType]:
        """Return direct group memberships, without effective-permission expansion."""

        with system_context(reason="iam.graphql.group.members"):
            return cast(list[IAMGroupMemberType], _group_members_owner(self))

    @strawberry_django.field
    def bindings(self) -> list[IAMGroupBindingType]:
        """Return tuples bound directly to this group's member subject set."""

        with system_context(reason="iam.graphql.group.bindings"):
            return cast(list[IAMGroupBindingType], _group_bindings_owner(self))


def _legacy_role_id(root: Any) -> str:
    """Return the short ID exposed by the authored legacy role binding."""

    return cast(IAMRoleRow, root).role_id


@strawberry.type
class IAMRoleType:
    """Legacy role binding over the canonical computed IAM role row."""

    id: str = strawberry.field(resolver=_legacy_role_id)
    namespace: str
    label: str
    declared: bool
    grantable: bool


@strawberry.type
class IAMGrantType:
    """Direct binding over the canonical computed IAM grant row."""

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


@strawberry.type
class IAMPrincipalRoleType:
    """A role held by the inspected principal."""

    id: str
    role: str
    role_name: str
    namespace: str
    source: str
    source_label: str
    direct: bool


@strawberry.type
class IAMPrincipalGrantType:
    """An explicit non-role binding contributing access to a principal."""

    id: str
    resource: str
    resource_type: str
    resource_id: str
    relation: str
    source: str
    source_label: str
    direct: bool
    caveat_name: str
    target_model: str | None
    target_id: str | None


@strawberry.type
class IAMPrincipalPermissionType:
    """A permission path reached by an effective role or explicit binding."""

    id: str
    resource: str
    resource_type: str
    resource_id: str
    permission: str
    source: str
    direct: bool
    caveat_name: str
    target_model: str | None
    target_id: str | None


@strawberry.type
class IAMPrincipalAccessType:
    """Administrative access projection for one user, service, or group."""

    subject: str
    roles: list[IAMPrincipalRoleType]
    grants: list[IAMPrincipalGrantType]
    permissions: list[IAMPrincipalPermissionType]


@strawberry.type
class RecordReaderType:
    """One named effective person reader; provenance is supplied by role owners."""

    subject: str
    label: str
    you: bool
    following: bool


@strawberry.type
class IAMGroupMemberType:
    """Direct group membership tuple."""

    id: str
    subject: str
    subject_type: str
    subject_id: str
    label: str
    caveat_name: str


@strawberry.type
class IAMGroupBindingType:
    """Direct tuple binding to a group member set."""

    id: str
    resource: str
    resource_type: str
    resource_id: str
    relation: str
    caveat_name: str
    target_model: str | None
    target_id: str | None


@strawberry.type
class IAMRelationType:
    """Direct binding over a relation in the installed REBAC schema."""

    name: str

    @strawberry.field
    def allowed_subject_types(self) -> list[str]:
        """Render native allowed-subject declarations for the wire boundary."""

        return [render_allowed_subject(allowed) for allowed in cast(Relation, self).allowed_subjects]


@strawberry.type
class IAMPermCondition:
    """Flattened permission expression leaf."""

    name: str


@strawberry.type
class IAMPermissionType:
    """Permission binding retaining only native schema context."""

    schema: strawberry.Private[Schema]
    resource_type: strawberry.Private[str]
    permission: strawberry.Private[Permission]

    @strawberry.field
    def name(self) -> str:
        """Return the native permission name."""

        return str(self.permission.name)

    @strawberry.field
    def conditions(self) -> list[IAMPermCondition]:
        """Return flattened source labels for the native permission expression."""

        return [
            IAMPermCondition(name=name)
            for name in _permission_conditions_owner(self.schema, self.resource_type, self.permission.name)
        ]


@strawberry.type
class IAMResourceSchemaType:
    """Resource binding retaining the native schema and definition."""

    schema: strawberry.Private[Schema]
    definition: strawberry.Private[Definition]

    @strawberry.field
    def resource_type(self) -> str:
        """Return the native resource type."""

        return str(self.definition.resource_type)

    @strawberry.field
    def relations(self) -> list[IAMRelationType]:
        """Return native relations in deterministic name order."""

        return cast(list[IAMRelationType], sorted(self.definition.relations, key=lambda item: item.name))

    @strawberry.field
    def permissions(self) -> list[IAMPermissionType]:
        """Bind native permissions with the schema context their labels require."""

        return [
            IAMPermissionType(
                schema=self.schema,
                resource_type=self.definition.resource_type,
                permission=permission,
            )
            for permission in sorted(self.definition.permissions, key=lambda item: item.name)
        ]


@strawberry.type
class IAMOverviewNamespaceType:
    """Role namespace aggregate shown by the IAM overview."""

    namespace: str
    role_count: int
    grant_count: int


@strawberry_django.type(active_relationship_model())
class RebacRelationshipType:
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


@strawberry.type
class IssuedUserPassword:
    """A newly issued credential returned only by the issue mutation."""

    password: str


def _permission_hub_roles() -> list[IAMRoleType]:
    """Return roles visible from active role relationship rows."""

    return cast(list[IAMRoleType], _permission_hub_roles_owner())


def _permission_schema() -> list[IAMResourceSchemaType]:
    """Bind native installed REBAC definitions for the IAM console."""

    schema, definitions = _permission_schema_owner()
    return [IAMResourceSchemaType(schema=schema, definition=definition) for definition in definitions]


def _principal_access(subject: str) -> IAMPrincipalAccessType:
    """Resolve the public subject boundary and project its access evidence."""

    resolved = validate_subject(subject)
    access = _principal_access_owner(resolved)
    return IAMPrincipalAccessType(
        subject=access.subject,
        roles=cast(list[IAMPrincipalRoleType], access.roles),
        grants=cast(list[IAMPrincipalGrantType], access.grants),
        permissions=cast(list[IAMPrincipalPermissionType], access.permissions),
    )


def _iam_overview(peek_limit: int) -> IAMOverviewType:
    """Return IAM dashboard facts independent of paginated list rows."""

    return cast(IAMOverviewType, _iam_overview_owner(peek_limit))


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


def _user_queryset(info: strawberry.Info) -> QuerySet[Any]:
    """Return readable people and service users for identity and access pickers."""

    return cast(QuerySet[Any], User.objects.with_actor(session_user(info)))


def _group_queryset(info: strawberry.Info) -> QuerySet[Any]:
    """Return member-visible groups through the native REBAC query owner."""

    return RebacQuerySet(model=Group).with_actor(session_user(info)).with_action("read")


def _user_for_resource_id(value: str, queryset: QuerySet[Any]) -> Any:
    """Return one user addressed by the Hasura resource id boundary."""

    instance = instance_from_public_id(User, str(value), queryset=queryset)
    if instance is None:
        raise ValueError(f"User {value!r} was not found")
    return instance


def _group_for_resource_id(value: str, queryset: QuerySet[Any]) -> Any:
    """Return one IAM group addressed by its native public id."""

    instance = instance_from_public_id(Group, str(value), queryset=queryset)
    if instance is None:
        raise ValueError(f"Group {value!r} was not found")
    return instance


class IAMUserWriteBackend:
    """Person creation and administrator updates for the Hasura user resource."""

    def create(self, info: strawberry.Info, data: dict[str, Any]) -> Any:
        """Delegate account creation to IAM's actor-gated person factory."""

        return User.objects.create_person(**data)

    def update(
        self, info: strawberry.Info, pk: str, data: dict[str, Any],
    ) -> Any:
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

        preview = delete_by_public_id(User, str(pk), confirm=True, queryset=write_queryset(User))
        preview.require_no_blockers()
        return preview.deleted_instance


class IAMGroupWriteBackend:
    """Admin write semantics for the Hasura ``groups`` resource."""

    def create(self, info: strawberry.Info, data: dict[str, Any]) -> Any:
        """Create one IAM group."""

        require_platform_admin(info)

        with transaction.atomic():
            group = Group(**data)
            group.full_clean()
            group.save()
            return group

    def update(
        self, info: strawberry.Info, pk: str, data: dict[str, Any],
    ) -> Any:
        """Patch one IAM group."""

        require_platform_admin(info)

        with transaction.atomic():
            group = _group_for_resource_id(pk, write_queryset(Group))
            for field, value in data.items():
                setattr(group, field, value)
            group.full_clean()
            group.save()
            return group

    def delete(self, info: strawberry.Info, pk: str) -> Any | None:
        """Delete one IAM group by public id."""

        require_platform_admin(info)

        preview = delete_by_public_id(Group, str(pk), confirm=True, queryset=write_queryset(Group))
        preview.require_no_blockers()
        return preview.deleted_instance


def _admin_actor(info: strawberry.Info) -> bool:
    """Return whether the request actor reaches IAM's platform-admin role."""

    return is_platform_admin(getattr(_request(info), "user", None))


def _role_rows_for(info: strawberry.Info) -> list[IAMRoleRow]:
    """Row provider gated on the same platform-admin reach the authored query had."""

    if not _admin_actor(info):
        return []
    with system_context(reason="iam.graphql.roles"):
        return _permission_hub_roles_owner()


def _grant_rows_for(info: strawberry.Info) -> list[IAMGrantRow]:
    """Row provider gated on the same platform-admin reach the authored query had."""

    if not _admin_actor(info):
        return []
    with system_context(reason="iam.graphql.grants"):
        return _permission_hub_grants_owner()


_ROLE_RESOURCE = hasura_pydantic_resource(
    IAMRoleRow,
    name="iam_roles",
    model_label="iam.Role",
    filterable=["id", "role_id", "namespace", "label", "declared", "grantable"],
    sortable=["role_id", "namespace", "label", "declared", "grantable"],
    rows=_role_rows_for,
)


_GRANT_RESOURCE = hasura_pydantic_resource(
    IAMGrantRow,
    name="iam_grants",
    model_label="iam.Grant",
    filterable=[
        "id", "subject", "subject_id", "subject_type", "subject_relation",
        "subject_label", "role", "role_name", "namespace", "caveat_name",
    ],
    sortable=["subject_label", "subject_type", "role", "role_name", "namespace", "caveat_name"],
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
    insertable=["username", "email", "first_name", "last_name"],
    updatable=["username", "password", "email", "first_name", "last_name", "is_staff", "is_active"],
    get_queryset=_user_queryset,
    write_backend=IAMUserWriteBackend(),
    id_column="sqid",
    model_label="iam.User",
    subject_field="assignment_subject",
)


@strawberry.input(name="users_insert_input", extend=True)
class UserPasswordInsertInput:
    """Optional raw credential consumed only by the person factory.

    The stored hash is required by Django; the creation credential is optional.
    Native input extension keeps those distinct without altering the model field.
    """

    password: str | None = strawberry.UNSET


_GROUP_RESOURCE = hasura_model_resource(
    GroupType,
    model=Group,
    name="groups",
    filterable=["id", "name", "description"],
    sortable=["name"],
    aggregatable=["id"],
    groupable=["name"],
    writable=["name", "description"],
    get_queryset=_group_queryset,
    write_backend=IAMGroupWriteBackend(),
    id_column="sqid",
    model_label="iam.Group",
    subject_field="assignment_subject",
)


# Filter/sort only on columns the active relationship store materializes as
# direct concrete fields. The denormalized ``resource_type``/``subject_type``
# strings live behind ``resource_fk``/``subject_fk`` in registry storage mode, so
# they are not ORM-addressable single-field columns; ``relation`` and the caveat/
# subject-relation columns are concrete in both storage modes.
_RELATIONSHIP_FILTER_FIELDS = ("relation", "optional_subject_relation", "caveat_name")

_REBAC_RELATIONSHIP_RESOURCE = hasura_model_resource(
    RebacRelationshipType,
    model=active_relationship_model(),
    name="rebac_relationships",
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
    public_id_field="id",
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
        """Return the viewed identity, or the authenticated session identity."""

        request = _request(info)
        view_as = _view_as(info)
        user = view_as.target if view_as is not None else getattr(request, "user", None)
        if isinstance(user, AnonymousUser) or not getattr(
            user,
            "is_authenticated",
            False,
        ):
            return None
        return cast(CurrentUserType, user)


@strawberry.type
class IAMConsoleQuery:
    """Session identity reads and admin permission-hub queries."""

    @strawberry.field
    def record_readers(
        self, info: strawberry.Info, target_type: str, target_id: PublicID,
    ) -> list[RecordReaderType]:
        """List named effective person readers through the REBAC reverse index.

        The reverse index gives identities but no permission-path provenance.
        Anonymous, wildcard, and authenticated audiences have no finite person
        roster here. This surface never claims to enumerate those audiences.
        Direct relation rows come from sharing's ``record_access`` query;
        role owners contribute their derived labels separately.
        """

        model = model_for_resource_type(target_type)
        if model is None or not issubclass(model, AngeeModel):
            raise ValueError("Unknown record target type.")
        target = (authorized_record_access(info, model, target_id)[0]
                  if model.get_rebac_grantable()
                  else authorized_permission_target(info, model, target_id, "read"))
        refs = tuple(islice(rebac_backend().lookup_subjects(
            resource=to_object_ref(target), action="read", subject_type="auth/user",
        ), 100))
        resolved = resolve_subjects(refs)
        viewer = to_subject_ref(session_user(info))
        following = set()
        if apps.is_installed("angee.messaging"):
            follower_model = apps.get_model("messaging", "ThreadFollower")
            following = set(follower_model.objects.subjects_for_record(target))
        return [
            RecordReaderType(
                subject=str(public_subject_ref(ref)),
                label=user_label(resolved[ref]),
                you=ref == viewer,
                following=ref in following,
            )
            for ref in refs if ref in resolved and getattr(resolved[ref], "kind", None) == "person"
        ]

    @strawberry.field
    def real_user(self, info: strawberry.Info) -> CurrentUserType | None:
        """Return the real identity during preview, and null otherwise."""

        view_as = _view_as(info)
        return cast(CurrentUserType, view_as.real_user) if view_as is not None else None

    @strawberry.field
    def colleagues(
        self,
        info: strawberry.Info,
        search: str = "",
        limit: int = VISIBLE_PEOPLE_DEFAULT_LIMIT,
    ) -> list[UserType]:
        """Return the signed-in actor's visible people for member pickers.

        REBAC read arms authorize rows; the User collection owns active-human
        filtering, ordering, search, and limits. Access pickers use the ``users``
        resource, which also includes readable service users.
        """

        return cast(list[UserType], User.objects.visible_people(session_user(info), search=search, limit=limit))

    @strawberry.field
    def viewable_people(
        self,
        info: strawberry.Info,
        search: str = "",
        limit: int = VISIBLE_PEOPLE_DEFAULT_LIMIT,
    ) -> list[UserType]:
        """List permitted preview targets for the real actor, even during preview."""

        view_as = _view_as(info)
        actor = view_as.real_user if view_as is not None else session_user(info)
        return cast(list[UserType], User.objects.viewable_people(actor, search=search, limit=limit))

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def roles(self) -> list[IAMRoleType]:
        """Return schema-declared and retained tuple-only roles."""

        return _permission_hub_roles()

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def rebac_schema(self) -> list[IAMResourceSchemaType]:
        """Return the installed REBAC schema projection."""

        return _permission_schema()

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def iam_principal_access(self, subject: str) -> IAMPrincipalAccessType:
        """Return roles, grants, and permission paths for one principal."""

        with system_context(reason="iam.graphql.principal_access"):
            return _principal_access(subject)

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def iam_overview(
        self,
        info: strawberry.Info,
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
class IAMUserPasswordMutation:
    """Account access actions gated by the target user's zed permissions."""

    @strawberry.mutation
    def issue_user_password(self, info: strawberry.Info, id: PublicID) -> IssuedUserPassword:
        """Return a credential once after the account owner's checks succeed."""

        user = authorized_permission_target(info, User, id, "issue_password")
        return IssuedUserPassword(password=user.issue_password())


@strawberry.type
class IAMUserDeletePreviewMutation:
    """Authored cascade delete preview for users."""

    @strawberry.mutation(name="delete_user")
    def delete_user(self, info: strawberry.Info, id: PublicID, confirm: bool = False) -> DeletePreview:
        """Preview or confirm deletion of one user by public id."""

        require_platform_admin(info)
        return delete_by_public_id(User, str(id), confirm=confirm, queryset=write_queryset(User))


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
    def grant_role(
        self,
        subject: str,
        role: str,
        caveat_name: str = "",
        caveat_context: JSON | None = None,
    ) -> bool:
        """Grant a declared role to one concrete IAM subject."""

        with system_context(reason="iam.graphql.permission_hub.grant_role"):
            _grant_role_owner(
                subject=subject,
                role=role,
                caveat_name=caveat_name,
                caveat_context=cast(dict[str, Any] | None, caveat_context),
            )
            return True

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def revoke_role(self, subject: str, role: str, caveat_name: str = "") -> bool:
        """Revoke the selected caveated or uncaveated role tuple."""

        with system_context(reason="iam.graphql.permission_hub.revoke_role"):
            return _revoke_role_owner(subject=subject, role=role, caveat_name=caveat_name)


@strawberry.type
class IAMGroupMembershipMutation:
    """Membership mutations authorized by the target group's write permission."""

    @strawberry.mutation
    def add_group_member(
        self,
        info: strawberry.Info,
        group_id: PublicID,
        subject: str,
        caveat_name: str = "",
        caveat_context: JSON | None = None,
    ) -> bool:
        """Add one existing canonical user subject to an IAM group."""

        group = _group_for_resource_id(str(group_id), _group_queryset(info).with_action("write"))
        with transaction.atomic():
            group.add_member(
                subject,
                caveat_name=caveat_name,
                caveat_context=cast(dict[str, Any] | None, caveat_context),
            )
        return True

    @strawberry.mutation
    def remove_group_member(
        self,
        info: strawberry.Info,
        group_id: PublicID,
        subject: str,
        caveat_name: str = "",
    ) -> bool:
        """Remove the exact membership tuple, including a stale user subject."""

        group = _group_for_resource_id(str(group_id), _group_queryset(info).with_action("write"))
        with transaction.atomic():
            return group.remove_member(subject, caveat_name=caveat_name)


schemas = {
    "public": {
        "query": [IAMQuery],
        "mutation": [IAMMutation],
        "subscription": [
            changes(
                User,
                field="userChanged",
                read_gate=ActorSelfChangeReadGate,
            )
        ],
        "types": [
            UserType,
            CurrentUserType,
        ],
    },
    "console": {
        "input_extensions": [UserPasswordInsertInput],
        "query": [
            IAMQuery,
            IAMConsoleQuery,
            _USER_RESOURCE.query,
            _GROUP_RESOURCE.query,
            _ROLE_RESOURCE.query,
            _GRANT_RESOURCE.query,
            _REBAC_RELATIONSHIP_RESOURCE.query,
        ],
        "mutation": [
            IAMMutation,
            _USER_RESOURCE.mutation,
            _GROUP_RESOURCE.mutation,
            IAMUserDeletePreviewMutation,
            IAMUserPasswordMutation,
            IAMPermissionHubMutation,
            IAMGroupMembershipMutation,
        ],
        "subscription": [changes(User, field="userChanged")],
        "types": [
            UserType,
            CurrentUserType,
            GroupType,
            IAMRoleType,
            IAMGrantType,
            IAMPrincipalRoleType,
            IAMPrincipalGrantType,
            IAMPrincipalPermissionType,
            IAMPrincipalAccessType,
            RecordReaderType,
            IAMGroupMemberType,
            IAMGroupBindingType,
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
            *_REBAC_RELATIONSHIP_RESOURCE.types,
        ],
    },
}
"""GraphQL contributions installed by the IAM addon."""
