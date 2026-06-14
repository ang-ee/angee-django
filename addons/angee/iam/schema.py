"""GraphQL schema contributions for Angee IAM."""

from __future__ import annotations

import base64
import hashlib
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
from strawberry.scalars import JSON
from strawberry_django.pagination import OffsetPaginated

from angee.base.models import instance_from_public_id
from angee.graphql.actions import ActionResult
from angee.graphql.crud import crud
from angee.graphql.deletion import DeletePreview
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
from angee.iam import identity
from angee.iam.credentials import CredentialKind, handler_for
from angee.iam.oidc import client as client_module
from angee.iam.oidc import state
from angee.iam.oidc.errors import INVALID_STATE, OidcFlowError
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES as _ADMIN_PERMISSION_CLASSES
from angee.iam.permissions import is_authenticated as _is_authenticated
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
OAuthClient = _iam_model("OAuthClient")
ExternalAccount = _iam_model("ExternalAccount")
Credential = _iam_model("Credential")

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


@strawberry_django.type(OAuthClient)
class OAuthClientType(AngeeNode):
    """Admin GraphQL projection of an IAM OAuth client registration."""

    display_name: auto
    slug: auto
    icon: auto
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

        return cast(list[str], cast(Any, self).default_scope_values)

    @strawberry_django.field(only=["scopes_catalogue"])
    def scopes_catalogue(self) -> list[str]:
        """Return the advertised OAuth scopes."""

        return cast(list[str], cast(Any, self).scopes_catalogue_values)

    @strawberry_django.field(only=["allowed_email_domains"])
    def allowed_email_domains(self) -> list[str]:
        """Return the login domain allow-list."""

        return cast(list[str], cast(Any, self).allowed_email_domain_values)

    @strawberry_django.field(only=["client_secret"])
    def client_secret(self) -> str:
        """Return the decrypted client secret for the admin console."""

        return str(cast(Any, self).client_secret or "")

    @strawberry_django.field
    def configuration_state(self) -> str:
        """Return this OAuth client's operator-facing configuration readiness."""

        return str(cast(Any, self).configuration_state)


@strawberry.type
class CredentialOAuthClientType:
    """Public-safe OAuth client projection for credential health rows."""

    @strawberry.field
    def display_name(self) -> str:
        """Return the configured OAuth client display name."""

        return str(cast(Any, self).display_name)


@strawberry_django.type(ExternalAccount)
class ExternalAccountType(AngeeNode):
    """GraphQL projection of a linked external identity."""

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

    @strawberry_django.field
    def provider_slug(self) -> str:
        """Return the originating OAuth client's slug (the provider key)."""

        return str(getattr(cast(Any, self).oauth_client, "slug", "") or "")

    @strawberry_django.field
    def provider_environment(self) -> str:
        """Return the originating OAuth client's environment.

        ``(slug, environment)`` is the OAuth client's unique key, so the console
        can resolve an account back to its exact client without ambiguity.
        """

        return str(getattr(cast(Any, self).oauth_client, "environment", "") or "")

    @strawberry_django.field
    def provider_label(self) -> str:
        """Return the originating OAuth client's display label."""

        return str(getattr(cast(Any, self).oauth_client, "display_name", "") or "")

    @strawberry_django.field
    def provider_icon(self) -> str:
        """Return the originating OAuth client's branding icon."""

        return str(getattr(cast(Any, self).oauth_client, "icon", "") or "")


@strawberry_django.type(Credential)
class CredentialType(AngeeNode):
    """GraphQL projection of credential health without secret values."""

    kind: auto
    name: auto
    status: auto
    expires_at: auto
    last_refresh_at: auto
    last_refresh_status: auto
    external_account: ExternalAccountType | None
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["oauth_client"])
    def oauth_client(self) -> CredentialOAuthClientType | None:
        """Return a public-safe projection of the OAuth client (``None`` for local kinds)."""

        return cast("CredentialOAuthClientType | None", cast(Any, self).oauth_client)

    @strawberry_django.field(only=["oauth_client", "external_account", "name"])
    def display_name(self) -> str:
        """Return a human label for relation pickers.

        Credential has no natural string column; OAuth credentials read as
        ``provider: subject``, while provider-less ones read as their ``name``.
        ``only`` lists the FK *id* columns, but this dereferences the related rows
        — list resolvers must feed `console_credentials()`/`connected_for()`, which
        `rebac_select_related("oauth_client", "external_account")` to avoid an N+1.
        """

        client = getattr(cast(Any, self), "oauth_client", None)
        if client is not None:
            provider = str(getattr(client, "slug", "") or getattr(client, "display_name", "") or "credential")
            account = getattr(cast(Any, self), "external_account", None)
            subject = str(getattr(account, "external_id", "") or "") if account else ""
            return f"{provider}: {subject}" if subject else provider
        return str(cast(Any, self).name or "credential")


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
class AvailableConnection:
    """Picker-safe OAuth client fields for public connection selection.

    The OAuth client is self-describing (``slug``/``display_name``/``icon`` are
    its own columns), so the picker reads them straight off each row — one query
    for the whole page, no per-row fetch and no catalogue join.
    """

    @strawberry.field
    def oauth_client_sqid(self) -> strawberry.ID:
        """Return the OAuth client sqid accepted by connection mutations."""

        return strawberry.ID(str(cast(Any, self).sqid))

    @strawberry.field
    def oauth_client_display_name(self) -> str:
        """Return the OAuth client display label."""

        return str(cast(Any, self).display_name)

    @strawberry.field
    def oauth_client_slug(self) -> str:
        """Return the OAuth client slug (the provider key)."""

        return str(cast(Any, self).slug)

    @strawberry.field
    def oauth_client_icon(self) -> str:
        """Return the OAuth client branding icon."""

        return str(cast(Any, self).icon)

    @strawberry.field
    def is_oidc(self) -> bool:
        """Return whether this connection can run OIDC login/link flows."""

        return bool(cast(Any, self).is_oidc)


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
class OAuthClientInput:
    """Admin-write fields accepted when creating an OAuth client."""

    slug: str
    display_name: str
    client_id: str
    icon: str = ""
    client_secret: str = ""
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
    """Admin-write fields accepted when updating an OAuth client."""

    id: relay.GlobalID
    slug: str | None = strawberry.UNSET
    icon: str | None = strawberry.UNSET
    display_name: str | None = strawberry.UNSET
    client_id: str | None = strawberry.UNSET
    client_secret: str | None = strawberry.UNSET
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


@strawberry.input
class ExternalAccountInput:
    """Fields accepted when manually linking an external account."""

    oauth_client: relay.GlobalID
    external_id: str
    owner: str | None = None
    email: str = ""
    display_name: str = ""
    avatar_url: str = ""
    status: str = "active"


@strawberry.input
class ExternalAccountPatch:
    """Admin-write fields accepted when updating an external account (scalars only)."""

    id: relay.GlobalID
    email: str | None = strawberry.UNSET
    display_name: str | None = strawberry.UNSET
    avatar_url: str | None = strawberry.UNSET
    status: str | None = strawberry.UNSET


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


@strawberry.input
class CredentialInput:
    """Admin-write fields for a provider-less credential (OAuth ones arrive via login).

    ``kind`` discriminates the material: ``static_token`` reads ``api_key``,
    ``ssh_key`` reads ``private_key``. ``user`` defaults to the calling admin.
    """

    name: str
    kind: str
    user: relay.GlobalID | None = None
    api_key: str = ""
    private_key: str = ""


@strawberry.input
class CredentialPatch:
    """Admin-write fields accepted when updating a credential."""

    id: relay.GlobalID
    status: str | None = strawberry.UNSET


def _available_connections(
    info: strawberry.Info,
) -> QuerySet[Any]:
    """Return enabled and configured OIDC clients for the public connection picker."""

    del info
    return cast(QuerySet[Any], cast(Any, OAuthClient.objects).available_connections())


def _my_connected_accounts(
    info: strawberry.Info,
) -> QuerySet[Any]:
    """Return this session user's credential-backed connected accounts."""

    return cast(QuerySet[Any], cast(Any, Credential.objects).connected_for(_session_user(info)))


def _console_oauth_clients(
    info: strawberry.Info,
) -> QuerySet[Any]:
    """Return admin-visible OAuth clients (self-describing; no vendor join)."""

    del info
    return cast(QuerySet[Any], cast(Any, OAuthClient.objects).console_oauth_clients())


def _console_external_accounts(
    info: strawberry.Info,
) -> QuerySet[Any]:
    """Return admin-visible external accounts with guarded FK joins."""

    del info
    return cast(QuerySet[Any], cast(Any, ExternalAccount.objects).console_external_accounts())


def _console_credentials(
    info: strawberry.Info,
) -> QuerySet[Any]:
    """Return admin-visible credential health with guarded FK joins."""

    del info
    return cast(QuerySet[Any], cast(Any, Credential.objects).console_credentials())


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


def _oauth_client_from_id(oauth_client_id: relay.GlobalID) -> Any:
    """Return the OAuth client addressed by one GraphQL global id."""

    with system_context(reason="iam.graphql.external_account.oauth_client"):
        oauth_client = instance_from_public_id(
            OAuthClient,
            oauth_client_id.node_id,
            queryset=OAuthClient._default_manager.all(),
        )
    if oauth_client is None:
        raise ValueError(f"OAuth client {oauth_client_id!s} was not found.")
    return oauth_client


def _user_graphql_type_name() -> str:
    """Return the registered GraphQL type name for console user rows."""

    return str(cast(Any, UserType).__strawberry_definition__.name)


def _session_backend(user: Any) -> str:
    """Return the Django auth backend path to store in the login session.

    Django requires an explicit backend when a user did not come from
    ``authenticate()`` and multiple backends are installed. The backend string is
    stored in the session; prefer the non-REBAC backend for normal session auth,
    matching the P1 OIDC flow.
    """

    bound = getattr(user, "backend", None)
    if bound:
        return str(bound)
    for path in getattr(settings, "AUTHENTICATION_BACKENDS", ()):
        if "rebac" not in path.lower():
            return str(path)
    return "django.contrib.auth.backends.ModelBackend"


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
    user: UserType | None = strawberry_django.node(
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
    external_account: ExternalAccountType | None = strawberry_django.node(
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    credential_health: OffsetPaginated[CredentialType] = strawberry_django.offset_paginated(
        resolver=_console_credentials,
        permission_classes=_ADMIN_PERMISSION_CLASSES,
    )
    credential: CredentialType | None = strawberry_django.node(
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
                auth_login(
                    request,
                    result.user,
                    backend=_session_backend(result.user),
                )
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
            if credential.kind == CredentialKind.OAUTH and cast(Any, Credential.objects).is_only_oidc_sign_in(user):
                raise OidcFlowError("only_sign_in_method", 409)
            _revoke_remote_oauth_token(credential)
            external_account = credential.external_account
            with system_context(reason="iam.graphql.unlink_account"), transaction.atomic():
                ExternalAccount.objects.revoke_owner(external_account, user)
                deleted, _details = (
                    Credential.objects.filter(pk=credential.pk)
                    .with_action("delete")
                    .delete()
                )
            return UnlinkAccountResult(ok=deleted > 0)
        except OidcFlowError as error:
            return UnlinkAccountResult(ok=False, error=str(error), error_code=error.code)


def _revoke_remote_oauth_token(credential: Any) -> None:
    """Best-effort remote revocation before removing a local OAuth credential."""

    try:
        oauth_client = credential.oauth_client
        # Provider-less (static/ssh) credentials have nothing to revoke remotely.
        if oauth_client is None or not getattr(oauth_client, "revoke_endpoint", ""):
            return
        token = str(credential.reveal().get("access_token") or "")
        if token:
            client_module.revoke_token(oauth_client, token)
    except Exception:
        return


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


_OAUTH_CLIENT_MUTATION = crud(
    OAuthClientType,
    create=OAuthClientInput,
    update=OAuthClientPatch,
    delete=True,
    permission_classes=_ADMIN_PERMISSION_CLASSES,
    name="oauth_client",
    write_context="iam.graphql.oauth_client",
)
"""Admin OAuth-client CRUD: const-admin gated by ``PlatformAdminPermission``, written elevated."""


@strawberry.type
class IAMExternalAccountMutation:
    """Admin mutations for manually linked external identities."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def create_external_account(self, data: ExternalAccountInput) -> ExternalAccountType:
        """Create or update one external account via the account manager owner."""

        oauth_client = _oauth_client_from_id(data.oauth_client)
        owner = _user_principal(data.owner) if data.owner is not None else None
        account = ExternalAccount.objects.link(
            oauth_client,
            data.external_id,
            owner=owner,
            email=data.email,
            display_name=data.display_name,
            avatar_url=data.avatar_url,
            status=data.status,
        )
        return cast(ExternalAccountType, account)

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def update_external_account(self, data: ExternalAccountPatch) -> ExternalAccountType:
        """Update one external account's scalar identity fields."""

        with system_context(reason="iam.graphql.external_account.update"), transaction.atomic():
            account = instance_from_public_id(
                ExternalAccount, data.id.node_id, queryset=ExternalAccount._default_manager.all()
            )
            if account is None:
                raise ValueError(f"External account {data.id!s} was not found")
            for field in ("email", "display_name", "avatar_url", "status"):
                value = getattr(data, field)
                if value is not strawberry.UNSET:
                    setattr(account, field, value)
            account.save()
        return cast(ExternalAccountType, account)

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def delete_external_account(self, id: relay.GlobalID, confirm: bool = False) -> DeletePreview:
        """Revoke the owner grant, then delete the account (owner is a REBAC tuple)."""

        def revoke(account: Any) -> None:
            owner = ExternalAccount.objects.owner_for(account)
            if owner is not None:
                ExternalAccount.objects.revoke_owner(account, owner)

        return _admin_delete(
            ExternalAccount,
            id.node_id,
            reason="iam.graphql.external_account.delete",
            confirm=confirm,
            before_delete=revoke,
        )


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


def _credential_material(data: CredentialInput) -> dict[str, str]:
    """Read the secret the kind's handler names out of the discriminated input.

    The kind→secret mapping is owned by the handler (`material_field`); a kind whose
    secret field the create input does not carry (e.g. ``oauth``) is not creatable.
    """

    field = handler_for(data.kind).material_field
    if not hasattr(data, field):
        raise ValueError(f"Cannot create a credential of kind {data.kind!r}.")
    return {field: getattr(data, field)}


@strawberry.type
class IAMCredentialMutation:
    """Admin CRUD for credentials; create mints provider-less kinds (OAuth arrives via login)."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def create_credential(self, info: strawberry.Info, data: CredentialInput) -> CredentialType:
        """Create one provider-less credential, dispatching material by ``kind``."""

        user = _session_user(info) if data.user is None else _user_from_global_id(data.user)
        credential = Credential.objects.create_local_credential(
            user,
            kind=data.kind,
            name=data.name,
            material=_credential_material(data),
        )
        return cast(CredentialType, credential)

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def update_credential(self, data: CredentialPatch) -> CredentialType:
        """Update one credential's status."""

        with system_context(reason="iam.graphql.credential.update"), transaction.atomic():
            credential = instance_from_public_id(
                Credential, data.id.node_id, queryset=Credential._default_manager.all()
            )
            if credential is None:
                raise ValueError(f"Credential {data.id!s} was not found")
            if data.status is not strawberry.UNSET and data.status is not None:
                credential.status = data.status
                credential.save(update_fields=["status", "updated_at"])
        return cast(CredentialType, credential)

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def delete_credential(self, id: relay.GlobalID, confirm: bool = False) -> DeletePreview:
        """Best-effort remote revoke, then delete the credential when unblocked."""

        return _admin_delete(
            Credential,
            id.node_id,
            reason="iam.graphql.credential.delete",
            confirm=confirm,
            before_delete=_revoke_remote_oauth_token,
        )


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


@strawberry.type
class OAuthClientActionMutation:
    """Operational actions on an OAuth/OIDC login provider."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def discover_oidc_endpoints(self, id: relay.GlobalID) -> ActionResult:
        """Fetch the provider's OIDC discovery document and fill blank endpoints."""

        oauth_client = _oauth_client_from_id(id)
        if not str(getattr(oauth_client, "discovery_url", "") or ""):
            return ActionResult(ok=False, message="Set a discovery URL first.")
        with system_context(reason="iam.graphql.discover_oidc_endpoints"):
            try:
                discovery = client_module.fetch_discovery(oauth_client)
            except Exception as error:  # noqa: BLE001 — surface discovery failure to the operator
                return ActionResult(ok=False, message=f"Discovery failed: {error}")
            oauth_client.save()
        issuer = discovery.get("issuer") if isinstance(discovery, dict) else None
        return ActionResult(ok=True, message=f"Discovered endpoints for {issuer or 'provider'}.")


def _session_user(info: strawberry.Info) -> Any:
    """Return the authenticated session user or raise a REBAC denial."""

    user = getattr(_request(info), "user", None)
    if not _is_authenticated(user):
        raise PermissionDenied("Authentication required.")
    return user


def _enabled_oidc_oauth_client(oauth_client_sqid: str) -> Any:
    """Return one enabled OIDC OAuth client addressed by sqid, or raise."""

    oauth_client = (
        OAuthClient.objects.system_context(reason="iam.graphql.oidc_oauth_client")
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
        scopes=oauth_client.default_scope_values,
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


schemas = {
    "public": {
        "query": [IAMQuery, IAMConnectionsQuery],
        "mutation": [IAMMutation],
        "types": [
            UserType,
            CurrentUserType,
            CredentialOAuthClientType,
            ExternalAccountType,
            CredentialType,
            AvailableConnection,
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
            _OAUTH_CLIENT_MUTATION,
            IAMUserMutation,
            IAMExternalAccountMutation,
            IAMCredentialMutation,
            IAMPermissionHubMutation,
            OAuthClientActionMutation,
        ],
        "subscription": [changes(User, field="userChanged")],
        "types": [
            UserType,
            CurrentUserType,
            OAuthClientType,
            CredentialOAuthClientType,
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
            OidcStartPayload,
            LoginCompletePayload,
            LinkAccountResult,
            UnlinkAccountResult,
        ],
    },
}
"""GraphQL contributions installed by the IAM addon."""
