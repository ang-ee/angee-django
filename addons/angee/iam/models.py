"""Source models for Angee identity."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any, cast
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import UnicodeUsernameValidator
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone
from rebac import (
    RelationshipTuple,
    app_settings,
    delete_relationship,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.managers import RebacManager, RebacQuerySet
from rebac.models import active_relationship_model
from rebac.permissions_mixin import RebacPermissionsMixin
from rebac.roles import grant, revoke

from angee.base.fields import EncryptedField, SqidField, StateField
from angee.base.mixins import AuditMixin, SqidMixin
from angee.base.models import AngeeModel
from angee.iam.credentials import CredentialKind, handler_for


class AccountStatus(models.TextChoices):
    """Identity lifecycle for a linked external account.

    Pure identity health — does the account's login/credential still work. The
    integration-health rollup across capabilities lives on
    ``integrate.ConnectionStatus``, not here.
    """

    ACTIVE = "active", "Active"
    EXPIRED = "expired", "Expired"
    REVOKED = "revoked", "Revoked"


class CredentialStatus(models.TextChoices):
    """Lifecycle state for per-user credential material."""

    ACTIVE = "active", "Active"
    EXPIRED = "expired", "Expired"
    REVOKED = "revoked", "Revoked"


class UserManager(RebacManager, BaseUserManager):
    """Manager for Angee's composed user model."""

    use_in_migrations = True

    def get_by_natural_key(self, username: str) -> Any:
        """Return a user for credential checks without row-scope filtering."""

        return self.system_context(reason="iam.credentials").get(**{self.model.USERNAME_FIELD: username})

    def get(self, *args: Any, **kwargs: Any) -> Any:
        """Return a user, bypassing REBAC only for session primary keys."""

        if self._is_session_lookup(args, kwargs):
            return self.system_context(reason="iam.session").get(**kwargs)
        return super().get(*args, **kwargs)

    async def aget_by_natural_key(self, username: str) -> Any:
        """Async sibling of ``get_by_natural_key``."""

        return await self.system_context(reason="iam.credentials").aget(**{self.model.USERNAME_FIELD: username})

    def create_user(
        self,
        username: str,
        email: str | None = None,
        password: str | None = None,
        **extra_fields: Any,
    ) -> Any:
        """Create and save a regular user."""

        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(username, email, password, **extra_fields)

    def create_superuser(
        self,
        username: str,
        email: str | None = None,
        password: str | None = None,
        **extra_fields: Any,
    ) -> Any:
        """Create and save a superuser."""

        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(username, email, password, **extra_fields)

    def _create_user(
        self,
        username: str,
        email: str | None,
        password: str | None,
        **extra_fields: Any,
    ) -> Any:
        """Build, password-hash, and save one user."""

        if not username:
            raise ValueError("The given username must be set")
        user = self.model(
            username=self.model.normalize_username(username),
            email=self.normalize_email(email),
            **extra_fields,
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def _is_session_lookup(
        self,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> bool:
        """Return whether ``kwargs`` match Django's session user lookup."""

        if args or len(kwargs) != 1:
            return False
        key = next(iter(kwargs))
        pk = self.model._meta.pk
        return pk is not None and key in {"pk", pk.name, pk.attname}


class User(SqidMixin, AbstractBaseUser, RebacPermissionsMixin, AngeeModel):
    """Abstract swappable user model composed into Angee runtimes."""

    runtime = True

    username_validator = UnicodeUsernameValidator()

    sqid = SqidField(real_field_name="id", prefix="usr", min_length=8)
    username = models.CharField(
        max_length=150,
        unique=True,
        validators=(username_validator,),
    )
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True)
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    EMAIL_FIELD = "email"
    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ("email",)

    class Meta:
        """Django model options for the IAM user source."""

        abstract = True
        swappable = "AUTH_USER_MODEL"
        rebac_resource_type = "auth/user"

    def clean(self) -> None:
        """Normalize username and email before validation."""

        super().clean()
        self.email = type(self).objects.normalize_email(self.email)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the user and mirror superuser status to the admin role."""

        update_fields = kwargs.get("update_fields")
        sync_admin_role = update_fields is None or "is_superuser" in update_fields
        super().save(*args, **kwargs)
        if not sync_admin_role:
            return
        role = app_settings.REBAC_UNIVERSAL_ADMIN_ROLE
        if not role:
            return
        if self.is_superuser:
            grant(actor=self, role=role)
        else:
            revoke(actor=self, role=role)

    def get_full_name(self) -> str:
        """Return first and last name joined with a space."""

        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self) -> str:
        """Return the user's short display name."""

        return self.first_name


class ExternalAccountQuerySet(RebacQuerySet[Any]):
    """REBAC-scoped reads for external accounts."""

    def console_external_accounts(self) -> ExternalAccountQuerySet:
        """Return admin-visible external accounts with guarded FK joins."""

        return cast(ExternalAccountQuerySet, self.rebac_select_related("oauth_client", "credential"))


class ExternalAccountManager(RebacManager.from_queryset(ExternalAccountQuerySet)):  # type: ignore[misc]
    """Manager for idempotent external account linking.

    Actor-less framework writes run under ``system_context``; update paths do not maintain ``updated_by``.
    """

    caller_fields = frozenset(
        {
            "email",
            "display_name",
            "avatar_url",
            "status",
            "identity_claims",
            "last_error",
            "last_error_at",
        }
    )

    def link(
        self,
        oauth_client: Any,
        external_id: str,
        *,
        owner: Any | None = None,
        **identity: Any,
    ) -> Any:
        """Create or update one ``(oauth_client, external_id)`` external account."""

        reason = "iam.connections.link"
        update_values = _validated_manager_values(
            self.model,
            identity,
            allowed=self.caller_fields,
        )
        create_values = {
            "email": "",
            "display_name": "",
            "avatar_url": "",
            "status": AccountStatus.ACTIVE,
            "identity_claims": {},
            "last_error": "",
            "last_error_at": None,
            "last_used_at": None,
            **update_values,
        }
        with system_context(reason=reason), transaction.atomic():
            instance, created = self.update_or_create(
                oauth_client=oauth_client,
                external_id=external_id,
                defaults=update_values,
                create_defaults=create_values,
            )
            if owner is not None and (created or self.owner_for(instance) is None):
                self.grant_owner(instance, owner)
        return instance

    def grant_owner(self, account: Any, owner: Any) -> None:
        """Grant ``owner`` direct ownership of an external account."""

        write_relationships(
            [
                RelationshipTuple(
                    resource=to_object_ref(account),
                    relation="owner",
                    subject=to_subject_ref(owner),
                )
            ]
        )

    def revoke_owner(self, account: Any, owner: Any) -> None:
        """Revoke ``owner`` direct ownership of an external account."""

        delete_relationship(
            RelationshipTuple(
                resource=to_object_ref(account),
                relation="owner",
                subject=to_subject_ref(owner),
            )
        )

    def owner_for(self, account: Any) -> AbstractBaseUser | None:
        """Return the user granted owner on ``account``, if one exists."""

        resource_ref = to_object_ref(account)
        Relationship = active_relationship_model()
        with system_context(reason="iam.connections.owner"):
            row = (
                Relationship.objects.filter(
                    resource_type=resource_ref.resource_type,
                    resource_id=resource_ref.resource_id,
                    relation="owner",
                    subject_type=app_settings.REBAC_USER_TYPE,
                    optional_subject_relation="",
                )
                .order_by(self._relationship_subject_id_ordering(Relationship))
                .first()
            )
            if row is None:
                return None
            UserModel = get_user_model()
            # REBAC exposes SubjectRef creation publicly, but not inverse model lookup.
            user_id_attr = str(getattr(UserModel._meta, "rebac_id_attr", None) or app_settings.REBAC_USER_ID_ATTR)
            try:
                return UserModel.objects.get(**{user_id_attr: row.subject_id})
            except UserModel.DoesNotExist:
                return None

    def _relationship_subject_id_ordering(self, relationship_model: type[Any]) -> str:
        """Return the concrete ordering key for the active REBAC relationship store."""

        if any(field.name == "subject_fk" for field in relationship_model._meta.fields):
            return "subject_fk__resource_id"
        return "subject_id"


class ExternalAccount(SqidMixin, AuditMixin, AngeeModel):
    """A user's identity at a provider, shared by principals through REBAC grants.

    Identity only: which client minted it (``oauth_client``), which external
    subject (``external_id``), and the credential that authenticates as it. The
    integration that *runs over* a connection lives in ``integrate.Connection``,
    which owns the capability-health rollup.
    """

    runtime = True

    sqid = SqidField(real_field_name="id", prefix="eac", min_length=8)
    oauth_client = models.ForeignKey(
        "iam.OAuthClient",
        on_delete=models.PROTECT,
        related_name="external_accounts",
    )
    external_id = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    display_name = models.CharField(max_length=255, blank=True)
    avatar_url = models.URLField(blank=True)
    credential = models.ForeignKey(
        "iam.Credential",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    status = StateField(choices_enum=AccountStatus, default=AccountStatus.ACTIVE)
    identity_claims = models.JSONField(default=dict, blank=True)
    last_error = models.TextField(blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    objects = ExternalAccountManager()

    class Meta:
        """Django model options for external accounts."""

        abstract = True
        ordering = ("-updated_at",)
        rebac_resource_type = "auth/external_account"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("oauth_client", "external_id"),
                name="uniq_iam_external_account_oauth_client_external_id",
            ),
        )

    def __str__(self) -> str:
        """Return a stable provider-qualified account label."""

        client_slug = getattr(getattr(self, "oauth_client", None), "slug", "?")
        return f"{client_slug}:{self.external_id}"

    @property
    def credential_status(self) -> str:
        """Return the current OAuth credential status, if this account has one."""

        credential = getattr(self, "credential", None)
        return "" if credential is None else str(getattr(credential, "status", "") or "")

    @staticmethod
    def display_name_from_claims(claims: Mapping[str, Any], email: str) -> str:
        """Return the best display label from verified identity claims."""

        for key in ("name", "preferred_username", "given_name"):
            value = claims.get(key)
            if value:
                return str(value)
        return email


class OAuthClientQuerySet(RebacQuerySet[Any]):
    """REBAC-scoped reads for OAuth client registration."""

    def available_connections(self) -> OAuthClientQuerySet:
        """Return enabled and configured OIDC clients for the public connection picker."""

        return cast(
            OAuthClientQuerySet,
            self.system_context(reason="iam.graphql.available_connections")
            .filter(is_enabled=True, is_oidc=True)
            .exclude(client_id="")
            .exclude(discovery_url=""),
        )

    def console_oauth_clients(self) -> OAuthClientQuerySet:
        """Return admin-visible OAuth clients (self-describing; no vendor join)."""

        return cast(OAuthClientQuerySet, self)


class OAuthClientManager(RebacManager.from_queryset(OAuthClientQuerySet)):  # type: ignore[misc]
    """Manager for settings-sourced OAuth/OIDC client registration."""

    seed_fields = frozenset(
        {
            "display_name",
            "icon",
            "client_id",
            "issuer",
            "authorize_endpoint",
            "token_endpoint",
            "revoke_endpoint",
            "userinfo_endpoint",
            "jwks_uri",
            "discovery_url",
            "token_request_format",
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
            "authorize_params",
            "token_params",
            "external_id_claim",
            "email_claim",
            "display_name_claim",
            "avatar_url_claim",
            "manual_redirect_uri",
        }
    )
    setting_fields = seed_fields | frozenset({"slug", "environment", "client_secret"})
    required_setting_fields = frozenset({"slug", "display_name", "client_id"})

    def sync_from_settings(
        self,
        entries: Iterable[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]] | None = None,
    ) -> tuple[Any, ...]:
        """Create or update OAuth clients declared in ``settings.ANGEE_IAM_OAUTH_CLIENTS``.

        The host owns reading environment variables. IAM reads only Django
        settings and keeps secrets out of resource files.
        """

        synced: list[Any] = []
        with system_context(reason="iam.oauth_clients.seed"), transaction.atomic():
            for index, entry in enumerate(self._setting_entries(entries), start=1):
                synced.append(self._sync_setting_entry(index, entry))
        return tuple(synced)

    def _setting_entries(
        self,
        entries: Iterable[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]] | None,
    ) -> tuple[Mapping[str, Any], ...]:
        """Return normalized setting entries from an explicit value or settings."""

        raw_entries = getattr(settings, "ANGEE_IAM_OAUTH_CLIENTS", ()) if entries is None else entries
        if not raw_entries:
            return ()
        if isinstance(raw_entries, Mapping):
            values = tuple(raw_entries.values())
        elif isinstance(raw_entries, Iterable) and not isinstance(raw_entries, str | bytes):
            values = tuple(raw_entries)
        else:
            raise ValueError("ANGEE_IAM_OAUTH_CLIENTS must be a sequence or mapping of OAuth client entries.")
        for index, entry in enumerate(values, start=1):
            if not isinstance(entry, Mapping):
                raise ValueError(f"ANGEE_IAM_OAUTH_CLIENTS entry {index} must be a mapping.")
        return values

    def _sync_setting_entry(self, index: int, entry: Mapping[str, Any]) -> Any:
        """Upsert one settings-authored OAuth client row."""

        self._validate_setting_entry(index, entry)
        slug = str(entry["slug"])
        environment = str(entry.get("environment") or "prod")
        defaults = {field: entry[field] for field in sorted(self.seed_fields) if field in entry}
        if "client_secret" in entry:
            defaults["client_secret"] = str(entry.get("client_secret") or "")
        oauth_client, _created = self.update_or_create(
            slug=slug,
            environment=environment,
            defaults=defaults,
        )
        return oauth_client

    def _validate_setting_entry(self, index: int, entry: Mapping[str, Any]) -> None:
        """Raise a clear error for malformed OAuth client seed settings."""

        unknown = set(entry) - self.setting_fields
        if unknown:
            names = ", ".join(sorted(str(name) for name in unknown))
            raise ValueError(f"ANGEE_IAM_OAUTH_CLIENTS entry {index} has unknown field(s): {names}")
        missing = {field for field in self.required_setting_fields if not entry.get(field)}
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"ANGEE_IAM_OAUTH_CLIENTS entry {index} is missing required field(s): {names}")


class OAuthClient(SqidMixin, AuditMixin, AngeeModel):
    """OAuth/OIDC client registration and login policy.

    Self-describing: ``slug`` is iam's own login-client key and ``icon``/
    ``display_name`` its own login-button branding — iam renders the OIDC button
    without reading any catalogue. The integration-side third-party catalogue is
    ``integrate.Vendor``; that ``slug`` is a deliberately independent namespace,
    not a foreign key into this one.
    """

    runtime = True

    sqid = SqidField(real_field_name="id", prefix="clt", min_length=8)
    slug = models.SlugField()
    icon = models.CharField(max_length=128, blank=True)
    environment = models.CharField(max_length=32, default="prod")
    display_name = models.CharField(max_length=128)
    client_id = models.CharField(max_length=255, blank=True)
    client_secret = EncryptedField(blank=True)
    issuer = models.URLField(blank=True)
    authorize_endpoint = models.URLField(blank=True)
    token_endpoint = models.URLField(blank=True)
    revoke_endpoint = models.URLField(blank=True)
    userinfo_endpoint = models.URLField(blank=True)
    jwks_uri = models.URLField(blank=True)
    discovery_url = models.URLField(blank=True)
    token_request_format = models.CharField(max_length=16, default="form", blank=True)
    is_oidc = models.BooleanField(default=False, db_index=True)
    is_enabled = models.BooleanField(default=True, db_index=True)
    scopes_catalogue = models.JSONField(default=list, blank=True)
    default_scopes = models.JSONField(default=list, blank=True)
    supports_refresh = models.BooleanField(default=True)
    refresh_rotates = models.BooleanField(default=False)
    supports_pkce = models.BooleanField(default=True)
    max_refresh_age_seconds = models.PositiveIntegerField(null=True, blank=True)
    link_on_email_match = models.BooleanField(default=False)
    create_on_login = models.BooleanField(default=False)
    allowed_email_domains = models.JSONField(default=list, blank=True)
    authorize_params = models.JSONField(default=dict, blank=True)
    token_params = models.JSONField(default=dict, blank=True)
    external_id_claim = models.CharField(max_length=128, default="sub", blank=True)
    email_claim = models.CharField(max_length=128, default="email", blank=True)
    display_name_claim = models.CharField(max_length=128, blank=True)
    avatar_url_claim = models.CharField(max_length=128, blank=True)
    # Fixed manual-paste callback for fixed public clients (e.g. Anthropic) whose
    # allow-list we cannot extend. When set, connect uses a localhost loopback redirect
    # when the console runs on localhost, else this manual page (see resolve_connect_redirect).
    manual_redirect_uri = models.URLField(blank=True)

    objects = OAuthClientManager()

    class Meta:
        """Django model options for OAuth clients."""

        abstract = True
        ordering = ("slug", "environment")
        rebac_resource_type = "auth/oauth_client"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("slug", "environment"),
                name="uniq_iam_oauth_client_slug_environment",
            ),
        )

    def __str__(self) -> str:
        """Return the configured OAuth client display name or slug environment."""

        if self.display_name:
            return self.display_name
        return f"{self.slug or '?'} ({self.environment})"

    @property
    def configuration_state(self) -> str:
        """Return this OAuth client's operator-facing configuration readiness."""

        if not self.is_enabled:
            return "disabled"
        if not self.client_id:
            return "needs_client"
        if not self.discovery_url and not (self.authorize_endpoint and self.token_endpoint):
            return "needs_endpoints"
        return "ready"

    @property
    def default_scope_values(self) -> list[str]:
        """Return the configured default OAuth scopes as strings."""

        return self._string_list(self.default_scopes)

    @property
    def scopes_catalogue_values(self) -> list[str]:
        """Return the advertised OAuth scopes as strings."""

        return self._string_list(self.scopes_catalogue)

    @property
    def authorize_param_values(self) -> dict[str, str]:
        """Return configured provider-specific authorize parameters."""

        return self._string_mapping(self.authorize_params)

    @property
    def token_param_values(self) -> dict[str, str]:
        """Return configured provider-specific token-exchange parameters."""

        return self._string_mapping(self.token_params)

    def resolve_connect_redirect(self, proposed_redirect_uri: str) -> tuple[str, str]:
        """Return the ``(redirect_uri, mode)`` this client uses to connect from a browser.

        ``mode`` is ``"auto"`` (the provider redirects back to ``proposed_redirect_uri``)
        or ``"manual"`` (the user copies the code the provider displays and pastes it
        back). A client with no ``manual_redirect_uri`` always redirects back. A fixed
        public client (``manual_redirect_uri`` set) can redirect back only to a
        ``localhost`` loopback — its allow-list rejects other hosts and a cross-origin
        callback would also drop the session — so off-localhost it falls back to manual.
        """

        if not self.manual_redirect_uri:
            return proposed_redirect_uri, "auto"
        host = (urlsplit(proposed_redirect_uri).hostname or "").lower()
        if host == "localhost":
            return proposed_redirect_uri, "auto"
        return self.manual_redirect_uri, "manual"

    @property
    def token_request_format_value(self) -> str:
        """Return the configured token request body format."""

        value = str(self.token_request_format or "form").strip().lower()
        if value in {"form", "json"}:
            return value
        return "form"

    @property
    def allowed_email_domain_values(self) -> list[str]:
        """Return the login domain allow-list as strings."""

        return self._string_list(self.allowed_email_domains)

    def external_id_from_claims(self, claims: Mapping[str, Any]) -> str:
        """Return this provider account's stable external id from identity claims."""

        return self._claim_string(claims, self.external_id_claim)

    def email_from_claims(self, claims: Mapping[str, Any]) -> str:
        """Return this provider account's email from identity claims."""

        return self._claim_string(claims, self.email_claim)

    def display_name_from_claims(self, claims: Mapping[str, Any], email: str) -> str:
        """Return this provider account's display label from identity claims."""

        configured = self._claim_string(claims, self.display_name_claim)
        if configured:
            return configured
        return ExternalAccount.display_name_from_claims(claims, email)

    def avatar_url_from_claims(self, claims: Mapping[str, Any]) -> str:
        """Return this provider account's avatar URL from identity claims."""

        return self._claim_string(claims, self.avatar_url_claim)

    def allows_email_domain(self, email: str | None) -> bool:
        """Return whether ``email`` is allowed by this client's domain policy."""

        allowed_domains = {
            domain.strip().lower()
            for domain in self.allowed_email_domain_values
            if domain.strip()
        }
        if not allowed_domains:
            return True
        if not email or "@" not in email:
            return False
        return email.rsplit("@", 1)[1].lower() in allowed_domains

    def _string_list(self, value: object) -> list[str]:
        """Return one JSON-backed column value as a string list."""

        if not isinstance(value, (list, tuple)):
            return []
        return [str(item) for item in value]

    def _string_mapping(self, value: object) -> dict[str, str]:
        """Return one JSON-backed column value as string query/form params."""

        if not isinstance(value, Mapping):
            return {}
        return {str(key): str(item) for key, item in value.items() if item is not None}

    def _claim_string(self, claims: Mapping[str, Any], claim_name: str | None) -> str:
        """Return a flat or dotted-path claim value as a string."""

        name = str(claim_name or "").strip()
        if not name:
            return ""
        if name in claims:
            return self._string_claim_value(claims.get(name))
        value: Any = claims
        for part in name.split("."):
            if not isinstance(value, Mapping):
                return ""
            value = value.get(part)
            if value in (None, ""):
                return ""
        return self._string_claim_value(value)

    def _string_claim_value(self, value: Any) -> str:
        """Return scalar identity claim values only."""

        if value in (None, ""):
            return ""
        if isinstance(value, Mapping | list | tuple | set):
            return ""
        return str(value)


class CredentialQuerySet(RebacQuerySet[Any]):
    """REBAC-scoped reads for credential health and connected accounts."""

    def connected_for(self, user: Any) -> CredentialQuerySet:
        """Return ``user``'s external-account-backed credentials.

        Each row is a credential whose ``external_account`` (and that account's
        ``credential``) is preloaded under the ambient actor's scope. Owns the
        "what counts as a connected account" predicate for the self-service
        connected-accounts surface.
        """

        return cast(
            CredentialQuerySet,
            self.filter(
                user=user,
                external_account__isnull=False,
            ).rebac_select_related("external_account", "external_account__credential"),
        )

    def console_credentials(self) -> CredentialQuerySet:
        """Return admin-visible credential health with guarded FK joins."""

        return cast(
            CredentialQuerySet,
            self.rebac_select_related(
                "oauth_client",
                "external_account",
            ),
        )

    def is_only_oidc_sign_in(self, user: Any) -> bool:
        """Return whether ``user`` has no password and only one OIDC sign-in account."""

        if user.has_usable_password():
            return False
        with system_context(reason="iam.graphql.unlink_account.guard"):
            oidc_account_count = (
                self.filter(
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


class CredentialManager(RebacManager.from_queryset(CredentialQuerySet)):  # type: ignore[misc]
    """Manager for idempotent per-user credential writes.

    Actor-less framework writes run under ``system_context``; update paths do not maintain ``updated_by``.
    """

    caller_fields = frozenset(
        {
            "external_account",
            "status",
            "expires_at",
            "granted_scopes",
            "last_refresh_at",
            "last_refresh_status",
        }
    )
    operation_fields = frozenset({"kind", "material"})

    _REASON = "iam.connections.credential"

    def upsert_for_user(
        self,
        user: Any,
        oauth_client: Any,
        kind: str,
        material: dict[str, Any],
        /,
        *,
        external_account: Any | None = None,
        **fields: Any,
    ) -> Any:
        """Create or update one ``(user, oauth_client)`` OAuth credential (login flow)."""

        handler = handler_for(kind)
        operation_values, update_values = self._assemble_values(handler, material, fields)
        if external_account is not None:
            update_values["external_account"] = external_account
        create_values = {
            "external_account": external_account,
            **self._blank_create_values(),
            **operation_values,
            **update_values,
        }
        # Give the credential a human label on create (OAuth rows carry no name of their
        # own). Create-only so an admin rename, and token refreshes, are preserved.
        if not str(create_values.get("name") or ""):
            create_values["name"] = self._oauth_credential_name(oauth_client, external_account)
        with system_context(reason=self._REASON), transaction.atomic():
            instance, _created = self.update_or_create(
                user=user,
                oauth_client=oauth_client,
                defaults={**operation_values, **update_values},
                create_defaults=create_values,
            )
        return instance

    @staticmethod
    def _oauth_credential_name(oauth_client: Any, external_account: Any | None) -> str:
        """Return a default label for an OAuth credential: the provider, plus its subject."""

        label = str(
            getattr(oauth_client, "display_name", "") or getattr(oauth_client, "slug", "") or "OAuth"
        )
        subject = ""
        if external_account is not None:
            subject = str(
                getattr(external_account, "email", "")
                or getattr(external_account, "display_name", "")
                or getattr(external_account, "external_id", "")
                or ""
            )
        return f"{label} ({subject})" if subject else label

    def create_local_credential(
        self,
        user: Any,
        *,
        kind: str,
        name: str,
        material: dict[str, Any],
        **fields: Any,
    ) -> Any:
        """Create or update one provider-less (static/ssh) credential, keyed by ``(user, name)``.

        The provider-less counterpart to :meth:`upsert_for_user`: ``oauth_client`` is
        ``NULL`` (the kind/provider invariant forbids one), and ``name`` is the
        credential's identity. OAuth credentials are minted by the login flow only.
        """

        handler = handler_for(kind)
        if handler.kind == CredentialKind.OAUTH:
            raise ValueError("OAuth credentials are minted by the login flow, not create_local_credential().")
        if not name:
            raise ValueError("A provider-less credential requires a name.")
        operation_values, update_values = self._assemble_values(handler, material, fields)
        create_values = {
            "oauth_client": None,
            "external_account": None,
            **self._blank_create_values(),
            **operation_values,
            **update_values,
        }
        with system_context(reason=self._REASON), transaction.atomic():
            instance, _created = self.update_or_create(
                user=user,
                name=name,
                oauth_client=None,
                defaults={**operation_values, **update_values},
                create_defaults=create_values,
            )
        return instance

    def _assemble_values(
        self,
        handler: Any,
        material: dict[str, Any],
        fields: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return ``(operation_values, update_values)`` for both create paths."""

        handler.validate(material)
        if owned := self.operation_fields & fields.keys():
            names = ", ".join(sorted(owned))
            raise ValueError(f"Credential field(s) are owned by the manager: {names}")
        update_values = handler.upsert_fields(material)
        update_values.update(
            _validated_manager_values(self.model, fields, allowed=self.caller_fields)
        )
        update_values["status"] = CredentialStatus.ACTIVE
        material_value = json.dumps(material, sort_keys=True, separators=(",", ":"))
        operation_values = {"kind": handler.kind, "material": material_value}
        return operation_values, update_values

    @staticmethod
    def _blank_create_values() -> dict[str, Any]:
        """Return the inactive-refresh defaults a fresh credential row starts from."""

        return {
            "status": CredentialStatus.ACTIVE,
            "expires_at": None,
            "granted_scopes": [],
            "last_refresh_at": None,
            "last_refresh_status": "",
        }


class Credential(SqidMixin, AuditMixin, AngeeModel):
    """Per-user credential material for acting against a vendor OAuth client."""

    runtime = True

    sqid = SqidField(real_field_name="id", prefix="crd", min_length=8)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="credentials",
    )
    oauth_client = models.ForeignKey(
        "iam.OAuthClient",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="oauth_credentials",
    )
    """The provider this credential authenticates to — required for ``oauth`` (its
    identity is the provider account), optional for local kinds
    (``static_token``/``ssh_key``), which may be created without one."""
    external_account = models.ForeignKey(
        "iam.ExternalAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="credentials",
    )
    name = models.CharField(max_length=255, blank=True)
    """Human label for a provider-less credential (its identity, unique per user);
    ``oauth`` credentials leave it blank and derive a label from the provider/account."""
    kind = StateField(choices_enum=CredentialKind)
    material = EncryptedField()
    status = StateField(choices_enum=CredentialStatus, default=CredentialStatus.ACTIVE)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    granted_scopes = models.JSONField(default=list, blank=True)
    last_refresh_at = models.DateTimeField(null=True, blank=True)
    last_refresh_status = models.CharField(max_length=32, blank=True)

    objects = CredentialManager()

    class Meta:
        """Django model options for credentials."""

        abstract = True
        ordering = ("-updated_at",)
        rebac_resource_type = "auth/credential"
        rebac_id_attr = "sqid"
        constraints = (
            # OAuth identity: one credential per (user, provider). NULLs are
            # distinct in SQL unique, so provider-less kinds need their own arm.
            models.UniqueConstraint(
                fields=("user", "oauth_client"),
                condition=Q(oauth_client__isnull=False),
                name="uniq_iam_credential_user_oauth_client",
            ),
            # Provider-less identity: one named credential per user (named local
            # credentials; provider-bearing rows use the arm above).
            models.UniqueConstraint(
                fields=("user", "name"),
                condition=Q(oauth_client__isnull=True) & ~Q(name=""),
                name="uniq_iam_credential_user_name_local",
            ),
            # A provider is required only for ``oauth`` (its identity is the
            # provider account); local kinds may omit it.
            models.CheckConstraint(
                condition=~Q(kind=CredentialKind.OAUTH) | Q(oauth_client__isnull=False),
                name="iam_credential_oauth_requires_provider",
            ),
            # The mirror of the unique arm above: a provider-less credential is
            # identified by a non-blank name, so the DB (not just the factory) owns
            # "every local credential is named".
            models.CheckConstraint(
                condition=Q(oauth_client__isnull=False) | ~Q(name=""),
                name="iam_credential_local_requires_name",
            ),
        )

    @property
    def handler(self) -> Any:
        """Return the registered handler for this credential kind."""

        return handler_for(self.kind)

    def reveal(self) -> dict[str, Any]:
        """Return decrypted material through the kind handler."""

        return self.handler.reveal(self)

    def auth_headers(self) -> dict[str, str]:
        """Return authorization headers through the kind handler."""

        return self.handler.auth_headers(self)

    def secret_value(self) -> str:
        """Return the primary secret value through the kind handler."""

        return str(self.handler.secret_value(self))


def _validated_manager_values(
    model: type[models.Model],
    values: dict[str, Any],
    *,
    allowed: frozenset[str],
) -> dict[str, Any]:
    """Return caller values validated against one manager-owned field set."""

    unknown = set(values) - allowed
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(f"Unknown {model.__name__} field(s): {names}")
    return dict(values)
