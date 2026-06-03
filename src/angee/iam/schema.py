"""GraphQL schema contributions for Angee IAM."""

from __future__ import annotations

import base64
import hashlib
from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.models import AnonymousUser
from django.db import transaction
from django.db.models import F, QuerySet
from django.http import HttpRequest
from django.utils.http import url_has_allowed_host_and_scheme
from rebac import (
    ObjectRef,
    PermissionDenied,
    app_settings,
    system_context,
)
from rebac import (
    backend as rebac_backend,
)
from rebac.managers import RebacManager
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
from strawberry.permission import BasePermission
from strawberry.scalars import JSON

from angee.base.deletion import DeletionPreview
from angee.base.graphql import AngeeNode, OffsetPaginated
from angee.base.graphql.crud import DeletePreview
from angee.base.relations import revoke_owner
from angee.iam import identity
from angee.iam.credentials import CredentialKind
from angee.iam.oidc import client as client_module
from angee.iam.oidc import state
from angee.iam.oidc.errors import INVALID_STATE, OidcFlowError

try:
    User = apps.get_model("iam", "User")
except LookupError:  # pragma: no cover - source-addon unit tests may not build runtime models.
    User = get_user_model()
Vendor = apps.get_model("iam", "Vendor")
OAuthClient = apps.get_model("iam", "OAuthClient")
ExternalAccount = apps.get_model("iam", "ExternalAccount")
Credential = apps.get_model("iam", "Credential")

_OIDC_SESSION_OAUTH_CLIENT_PREFIX = "angee.iam.oidc.oauth_client:"
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


@strawberry_django.type(Vendor)
class VendorType(AngeeNode):
    """GraphQL projection of an IAM vendor catalogue row."""

    slug: auto
    display_name: auto
    website_url: auto
    icon: auto
    description: auto
    created_at: auto
    updated_at: auto


@strawberry_django.type(OAuthClient)
class OAuthClientType(AngeeNode):
    """Admin GraphQL projection of non-secret IAM OAuth client registration."""

    display_name: auto
    vendor: VendorType
    environment: auto
    client_id: auto
    issuer: auto
    authorize_endpoint: auto
    token_endpoint: auto
    revoke_endpoint: auto
    userinfo_endpoint: auto
    jwks_uri: auto
    discovery_url: auto
    is_oidc: auto
    is_enabled: auto
    supports_refresh: auto
    refresh_rotates: auto
    supports_pkce: auto
    max_refresh_age_seconds: auto
    link_on_email_match: auto
    create_on_login: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["default_scopes"])
    def default_scopes(self) -> list[str]:
        """Return the configured default OAuth scopes."""

        return _string_list(cast(Any, self).default_scopes)

    @strawberry_django.field(only=["scopes_catalogue"])
    def scopes_catalogue(self) -> list[str]:
        """Return the advertised OAuth scopes."""

        return _string_list(cast(Any, self).scopes_catalogue)

    @strawberry_django.field(only=["allowed_email_domains"])
    def allowed_email_domains(self) -> list[str]:
        """Return the login domain allow-list."""

        return _string_list(cast(Any, self).allowed_email_domains)

    @strawberry_django.field
    def configuration_state(self) -> str:
        """Return this OAuth client's operator-facing configuration readiness."""

        return str(cast(Any, self).configuration_state)

    @strawberry_django.field
    def vendor_label(self) -> str:
        """Return the linked vendor display label."""

        return str(cast(Any, self).vendor_label)

    @strawberry_django.field
    def vendor_slug(self) -> str:
        """Return the linked vendor slug."""

        return str(cast(Any, self).vendor_slug)


@strawberry_django.type(ExternalAccount)
class ExternalAccountType(AngeeNode):
    """GraphQL projection of a linked external identity."""

    vendor: VendorType
    external_id: auto
    email: auto
    display_name: auto
    avatar_url: auto
    status: auto
    last_used_at: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field
    def credential_status(self) -> str:
        """Return the current OAuth credential status, if this account has one."""

        return str(cast(Any, self).credential_status)


@strawberry_django.type(Credential)
class CredentialType(AngeeNode):
    """GraphQL projection of credential health without secret values."""

    kind: auto
    status: auto
    expires_at: auto
    last_refresh_at: auto
    last_refresh_status: auto
    oauth_client: OAuthClientType
    external_account: ExternalAccountType | None
    created_at: auto
    updated_at: auto


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
class AvailableConnectionVendor:
    """Picker-safe vendor fields for public connection selection."""

    slug: str
    display_name: str
    icon: str


@strawberry.type
class AvailableConnection:
    """Picker-safe OAuth client fields for public connection selection."""

    @strawberry.field
    def oauth_client_sqid(self) -> strawberry.ID:
        """Return the OAuth client sqid accepted by connection mutations."""

        return strawberry.ID(str(cast(Any, self).sqid))

    @strawberry.field
    def oauth_client_display_name(self) -> str:
        """Return the OAuth client display label."""

        return str(cast(Any, self).display_name)

    @strawberry.field
    def is_oidc(self) -> bool:
        """Return whether this connection can run OIDC login/link flows."""

        return bool(cast(Any, self).is_oidc)

    @strawberry.field
    def vendor(self) -> AvailableConnectionVendor:
        """Return the picker-safe vendor projection from the joined columns.

        ``_available_connections`` annotates the vendor columns onto each row, so
        this reads them off the same row — one query for the whole page, no
        per-row vendor fetch. A ``select_related`` join can't be used here: the
        picker is public/system-scoped, so there is no actor to check a
        materialised REBAC-bound ``Vendor`` instance against (the related-row
        guard fails closed). Annotated columns carry no such instance.
        """

        row = cast(Any, self)
        return AvailableConnectionVendor(
            slug=str(row.picker_vendor_slug),
            display_name=str(row.picker_vendor_display_name),
            icon=str(row.picker_vendor_icon),
        )


@strawberry.type
class OidcStartPayload:
    """Result returned by OIDC login/link start mutations."""

    authorize_url: str = ""
    state: str = ""
    error: str | None = None
    error_code: str | None = None


@strawberry.type
class LoginPayload:
    """Result returned by the session login mutation."""

    ok: bool
    user: UserType | None = None


@strawberry.type
class LoginCompletePayload:
    """Result returned by OIDC login completion."""

    ok: bool
    user: UserType | None = None
    intent: str = "login"
    next: str = "/"
    claims: JSON | None = None
    error: str | None = None
    error_code: str | None = None


@strawberry.type
class LinkAccountResult:
    """Result returned by OIDC account-link completion."""

    account: ExternalAccountType | None = None
    user: UserType | None = None
    intent: str = ""
    next: str = "/"
    claims: JSON | None = None
    error: str | None = None
    error_code: str | None = None


@strawberry.type
class UnlinkAccountResult:
    """Result returned by account unlink mutation."""

    ok: bool
    error: str | None = None
    error_code: str | None = None


@strawberry.input
class VendorInput:
    """Fields accepted when creating a vendor."""

    slug: str
    display_name: str
    website_url: str = ""
    icon: str = ""
    description: str = ""


@strawberry.input
class VendorPatch:
    """Fields accepted when updating a vendor."""

    id: relay.GlobalID
    slug: str | None = strawberry.UNSET
    display_name: str | None = strawberry.UNSET
    website_url: str | None = strawberry.UNSET
    icon: str | None = strawberry.UNSET
    description: str | None = strawberry.UNSET


@strawberry.input
class OAuthClientInput:
    """Non-secret fields accepted when creating an OAuth client."""

    vendor: relay.GlobalID
    display_name: str
    client_id: str
    environment: str = "prod"
    issuer: str = ""
    authorize_endpoint: str = ""
    token_endpoint: str = ""
    revoke_endpoint: str = ""
    userinfo_endpoint: str = ""
    jwks_uri: str = ""
    discovery_url: str = ""
    is_oidc: bool = False
    is_enabled: bool = True
    scopes_catalogue: list[str] = strawberry.field(default_factory=list)
    default_scopes: list[str] = strawberry.field(default_factory=list)
    supports_refresh: bool = True
    refresh_rotates: bool = False
    supports_pkce: bool = True
    max_refresh_age_seconds: int | None = None
    link_on_email_match: bool = False
    create_on_login: bool = False
    allowed_email_domains: list[str] = strawberry.field(default_factory=list)


@strawberry.input
class OAuthClientPatch:
    """Non-secret fields accepted when updating an OAuth client."""

    id: relay.GlobalID
    vendor: relay.GlobalID | None = strawberry.UNSET
    display_name: str | None = strawberry.UNSET
    client_id: str | None = strawberry.UNSET
    environment: str | None = strawberry.UNSET
    issuer: str | None = strawberry.UNSET
    authorize_endpoint: str | None = strawberry.UNSET
    token_endpoint: str | None = strawberry.UNSET
    revoke_endpoint: str | None = strawberry.UNSET
    userinfo_endpoint: str | None = strawberry.UNSET
    jwks_uri: str | None = strawberry.UNSET
    discovery_url: str | None = strawberry.UNSET
    is_oidc: bool | None = strawberry.UNSET
    is_enabled: bool | None = strawberry.UNSET
    scopes_catalogue: list[str] | None = strawberry.UNSET
    default_scopes: list[str] | None = strawberry.UNSET
    supports_refresh: bool | None = strawberry.UNSET
    refresh_rotates: bool | None = strawberry.UNSET
    supports_pkce: bool | None = strawberry.UNSET
    max_refresh_age_seconds: int | None = strawberry.UNSET
    link_on_email_match: bool | None = strawberry.UNSET
    create_on_login: bool | None = strawberry.UNSET
    allowed_email_domains: list[str] | None = strawberry.UNSET


class PlatformAdminPermission(BasePermission):
    """Allow only actors that reach IAM's const-backed platform admin role."""

    message = "Platform admin permission required."
    error_extensions = {"code": "PERMISSION_DENIED"}

    def has_permission(
        self,
        source: Any,
        info: strawberry.Info,
        **kwargs: Any,
    ) -> bool:
        """Return whether the request user has platform-admin reach."""

        del source, kwargs
        user = getattr(_request(info), "user", None)
        if not _is_authenticated(user):
            return False
        user_pk = cast(Any, user).pk
        if isinstance(User._default_manager, RebacManager):
            return cast(bool, User.objects.filter(pk=user_pk).exists())
        return bool(getattr(user, "is_superuser", False))


_ADMIN_PERMISSION_CLASSES: list[type[BasePermission]] = [PlatformAdminPermission]


def _available_connections(
    info: strawberry.Info,
) -> QuerySet[Any]:
    """Return enabled and configured OIDC clients for the public connection picker."""

    del info
    return cast(
        QuerySet[Any],
        OAuthClient.objects.system_context(reason="iam.graphql.available_connections")
        .filter(is_enabled=True, is_oidc=True)
        .exclude(client_id="")
        .exclude(discovery_url="", authorize_endpoint="")
        .annotate(
            picker_vendor_slug=F("vendor__slug"),
            picker_vendor_display_name=F("vendor__display_name"),
            picker_vendor_icon=F("vendor__icon"),
        ),
    )


def _my_connected_accounts(
    info: strawberry.Info,
) -> QuerySet[Any]:
    """Return this session user's credential-backed connected accounts."""

    return Credential.objects.connected_for(_session_user(info))


def _console_oauth_clients(
    info: strawberry.Info,
) -> QuerySet[Any]:
    """Return admin-visible OAuth clients with guarded vendor joins."""

    del info
    return cast(QuerySet[Any], OAuthClient.objects.rebac_select_related("vendor"))


def _console_external_accounts(
    info: strawberry.Info,
) -> QuerySet[Any]:
    """Return admin-visible external accounts with guarded vendor joins."""

    del info
    return cast(QuerySet[Any], ExternalAccount.objects.rebac_select_related("vendor", "credential"))


def _console_credentials(
    info: strawberry.Info,
) -> QuerySet[Any]:
    """Return admin-visible credential health with guarded FK joins."""

    del info
    return cast(
        QuerySet[Any],
        Credential.objects.rebac_select_related(
            "oauth_client",
            "oauth_client__vendor",
            "external_account",
            "external_account__vendor",
        ),
    )


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


def _role_membership_exists(row: Any) -> bool:
    """Return whether the direct role membership represented by ``row`` exists."""

    return active_relationship_model().objects.filter(
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        relation=ROLE_RELATION,
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        optional_subject_relation=row.optional_subject_relation,
        caveat_name=row.caveat_name,
    ).exists()


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
    public_lookup = getattr(User, "_public_id_lookup", None)
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
class IAMConnectionsQuery:
    """Public connection picker and self-service account queries."""

    available_connections: OffsetPaginated[AvailableConnection] = strawberry_django.offset_paginated(
        resolver=_available_connections,
    )
    my_connected_accounts: OffsetPaginated[CredentialType] = strawberry_django.offset_paginated(
        resolver=_my_connected_accounts,
    )


@strawberry.type
class IAMConsoleQuery:
    """Admin IAM connection queries."""

    users: OffsetPaginated[UserType] = strawberry_django.offset_paginated(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    vendors: OffsetPaginated[VendorType] = strawberry_django.offset_paginated(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    vendor: VendorType | None = strawberry_django.node(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    oauth_clients: OffsetPaginated[OAuthClientType] = strawberry_django.offset_paginated(
        resolver=_console_oauth_clients,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    oauth_client: OAuthClientType | None = strawberry_django.node(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    external_accounts: OffsetPaginated[ExternalAccountType] = strawberry_django.offset_paginated(
        resolver=_console_external_accounts,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    credential_health: OffsetPaginated[CredentialType] = strawberry_django.offset_paginated(
        resolver=_console_credentials,
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

    @strawberry.mutation
    def login_start(
        self,
        info: strawberry.Info,
        oauth_client_sqid: str,
        redirect_uri: str,
        next: str = "/",
    ) -> OidcStartPayload:
        """Start an OIDC login flow for an enabled login-capable OAuth client."""

        request = _request(info)
        try:
            oauth_client = _enabled_oidc_oauth_client(oauth_client_sqid)
            return _start_oidc_flow(
                request,
                oauth_client,
                redirect_uri,
                next_path=_coerce_next_path(next, request),
            )
        except OidcFlowError as error:
            return _oidc_start_error(error)

    @strawberry.mutation
    def login_complete(
        self,
        info: strawberry.Info,
        code: str,
        state: str,
        redirect_uri: str,
    ) -> LoginCompletePayload:
        """Complete an OIDC login flow and bind the user to the session."""

        request = _request(info)
        try:
            oauth_client = _oauth_client_for_remembered_state(request, state)
            result = identity.complete_login(
                oauth_client,
                code=code,
                state_token=state,
                redirect_uri=redirect_uri,
            )
            with system_context(reason="iam.oidc.login"):
                auth_login(request, result.user)
        except OidcFlowError as error:
            return LoginCompletePayload(ok=False, error=str(error), error_code=error.code)
        return LoginCompletePayload(
            ok=True,
            user=cast(UserType, result.user),
            next=result.next_path,
            claims=cast(JSON, result.claims),
        )

    @strawberry.mutation
    def link_account_start(
        self,
        info: strawberry.Info,
        oauth_client_sqid: str,
        redirect_uri: str,
        next: str = "/",
    ) -> OidcStartPayload:
        """Start an authenticated OIDC account-link flow."""

        user = _session_user(info)
        request = _request(info)
        try:
            oauth_client = _enabled_oidc_oauth_client(oauth_client_sqid)
            return _start_oidc_flow(
                request,
                oauth_client,
                redirect_uri,
                user_id=str(user.pk),
                next_path=_coerce_next_path(next, request),
                flow=state.StateFlow.LINK,
            )
        except OidcFlowError as error:
            return _oidc_start_error(error)

    @strawberry.mutation
    def link_account_complete(
        self,
        info: strawberry.Info,
        code: str,
        state: str,
        redirect_uri: str,
    ) -> LinkAccountResult:
        """Complete an authenticated OIDC account-link flow."""

        request = _request(info)
        _session_user(info)
        try:
            oauth_client = _oauth_client_for_remembered_state(request, state)
            result = identity.complete_link(
                oauth_client,
                code=code,
                state_token=state,
                redirect_uri=redirect_uri,
            )
        except OidcFlowError as error:
            return LinkAccountResult(error=str(error), error_code=error.code)
        return LinkAccountResult(
            account=cast(ExternalAccountType, result.account),
            user=cast(UserType, result.user),
            intent="link",
            next=result.next_path,
            claims=cast(JSON, result.claims),
        )

    @strawberry.mutation
    def unlink_account(
        self,
        info: strawberry.Info,
        external_account_sqid: str,
    ) -> UnlinkAccountResult:
        """Remove this session user's credential link to an external account."""

        user = _session_user(info)
        try:
            with system_context(reason="iam.graphql.unlink_account.lookup"):
                credential = (
                    Credential.objects.select_related(
                        "oauth_client",
                        "external_account",
                    )
                    .filter(
                        user=user,
                        external_account__sqid=external_account_sqid,
                    )
                    .first()
                )
            if credential is None:
                return UnlinkAccountResult(ok=False)
            if _would_remove_only_oidc_sign_in_method(user, credential):
                raise OidcFlowError("only_sign_in_method", 409)
            _revoke_remote_oauth_token(credential)
            external_account = credential.external_account
            with system_context(reason="iam.graphql.unlink_account"), transaction.atomic():
                revoke_owner(external_account, user)
                deleted, _details = (
                    Credential.objects.filter(pk=credential.pk)
                    .with_action("delete")
                    .delete()
                )
            return UnlinkAccountResult(ok=deleted > 0)
        except OidcFlowError as error:
            return UnlinkAccountResult(ok=False, error=str(error), error_code=error.code)


def _would_remove_only_oidc_sign_in_method(user: Any, credential: Any) -> bool:
    """Return whether unlinking ``credential`` would leave a passwordless user unable to sign in."""

    if user.has_usable_password() or credential.kind != CredentialKind.OAUTH:
        return False
    with system_context(reason="iam.graphql.unlink_account.guard"):
        oidc_account_count = (
            Credential.objects.filter(
                user=user,
                kind=CredentialKind.OAUTH,
                oauth_client__is_oidc=True,
                external_account__isnull=False,
            )
            .values("external_account_id")
            .distinct()
            .count()
        )
    return oidc_account_count <= 1


def _revoke_remote_oauth_token(credential: Any) -> None:
    """Best-effort remote revocation before removing a local OAuth credential."""

    try:
        oauth_client = credential.oauth_client
        if not getattr(oauth_client, "revoke_endpoint", ""):
            return
        token = str(credential.reveal().get("access_token") or "")
        if token:
            client_module.revoke_token(oauth_client, token)
    except Exception:
        return


@strawberry.type
class IAMVendorMutation:
    """Admin mutations for the vendor catalogue.

    Hand-rolled (not ``crud()``) on purpose: the writes run under
    ``system_context``, gated by :class:`PlatformAdminPermission`. ``crud()``
    relies on REBAC's per-row write-gate, but ``auth/vendor``'s const-backed
    ``create = admin->member`` is unsatisfiable for a table-backed row — the
    sqid is only computed after the insert, so the create check always sees an
    empty resource id and fails closed for everyone. The const-admin reach is
    therefore enforced at the GraphQL layer and the insert runs elevated, the
    same shape the IAM managers use (``AccountManager.link`` /
    ``CredentialManager.upsert_for_user`` create under ``system_context``).
    """

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def create_vendor(self, data: VendorInput) -> VendorType:
        """Create one vendor after the console admin gate passes."""

        with system_context(reason="iam.graphql.vendor.create"):
            return cast(VendorType, Vendor.objects.create(**_input_values(data, _VENDOR_FIELDS)))

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def update_vendor(self, data: VendorPatch) -> VendorType:
        """Update one vendor after the console admin gate passes."""

        with system_context(reason="iam.graphql.vendor.update"):
            vendor = _resolve_public_id(Vendor, data.id)
            _assign_values(vendor, _input_values(data, _VENDOR_FIELDS))
            vendor.save()
        return cast(VendorType, vendor)

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def delete_vendor(self, id: relay.GlobalID) -> DeletePreview:
        """Delete one vendor after the console admin gate passes."""

        return _delete_instance(Vendor, id, reason="iam.graphql.vendor.delete")


@strawberry.type
class IAMOAuthClientMutation:
    """Admin mutations for non-secret OAuth/OIDC client registration.

    Hand-rolled under ``system_context`` for the same reason as
    :class:`IAMVendorMutation`: the const-backed ``create`` gate cannot apply to
    a not-yet-inserted row, so the const-admin gate lives at the GraphQL layer.
    """

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def create_oauth_client(self, data: OAuthClientInput) -> OAuthClientType:
        """Create one OAuth client after the console admin gate passes."""

        with system_context(reason="iam.graphql.oauth_client.create"):
            values = _input_values(data, _OAUTH_CLIENT_FIELDS)
            values["vendor"] = _resolve_public_id(Vendor, data.vendor)
            return cast(OAuthClientType, OAuthClient.objects.create(**values))

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def update_oauth_client(self, data: OAuthClientPatch) -> OAuthClientType:
        """Update one OAuth client after the console admin gate passes."""

        with system_context(reason="iam.graphql.oauth_client.update"):
            oauth_client = _resolve_public_id(OAuthClient, data.id)
            values = _input_values(data, _OAUTH_CLIENT_FIELDS)
            if data.vendor is not strawberry.UNSET and data.vendor is not None:
                values["vendor"] = _resolve_public_id(Vendor, data.vendor)
            _assign_values(oauth_client, values)
            oauth_client.save()
        return cast(OAuthClientType, oauth_client)

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def delete_oauth_client(self, id: relay.GlobalID) -> DeletePreview:
        """Delete one OAuth client after the console admin gate passes."""

        return _delete_instance(OAuthClient, id, reason="iam.graphql.oauth_client.delete")


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
            row = rebac_grant(actor=principal, role=role_ref)
            return _role_membership_exists(row)

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


def _request(info: strawberry.Info) -> HttpRequest:
    """Return the Django request from Strawberry's context."""

    return cast(HttpRequest, info.context.request)


def _session_user(info: strawberry.Info) -> Any:
    """Return the authenticated session user or raise a REBAC denial."""

    user = getattr(_request(info), "user", None)
    if not _is_authenticated(user):
        raise PermissionDenied("Authentication required.")
    return user


def _is_authenticated(user: Any) -> bool:
    """Return whether ``user`` is a real authenticated session user."""

    return not isinstance(user, AnonymousUser) and bool(
        getattr(user, "is_authenticated", False)
    )


def _enabled_oidc_oauth_client(oauth_client_sqid: str) -> Any:
    """Return one enabled OIDC OAuth client addressed by sqid, or raise."""

    oauth_client = (
        OAuthClient.objects.system_context(reason="iam.graphql.oidc_oauth_client")
        .select_related("vendor")
        .filter(sqid=oauth_client_sqid)
        .first()
    )
    if oauth_client is None or not oauth_client.is_enabled or not oauth_client.is_oidc:
        raise ValueError("OAuth client is not enabled for OIDC.")
    return oauth_client


def _start_oidc_flow(
    request: HttpRequest,
    oauth_client: Any,
    redirect_uri: str,
    *,
    user_id: str | None = None,
    next_path: str = "/",
    flow: state.StateFlow = state.StateFlow.LOGIN,
) -> OidcStartPayload:
    """Issue state, remember its OAuth client, and return the authorize URL."""

    state_token, record = state.issue(
        oauth_client,
        redirect_uri,
        user_id=user_id,
        next_path=next_path,
        flow=flow,
    )
    _remember_flow_oauth_client(request, state_token, oauth_client)
    authorize_url = client_module.build_authorize_url(
        oauth_client,
        state=state_token,
        nonce=record.nonce,
        redirect_uri=redirect_uri,
        scopes=_string_list(oauth_client.default_scopes),
        code_challenge=_pkce_challenge(record.code_verifier),
    )
    return OidcStartPayload(authorize_url=authorize_url, state=state_token)


def _oidc_start_error(error: OidcFlowError) -> OidcStartPayload:
    """Return a typed start-flow error payload."""

    return OidcStartPayload(error=str(error), error_code=error.code)


def _coerce_next_path(value: str, request: HttpRequest) -> str:
    """Return a same-host post-flow redirect path, defaulting unsafe values to ``/``."""

    if not value:
        return "/"
    if url_has_allowed_host_and_scheme(
        value,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return value
    return "/"


def _remember_flow_oauth_client(
    request: HttpRequest,
    state_token: str,
    oauth_client: Any,
) -> None:
    """Bind one OIDC state token to an OAuth client sqid in the browser session."""

    session = cast(Any, request).session
    session[f"{_OIDC_SESSION_OAUTH_CLIENT_PREFIX}{state_token}"] = str(oauth_client.sqid)
    session.modified = True


def _oauth_client_for_remembered_state(
    request: HttpRequest,
    state_token: str,
) -> Any:
    """Return the session-bound OAuth client for one pending OIDC state token."""

    session = cast(Any, request).session
    key = f"{_OIDC_SESSION_OAUTH_CLIENT_PREFIX}{state_token}"
    oauth_client_sqid = session.pop(key, None)
    session.modified = True
    if not oauth_client_sqid:
        raise OidcFlowError(INVALID_STATE, 400)
    return _enabled_oidc_oauth_client(str(oauth_client_sqid))


def _pkce_challenge(code_verifier: str | None) -> str | None:
    """Return the S256 PKCE challenge for one verifier."""

    if not code_verifier:
        return None
    digest = hashlib.sha256(code_verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _string_list(value: object) -> list[str]:
    """Return ``value`` as a string list for JSON-backed scope fields."""

    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value]


_VENDOR_FIELDS = frozenset({"slug", "display_name", "website_url", "icon", "description"})
_OAUTH_CLIENT_FIELDS = frozenset(
    {
        "display_name",
        "environment",
        "client_id",
        "issuer",
        "authorize_endpoint",
        "token_endpoint",
        "revoke_endpoint",
        "userinfo_endpoint",
        "jwks_uri",
        "discovery_url",
        "is_oidc",
        "is_enabled",
        "scopes_catalogue",
        "default_scopes",
        "supports_refresh",
        "refresh_rotates",
        "supports_pkce",
        "max_refresh_age_seconds",
        "link_on_email_match",
        "create_on_login",
        "allowed_email_domains",
    }
)


def _input_values(data: object, fields: frozenset[str]) -> dict[str, Any]:
    """Return set Strawberry input values for one field set."""

    values: dict[str, Any] = {}
    for name in fields:
        value = getattr(data, name, strawberry.UNSET)
        if value is strawberry.UNSET:
            continue
        values[name] = value
    return values


def _assign_values(instance: Any, values: dict[str, Any]) -> None:
    """Assign values to a Django model instance."""

    for name, value in values.items():
        setattr(instance, name, value)


def _resolve_public_id(model: Any, global_id: relay.GlobalID) -> Any:
    """Resolve one Relay global ID to a model instance."""

    instance = model.from_public_id(global_id.node_id)
    if instance is None:
        raise ValueError(f"{model._meta.object_name} {global_id.node_id!r} was not found")
    return instance


def _delete_instance(model: Any, global_id: relay.GlobalID, *, reason: str) -> DeletePreview:
    """Delete one instance and return the standard deletion preview payload."""

    with system_context(reason=reason), transaction.atomic():
        instance = _resolve_public_id(model, global_id)
        preview = DeletionPreview.from_instance(instance)
        if not preview.has_blockers:
            instance.delete()
    return DeletePreview.from_domain(preview)


schemas = {
    "public": {
        "query": [IAMQuery, IAMConnectionsQuery],
        "mutation": [IAMMutation],
        "types": [
            UserType,
            CurrentUserType,
            VendorType,
            OAuthClientType,
            ExternalAccountType,
            CredentialType,
            AvailableConnection,
            AvailableConnectionVendor,
            OidcStartPayload,
            LoginCompletePayload,
            LinkAccountResult,
            UnlinkAccountResult,
        ],
    },
    "console": {
        "query": [IAMQuery, IAMConsoleQuery],
        "mutation": [
            IAMMutation,
            IAMVendorMutation,
            IAMOAuthClientMutation,
            IAMPermissionHubMutation,
        ],
        "types": [
            UserType,
            CurrentUserType,
            VendorType,
            OAuthClientType,
            ExternalAccountType,
            CredentialType,
            IAMRoleType,
            IAMGrantType,
            IAMRelationType,
            IAMPermCondition,
            IAMPermissionType,
            IAMResourceSchemaType,
            IAMRelationshipType,
            AvailableConnection,
            AvailableConnectionVendor,
            OidcStartPayload,
            LoginCompletePayload,
            LinkAccountResult,
            UnlinkAccountResult,
        ],
    },
}
"""GraphQL contributions installed by the IAM addon."""
