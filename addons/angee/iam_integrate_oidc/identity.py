"""OIDC login identity resolution: verify an external identity into an Angee user.

The login bridge between ``integrate`` (the OAuth/OIDC connection substrate) and
``iam`` (the user and session). It completes the login/link redirect using the
OIDC protocol, then resolves the verified claims to a host user — returning an
existing linked owner, or linking/creating one when the provider's OAuth-client
login policy allows. Account-connect (no login) lives in ``integrate.connect``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, cast

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import AbstractBaseUser
from django.db import models, transaction
from rebac import system_context

from angee.iam.auth import can_authenticate_user
from angee.iam_integrate_oidc.protocol import OAuthClientOidcProtocol
from angee.integrate.connect import complete_external_account_link
from angee.integrate.credentials import CredentialKind
from angee.integrate.models import AccountStatus
from angee.integrate.oauth import flow
from angee.integrate.oauth.errors import INVALID_ID_TOKEN, INVALID_STATE, OAuthFlowError
from angee.integrate.oauth.state import StateFlow, StateRecord

IDENTITY_RESOLUTION_FAILED = "identity_resolution_failed"
SESSION_AUTH_BACKEND = "angee.iam.auth.ModelBackend"


@dataclass(frozen=True, slots=True)
class LoginCompletion:
    """Resolved user and verified claims from one completed OIDC login flow."""

    user: AbstractBaseUser
    claims: dict[str, Any]
    next_path: str

    def __post_init__(self) -> None:
        """Bind Django's session backend contract for direct login()."""

        self.user.backend = SESSION_AUTH_BACKEND


@dataclass(frozen=True, slots=True)
class LinkCompletion:
    """Linked account, captured user, and verified claims from one OIDC link flow."""

    account: models.Model
    user: AbstractBaseUser
    claims: dict[str, Any]
    next_path: str


def _require_login_enabled(oauth_client: Any) -> Any:
    """Return the OAuth client when it is enabled for OIDC login, or fail."""

    if not bool(getattr(oauth_client, "login_enabled", False)):
        raise OAuthFlowError(INVALID_STATE, 400)
    return oauth_client


class OidcLoginCompletion:
    """Complete one OIDC login/link redirect against an OAuth client."""

    def __init__(self, oauth_client: Any) -> None:
        """Bind completion to the OAuth client captured by session state."""

        self.oauth_client = oauth_client
        _require_login_enabled(oauth_client)
        self.protocol = OAuthClientOidcProtocol(oauth_client)

    def complete_login(self, *, code: str, state_token: str, redirect_uri: str) -> LoginCompletion:
        """Complete an OIDC login redirect and return the resolved user with claims."""

        record = flow.consume_validated_state(
            self.oauth_client, state_token, redirect_uri, expected_flow=StateFlow.LOGIN
        )
        _tokens, claims = self._exchange_verify(code, state_token, redirect_uri, record)
        sub = self._required_sub(claims)
        user = resolve(self.oauth_client, sub=sub, email=self._claim_email(claims), claims=claims)
        return LoginCompletion(user=user, claims=claims, next_path=record.next_path or "/")

    def complete_link(self, *, code: str, state_token: str, redirect_uri: str) -> LinkCompletion:
        """Complete an authenticated OIDC account-link redirect and return the linked account.

        Links the verified external identity to the start-flow user and stores its
        OAuth credential under ``(user, provider)`` — the same shape as connect, but
        gated on a verified ID token.
        """

        record = flow.consume_validated_state(
            self.oauth_client, state_token, redirect_uri, expected_flow=StateFlow.LINK
        )
        link_user = OidcIdentityResolver(self.oauth_client).user_for_link_state(record)
        tokens, claims = self._exchange_verify(code, state_token, redirect_uri, record)
        sub = self._required_sub(claims)
        completion = complete_external_account_link(
            self.oauth_client,
            user=link_user,
            external_id=sub,
            tokens=tokens,
            claims=claims,
            next_path=record.next_path or "/",
            reason="iam_integrate_oidc.link",
        )
        return LinkCompletion(
            account=completion.account,
            user=completion.user,
            claims=completion.claims,
            next_path=completion.next_path,
        )

    def _exchange_verify(
        self, code: str, state_token: str, redirect_uri: str, record: StateRecord
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return ``(tokens, claims)``: verified ID-token claims enriched by userinfo."""

        tokens = self.protocol.exchange_code(
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=record.code_verifier,
            state=state_token,
        )
        claims = self.protocol.verify_id_token(str(tokens.get("id_token", "")), nonce=record.nonce)
        userinfo = self.protocol.fetch_userinfo(str(tokens.get("access_token", "") or ""))
        if userinfo:
            claims = {**userinfo, **claims}
        return tokens, claims

    def _required_sub(self, claims: dict[str, Any]) -> str:
        """Return the subject claim or fail the redirect completion."""

        sub = claims.get("sub")
        if not sub:
            raise OAuthFlowError(INVALID_ID_TOKEN, 400)
        return str(sub)

    def _claim_email(self, claims: dict[str, Any]) -> str | None:
        """Return the email claim when present."""

        value = self.oauth_client.email_from_claims(claims)
        return value or None


class OidcIdentityResolver:
    """Resolve OIDC claims to a host user, linking or provisioning when policy allows.

    Operates on ``get_user_model()`` (the host's swappable user model) through
    generic Django manager methods, so it is correct whatever the host's User is.
    The per-provider login policy is read from the OAuth client row.
    """

    def __init__(self, oauth_client: Any) -> None:
        """Bind resolution to the OAuth client whose OIDC login policy applies."""

        self.oauth_client = oauth_client
        _require_login_enabled(oauth_client)

    def resolve(self, *, sub: str, email: str | None, claims: dict[str, Any]) -> AbstractBaseUser:
        """Return the user for one verified OIDC identity, or fail closed."""

        Account = cast(Any, apps.get_model("integrate", "ExternalAccount"))
        with system_context(reason="iam_integrate_oidc.resolve"), transaction.atomic():
            account = Account.objects.filter(oauth_client=self.oauth_client, external_id=sub).first()
            if account is not None:
                # A revoked/expired/disabled account or a deactivated user must not log in.
                if account.status != AccountStatus.ACTIVE:
                    raise OAuthFlowError(IDENTITY_RESOLUTION_FAILED, 403)
                owner = Account.objects.owner_for(account)
                if owner is None or not can_authenticate_user(owner):
                    raise OAuthFlowError(IDENTITY_RESOLUTION_FAILED, 403)
                return cast(AbstractBaseUser, owner)

            normalized_email = email or ""
            email_verified = claims.get("email_verified") is True
            if (
                self.oauth_client.link_on_email_match
                and normalized_email
                and email_verified
                and self.oauth_client.allows_email_domain(normalized_email)
            ):
                user = self._find_by_email(normalized_email)
                if user is not None and can_authenticate_user(user):
                    Account.objects.link(
                        self.oauth_client,
                        sub,
                        owner=user,
                        email=normalized_email,
                        identity_claims=claims,
                        display_name=Account.display_name_from_claims(claims, normalized_email),
                    )
                    return user

            if self.oauth_client.create_on_login and (
                not normalized_email
                or (email_verified and self.oauth_client.allows_email_domain(normalized_email))
            ):
                user = self._create_for_identity(normalized_email, sub, claims=claims)
                Account.objects.link(
                    self.oauth_client,
                    sub,
                    owner=user,
                    email=normalized_email,
                    identity_claims=claims,
                    display_name=Account.display_name_from_claims(claims, normalized_email),
                )
                return user

        raise OAuthFlowError(IDENTITY_RESOLUTION_FAILED, 403)

    def user_for_link_state(self, record: StateRecord) -> AbstractBaseUser:
        """Return the user captured when the authenticated link flow started."""

        if not record.user_id:
            raise OAuthFlowError(INVALID_STATE, 400)
        user_model = get_user_model()
        manager = cast(Any, user_model.objects)
        with system_context(reason="iam_integrate_oidc.link_user"):
            try:
                user = manager.get(pk=record.user_id)
            except user_model.DoesNotExist as exc:
                raise OAuthFlowError(INVALID_STATE, 400) from exc
        return cast(AbstractBaseUser, user)

    def _find_by_email(self, email: str) -> AbstractBaseUser | None:
        """Return the unique user matching ``email`` case-insensitively."""

        manager = cast(Any, get_user_model().objects)
        queryset = manager.all().people()
        matches = list(queryset.filter(email__iexact=email).order_by("pk")[:2])
        if len(matches) > 1:
            raise OAuthFlowError(IDENTITY_RESOLUTION_FAILED, 403)
        return cast(AbstractBaseUser | None, matches[0] if matches else None)

    def _create_for_identity(self, email: str, sub: str, *, claims: dict[str, Any]) -> AbstractBaseUser:
        """Create a non-superuser user for one OIDC identity."""

        manager = cast(Any, get_user_model().objects)
        user_fields: dict[str, Any] = {}
        if given_name := claims.get("given_name"):
            user_fields["first_name"] = str(given_name)
        if family_name := claims.get("family_name"):
            user_fields["last_name"] = str(family_name)
        return cast(
            AbstractBaseUser,
            manager.create_user(
                username=self._available_username(email or f"oidc-{sub}"),
                email=email,
                password=None,
                is_staff=False,
                is_superuser=False,
                **user_fields,
            ),
        )

    def _available_username(self, seed: str) -> str:
        """Return a unique Django username derived from ``seed``."""

        manager = cast(Any, get_user_model().objects)
        base = re.sub(r"[^\w.@+-]", "-", seed).strip("-")[:140] or "oidc-user"
        candidate = base
        suffix = 1
        while manager.filter(username=candidate).exists():
            suffix_text = f"-{suffix}"
            candidate = f"{base[: 150 - len(suffix_text)]}{suffix_text}"
            suffix += 1
        return candidate


def resolve(oauth_client: Any, *, sub: str, email: str | None, claims: dict[str, Any]) -> AbstractBaseUser:
    """Resolve OIDC claims to a host user, linking or provisioning when policy allows.

    The identity-resolution seam the login completion calls (and callers/tests can
    substitute); delegates to :class:`OidcIdentityResolver`.
    """

    return OidcIdentityResolver(oauth_client).resolve(sub=sub, email=email, claims=claims)


def complete_login(oauth_client: Any, *, code: str, state_token: str, redirect_uri: str) -> LoginCompletion:
    """Complete an OIDC login redirect and return the resolved user with claims."""

    return OidcLoginCompletion(oauth_client).complete_login(
        code=code, state_token=state_token, redirect_uri=redirect_uri
    )


def complete_link(oauth_client: Any, *, code: str, state_token: str, redirect_uri: str) -> LinkCompletion:
    """Complete an authenticated OIDC account-link redirect and return the linked account.

    The linked user is the one captured in state when the flow started, never a
    later session user.
    """

    return OidcLoginCompletion(oauth_client).complete_link(
        code=code, state_token=state_token, redirect_uri=redirect_uri
    )


def is_only_oidc_sign_in(user: Any) -> bool:
    """Return whether ``user`` has no password and only one OIDC sign-in account.

    The guard the disconnect path consults before removing a sign-in credential, so
    a user who logs in solely through OIDC cannot strip their last way back in.
    """

    if user.has_usable_password():
        return False
    Credential = cast(Any, apps.get_model("integrate", "Credential"))
    with system_context(reason="iam_integrate_oidc.unlink.guard"):
        oidc_account_count = (
            Credential.objects.filter(
                user=user,
                kind="oauth",
                oauth_client__login_enabled=True,
                external_account__isnull=False,
            )
            .values("external_account_id")
            .distinct()
            .count()
        )
    return oidc_account_count <= 1


def guard_last_sign_in_disconnect(credential: Any) -> None:
    """Veto explicit disconnect of a user's last OIDC sign-in credential."""

    if str(credential.kind) != CredentialKind.OAUTH:
        return
    oauth_client = getattr(credential, "oauth_client", None)
    if oauth_client is None or not getattr(oauth_client, "login_enabled", False):
        return
    if is_only_oidc_sign_in(credential.user):
        raise OAuthFlowError("only_sign_in_method", 409)
