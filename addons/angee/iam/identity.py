"""Native IAM identity resolution: OIDC flows and user-reference display."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, cast

from asgiref.sync import sync_to_async
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import AbstractBaseUser
from django.db import models, transaction
from rebac import system_context

from angee.base.models import public_id_of
from angee.iam.credentials import CredentialKind
from angee.iam.models import AccountStatus
from angee.iam.oidc import client as client_module
from angee.iam.oidc import state
from angee.iam.oidc.errors import (
    EXTERNAL_ACCOUNT_RESOLUTION_FAILED,
    IDENTITY_RESOLUTION_FAILED,
    INVALID_ID_TOKEN,
    INVALID_STATE,
    OidcFlowError,
)


@dataclass(frozen=True, slots=True)
class LoginCompletion:
    """Resolved user and verified claims from one completed OIDC login flow."""

    user: AbstractBaseUser
    claims: dict[str, Any]
    next_path: str


@dataclass(frozen=True, slots=True)
class LinkCompletion:
    """Linked account, captured user, and verified claims from one OIDC link flow."""

    account: models.Model
    user: AbstractBaseUser
    claims: dict[str, Any]
    next_path: str


@dataclass(frozen=True, slots=True)
class AccountConnectionCompletion:
    """Connected account, captured credential, and verified provider claims."""

    account: models.Model
    credential: models.Model
    user: AbstractBaseUser
    claims: dict[str, Any]
    next_path: str


class OidcRedirectCompletion:
    """Complete one OIDC redirect against an OAuth client registration."""

    def __init__(self, oauth_client: Any) -> None:
        """Bind completion work to the OAuth client captured by session state."""

        self.oauth_client = oauth_client

    def complete_login(
        self,
        *,
        code: str,
        state_token: str,
        redirect_uri: str,
    ) -> LoginCompletion:
        """Complete an OIDC login redirect and return the resolved user with claims."""

        record = self._consume_state(
            state_token,
            redirect_uri,
            expected_flow=state.StateFlow.LOGIN,
        )
        claims, _tokens = self._claims_from_code(code, state_token, redirect_uri, record)
        sub = self._required_sub(claims)
        user = resolve(self.oauth_client, sub=sub, email=self._claim_email(claims), claims=claims)
        return LoginCompletion(user=user, claims=claims, next_path=self._record_next_path(record))

    def complete_link(
        self,
        *,
        code: str,
        state_token: str,
        redirect_uri: str,
    ) -> LinkCompletion:
        """Complete an authenticated OIDC account-link redirect and return the linked account."""

        result = self._complete_account_flow(
            code=code,
            state_token=state_token,
            redirect_uri=redirect_uri,
            expected_flow=state.StateFlow.LINK,
        )
        return LinkCompletion(
            account=result.account,
            user=result.user,
            claims=result.claims,
            next_path=result.next_path,
        )

    def complete_account_connect(
        self,
        *,
        code: str,
        state_token: str,
        redirect_uri: str,
    ) -> AccountConnectionCompletion:
        """Complete an authenticated OAuth account-connect redirect."""

        return self._complete_account_flow(
            code=code,
            state_token=state_token,
            redirect_uri=redirect_uri,
            expected_flow=state.StateFlow.CONNECT,
        )

    def _complete_account_flow(
        self,
        *,
        code: str,
        state_token: str,
        redirect_uri: str,
        expected_flow: state.StateFlow,
    ) -> AccountConnectionCompletion:
        """Link an external account and upsert its OAuth credential for a flow."""

        record = self._consume_state(
            state_token,
            redirect_uri,
            expected_flow=expected_flow,
        )
        link_user = self._link_state_user(record)
        claims, tokens = self._account_claims_from_code(code, state_token, redirect_uri, record)
        external_id = self._required_external_id(claims)
        Account = cast(Any, apps.get_model("iam", "ExternalAccount"))
        Credential = cast(Any, apps.get_model("iam", "Credential"))
        email = self._claim_email(claims) or ""
        with system_context(reason="iam.oauth.connect"), transaction.atomic():
            account = Account.objects.filter(oauth_client=self.oauth_client, external_id=external_id).first()
            if account is not None:
                owner = Account.objects.owner_for(account)
                if owner is not None and owner.pk != link_user.pk:
                    raise OidcFlowError("account_already_linked", 409)
            account = Account.objects.link(
                self.oauth_client,
                external_id,
                owner=link_user,
                email=email,
                identity_claims=claims,
                display_name=self.oauth_client.display_name_from_claims(claims, email),
                avatar_url=self.oauth_client.avatar_url_from_claims(claims),
            )
            credential = Credential.objects.upsert_for_user(
                link_user,
                self.oauth_client,
                CredentialKind.OAUTH,
                tokens,
                external_account=account,
            )
            account.credential = credential
            account.save(update_fields=["credential", "updated_at"])
        return AccountConnectionCompletion(
            account=cast(models.Model, account),
            credential=cast(models.Model, credential),
            user=link_user,
            claims=claims,
            next_path=self._record_next_path(record),
        )

    def _claims_from_code(
        self,
        code: str,
        state_token: str,
        redirect_uri: str,
        record: state.StateRecord,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return verified ID-token claims enriched by best-effort userinfo claims."""

        tokens = client_module.exchange_code(
            self.oauth_client,
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=record.code_verifier,
            state=state_token,
        )
        claims = client_module.verify_id_token(
            self.oauth_client,
            str(tokens.get("id_token", "")),
            nonce=record.nonce,
        )
        userinfo = client_module.fetch_userinfo(
            self.oauth_client,
            str(tokens.get("access_token", "") or ""),
        )
        if not userinfo:
            return claims, tokens
        return {**userinfo, **claims}, tokens

    def _account_claims_from_code(
        self,
        code: str,
        state_token: str,
        redirect_uri: str,
        record: state.StateRecord,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return account identity claims for either OIDC or plain OAuth clients."""

        if getattr(self.oauth_client, "is_oidc", False):
            return self._claims_from_code(code, state_token, redirect_uri, record)
        tokens = client_module.exchange_code(
            self.oauth_client,
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=record.code_verifier,
            state=state_token,
        )
        claims = client_module.fetch_userinfo(
            self.oauth_client,
            str(tokens.get("access_token", "") or ""),
        )
        return claims, tokens

    def _consume_state(
        self,
        state_token: str,
        redirect_uri: str,
        *,
        expected_flow: state.StateFlow,
    ) -> state.StateRecord:
        """Consume and validate a state record for this OAuth client and flow."""

        record = state.consume(state_token)
        self._validate_state_record(record, redirect_uri, expected_flow=expected_flow)
        return record

    def _validate_state_record(
        self,
        record: state.StateRecord,
        redirect_uri: str,
        *,
        expected_flow: state.StateFlow,
    ) -> None:
        """Fail closed when a consumed state record does not match this flow."""

        oauth_client_id = str(getattr(self.oauth_client, "sqid", getattr(self.oauth_client, "pk", "")))
        if (
            record.flow != expected_flow
            or record.oauth_client_id != oauth_client_id
            or record.redirect_uri != redirect_uri
        ):
            raise OidcFlowError(INVALID_STATE, 400)

    def _link_state_user(self, record: state.StateRecord) -> AbstractBaseUser:
        """Return the user captured when the authenticated link flow started."""

        return OidcIdentityResolver(self.oauth_client).user_for_link_state(record)

    def _required_sub(self, claims: dict[str, Any]) -> str:
        """Return the subject claim or fail the redirect completion."""

        sub = claims.get("sub")
        if not sub:
            raise OidcFlowError(INVALID_ID_TOKEN, 400)
        return str(sub)

    def _required_external_id(self, claims: dict[str, Any]) -> str:
        """Return the provider account id or fail the account-connect completion."""

        external_id = self.oauth_client.external_id_from_claims(claims)
        if not external_id:
            raise OidcFlowError(EXTERNAL_ACCOUNT_RESOLUTION_FAILED, 400)
        return external_id

    def _claim_email(self, claims: dict[str, Any]) -> str | None:
        """Return the email claim when present."""

        value = self.oauth_client.email_from_claims(claims)
        return value or None

    def _record_next_path(self, record: state.StateRecord) -> str:
        """Return the stored post-flow redirect path with the public default."""

        return record.next_path or "/"


class OidcIdentityResolver:
    """Resolve OIDC claims to a host user, linking or provisioning when policy allows.

    Operates on ``get_user_model()`` (the host's swappable user model) through
    generic Django manager methods, so it is correct whatever the host's User is —
    the resolution is identity-subsystem behavior, not iam-User-manager behavior.
    """

    def __init__(self, oauth_client: Any) -> None:
        """Bind resolution to the OAuth client whose login/link policy applies."""

        self.oauth_client = oauth_client

    def resolve(self, *, sub: str, email: str | None, claims: dict[str, Any]) -> AbstractBaseUser:
        """Return the user for one verified OIDC identity, or fail closed."""

        Account = cast(Any, apps.get_model("iam", "ExternalAccount"))
        with system_context(reason="iam.oidc.resolve"), transaction.atomic():
            account = (
                Account.objects.filter(oauth_client=self.oauth_client, external_id=sub).first()
            )
            if account is not None:
                # A revoked/expired/disabled account or a deactivated user must not log in.
                if account.status != AccountStatus.ACTIVE:
                    raise OidcFlowError(IDENTITY_RESOLUTION_FAILED, 403)
                owner = Account.objects.owner_for(account)
                if owner is None or not owner.is_active:
                    raise OidcFlowError(IDENTITY_RESOLUTION_FAILED, 403)
                return cast(AbstractBaseUser, owner)

            normalized_email = email or ""
            if (
                getattr(self.oauth_client, "link_on_email_match", False)
                and normalized_email
                and self.oauth_client.allows_email_domain(normalized_email)
            ):
                user = self._find_by_email(normalized_email)
                if user is not None and user.is_active:
                    Account.objects.link(
                        self.oauth_client,
                        sub,
                        owner=user,
                        email=normalized_email,
                        identity_claims=claims,
                        display_name=Account.display_name_from_claims(claims, normalized_email),
                    )
                    return user

            if (
                getattr(self.oauth_client, "create_on_login", False)
                and (
                    not normalized_email
                    or self.oauth_client.allows_email_domain(normalized_email)
                )
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

        raise OidcFlowError(IDENTITY_RESOLUTION_FAILED, 403)

    def user_for_link_state(self, record: state.StateRecord) -> AbstractBaseUser:
        """Return the user captured when the authenticated link flow started."""

        if not record.user_id:
            raise OidcFlowError(INVALID_STATE, 400)
        user_model = get_user_model()
        manager = cast(Any, user_model.objects)
        with system_context(reason="iam.oidc.link_user"):
            try:
                user = manager.get(pk=record.user_id)
            except user_model.DoesNotExist as exc:
                raise OidcFlowError(INVALID_STATE, 400) from exc
        return cast(AbstractBaseUser, user)

    def _find_by_email(self, email: str) -> AbstractBaseUser | None:
        """Return the first user matching ``email`` case-insensitively."""

        manager = cast(Any, get_user_model().objects)
        return cast(AbstractBaseUser | None, manager.filter(email__iexact=email).order_by("pk").first())

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
    """Resolve OIDC claims to a user, linking or provisioning when policy allows."""

    return OidcIdentityResolver(oauth_client).resolve(sub=sub, email=email, claims=claims)


async def aresolve(oauth_client: Any, *, sub: str, email: str | None, claims: dict[str, Any]) -> AbstractBaseUser:
    """Async wrapper for ``resolve`` that keeps sync ORM work thread-sensitive."""

    return await sync_to_async(resolve, thread_sensitive=True)(
        oauth_client,
        sub=sub,
        email=email,
        claims=claims,
    )


def complete_login(
    oauth_client: Any,
    *,
    code: str,
    state_token: str,
    redirect_uri: str,
) -> LoginCompletion:
    """Complete an OIDC login redirect and return the resolved user with claims."""

    return OidcRedirectCompletion(oauth_client).complete_login(
        code=code,
        state_token=state_token,
        redirect_uri=redirect_uri,
    )


async def acomplete_login(
    oauth_client: Any,
    *,
    code: str,
    state_token: str,
    redirect_uri: str,
) -> LoginCompletion:
    """Async wrapper for ``complete_login`` that keeps sync ORM work thread-sensitive."""

    return await sync_to_async(complete_login, thread_sensitive=True)(
        oauth_client,
        code=code,
        state_token=state_token,
        redirect_uri=redirect_uri,
    )


def complete_link(
    oauth_client: Any,
    user: AbstractBaseUser | None = None,
    *,
    code: str,
    state_token: str,
    redirect_uri: str,
) -> LinkCompletion:
    """Complete an authenticated account-link redirect and return the linked account."""

    del user
    return OidcRedirectCompletion(oauth_client).complete_link(
        code=code,
        state_token=state_token,
        redirect_uri=redirect_uri,
    )


async def acomplete_link(
    oauth_client: Any,
    user: AbstractBaseUser | None = None,
    *,
    code: str,
    state_token: str,
    redirect_uri: str,
) -> LinkCompletion:
    """Async wrapper for ``complete_link`` that keeps sync ORM work thread-sensitive."""

    return await sync_to_async(complete_link, thread_sensitive=True)(
        oauth_client,
        user,
        code=code,
        state_token=state_token,
        redirect_uri=redirect_uri,
    )


def complete_account_connect(
    oauth_client: Any,
    *,
    code: str,
    state_token: str,
    redirect_uri: str,
) -> AccountConnectionCompletion:
    """Complete an authenticated OAuth account-connect redirect."""

    return OidcRedirectCompletion(oauth_client).complete_account_connect(
        code=code,
        state_token=state_token,
        redirect_uri=redirect_uri,
    )


async def acomplete_account_connect(
    oauth_client: Any,
    *,
    code: str,
    state_token: str,
    redirect_uri: str,
) -> AccountConnectionCompletion:
    """Async wrapper for ``complete_account_connect``."""

    return await sync_to_async(complete_account_connect, thread_sensitive=True)(
        oauth_client,
        code=code,
        state_token=state_token,
        redirect_uri=redirect_uri,
    )


def user_public_id(user_id: Any) -> str | None:
    """Return a user's opaque public id without fetching the user row."""

    if user_id is None:
        return None
    return public_id_of(get_user_model()(id=user_id))


def user_display_label(user_id: Any) -> str | None:
    """Return a user's display label (name) without exposing the user object.

    Resolved under ``system_context`` (IAM's elevation for server-side
    reads) so an actor-scoped caller never pulls a guarded User row into
    its own queryset — REBAC rejects that; only a display string leaves
    the helper. Intended for the single-record form — not selected as a
    list column.
    """

    if user_id is None:
        return None
    with system_context(reason="iam.identity.user_label"):
        user = (
            get_user_model()
            .objects.filter(pk=user_id)
            .only("first_name", "last_name", "username")
            .first()
        )
    if user is None:
        return None
    return str(user.get_full_name() or user.username)
