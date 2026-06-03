"""Native IAM identity resolution for OIDC flows."""

from __future__ import annotations

import re
from typing import Any, cast

from asgiref.sync import sync_to_async
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import AbstractBaseUser
from django.db import models, transaction
from rebac import system_context

from angee.iam.models import AccountStatus
from angee.iam.oidc import client as client_module
from angee.iam.oidc import state
from angee.iam.oidc.errors import (
    IDENTITY_RESOLUTION_FAILED,
    INVALID_ID_TOKEN,
    INVALID_STATE,
    OidcFlowError,
)


def resolve(oauth_client: Any, *, sub: str, email: str | None, claims: dict[str, Any]) -> AbstractBaseUser:
    """Resolve OIDC claims to an IAM user, linking or provisioning when policy allows."""

    Account = cast(Any, apps.get_model("iam", "ExternalAccount"))
    with system_context(reason="iam.oidc.resolve"), transaction.atomic():
        account = (
            Account.objects.select_related("vendor")
            .filter(vendor=oauth_client.vendor, external_id=sub)
            .first()
        )
        if account is not None:
            # Honor the account lifecycle and is_active the way the password path does:
            # a revoked/expired/disabled account or a deactivated user must not log in.
            if account.status != AccountStatus.ACTIVE:
                raise OidcFlowError(IDENTITY_RESOLUTION_FAILED, 403)
            owner = Account.objects.owner_for(account)
            if owner is None or not owner.is_active:
                raise OidcFlowError(IDENTITY_RESOLUTION_FAILED, 403)
            return owner

        normalized_email = email or ""
        # Linking an IdP identity to an existing local account by email is an
        # account-takeover vector unless the IdP asserts the email is verified.
        if (
            getattr(oauth_client, "link_on_email_match", False)
            and normalized_email
            and _email_verified(claims)
            and _domain_allowed(oauth_client, normalized_email)
        ):
            user = _find_user_by_email(normalized_email)
            if user is not None and user.is_active:
                Account.objects.link(
                    oauth_client.vendor,
                    sub,
                    owner=user,
                    email=normalized_email,
                    identity_claims=claims,
                    display_name=_display_name(claims, normalized_email),
                )
                return user

        # Provisioning a user trusts the email only when verified (else create
        # with no email rather than an attacker-chosen one).
        if (
            getattr(oauth_client, "create_on_login", False)
            and _domain_allowed(oauth_client, normalized_email)
            and (_email_verified(claims) or not normalized_email)
        ):
            user = _create_user_for_identity(normalized_email, sub)
            Account.objects.link(
                oauth_client.vendor,
                sub,
                owner=user,
                email=normalized_email,
                identity_claims=claims,
                display_name=_display_name(claims, normalized_email),
            )
            return user

    raise OidcFlowError(IDENTITY_RESOLUTION_FAILED, 403)


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
) -> AbstractBaseUser:
    """Complete an OIDC login redirect and return the resolved IAM user."""

    record = state.consume(state_token)
    _validate_state_record(oauth_client, record, redirect_uri)
    tokens = client_module.exchange_code(
        oauth_client,
        code=code,
        redirect_uri=redirect_uri,
        code_verifier=record.code_verifier,
    )
    claims = client_module.verify_id_token(
        oauth_client,
        str(tokens.get("id_token", "")),
        nonce=record.nonce,
    )
    sub = claims.get("sub")
    if not sub:
        raise OidcFlowError(INVALID_ID_TOKEN, 400)
    return resolve(oauth_client, sub=str(sub), email=_claim_email(claims), claims=claims)


async def acomplete_login(
    oauth_client: Any,
    *,
    code: str,
    state_token: str,
    redirect_uri: str,
) -> AbstractBaseUser:
    """Async wrapper for ``complete_login`` that keeps sync ORM work thread-sensitive."""

    return await sync_to_async(complete_login, thread_sensitive=True)(
        oauth_client,
        code=code,
        state_token=state_token,
        redirect_uri=redirect_uri,
    )


def complete_link(
    oauth_client: Any,
    user: AbstractBaseUser,
    *,
    code: str,
    state_token: str,
    redirect_uri: str,
) -> models.Model:
    """Complete an authenticated account-link redirect and return the external account."""

    record = state.consume(state_token)
    _validate_state_record(oauth_client, record, redirect_uri)
    tokens = client_module.exchange_code(
        oauth_client,
        code=code,
        redirect_uri=redirect_uri,
        code_verifier=record.code_verifier,
    )
    claims = client_module.verify_id_token(
        oauth_client,
        str(tokens.get("id_token", "")),
        nonce=record.nonce,
    )
    sub = claims.get("sub")
    if not sub:
        raise OidcFlowError(INVALID_ID_TOKEN, 400)

    Account = cast(Any, apps.get_model("iam", "ExternalAccount"))
    Credential = cast(Any, apps.get_model("iam", "Credential"))
    email = _claim_email(claims) or ""
    with system_context(reason="iam.oidc.link"), transaction.atomic():
        account = Account.objects.filter(vendor=oauth_client.vendor, external_id=str(sub)).first()
        if account is not None:
            owner = Account.objects.owner_for(account)
            if owner is not None and owner.pk != user.pk:
                raise OidcFlowError("account_already_linked", 409)
        account = Account.objects.link(
            oauth_client.vendor,
            str(sub),
            owner=user,
            email=email,
            identity_claims=claims,
            display_name=_display_name(claims, email),
        )
        Credential.objects.upsert_for_user(
            user,
            oauth_client,
            "oauth",
            tokens,
            external_account=account,
        )
    return cast(models.Model, account)


async def acomplete_link(
    oauth_client: Any,
    user: AbstractBaseUser,
    *,
    code: str,
    state_token: str,
    redirect_uri: str,
) -> models.Model:
    """Async wrapper for ``complete_link`` that keeps sync ORM work thread-sensitive."""

    return await sync_to_async(complete_link, thread_sensitive=True)(
        oauth_client,
        user,
        code=code,
        state_token=state_token,
        redirect_uri=redirect_uri,
    )


def _domain_allowed(oauth_client: Any, email: str | None) -> bool:
    """Return whether ``email`` is allowed by the OAuth client's domain policy."""

    allowed_domains = {
        str(domain).strip().lower()
        for domain in getattr(oauth_client, "allowed_email_domains", []) or []
        if str(domain).strip()
    }
    if not allowed_domains:
        return True
    if not email or "@" not in email:
        return False
    return email.rsplit("@", 1)[1].lower() in allowed_domains


def _email_verified(claims: dict[str, Any]) -> bool:
    """Return whether the IdP asserted the email claim is verified."""

    return claims.get("email_verified") is True


def _find_user_by_email(email: str) -> AbstractBaseUser | None:
    """Return the first user matching ``email`` case-insensitively."""

    UserModel = get_user_model()
    user = UserModel.objects.filter(email__iexact=email).order_by("pk").first()
    return cast(AbstractBaseUser | None, user)


def _create_user_for_identity(email: str, sub: str) -> AbstractBaseUser:
    """Create a non-superuser IAM user for one OIDC identity."""

    UserModel = get_user_model()
    username = _available_username(UserModel, email or f"oidc-{sub}")
    return cast(
        AbstractBaseUser,
        UserModel.objects.create_user(
            username=username,
            email=email,
            password=None,
            is_staff=False,
            is_superuser=False,
        ),
    )


def _available_username(UserModel: Any, seed: str) -> str:
    """Return a unique Django username derived from ``seed``."""

    base = re.sub(r"[^\w.@+-]", "-", seed).strip("-")[:140] or "oidc-user"
    candidate = base
    suffix = 1
    while UserModel.objects.filter(username=candidate).exists():
        suffix_text = f"-{suffix}"
        candidate = f"{base[: 150 - len(suffix_text)]}{suffix_text}"
        suffix += 1
    return candidate


def _display_name(claims: dict[str, Any], email: str) -> str:
    """Return the best display label from verified identity claims."""

    for key in ("name", "preferred_username", "given_name"):
        value = claims.get(key)
        if value:
            return str(value)
    return email


def _claim_email(claims: dict[str, Any]) -> str | None:
    """Return the email claim when present."""

    value = claims.get("email")
    return str(value) if value else None


def _validate_state_record(oauth_client: Any, record: state.StateRecord, redirect_uri: str) -> None:
    """Fail closed when a consumed state record does not match this flow."""

    oauth_client_id = str(getattr(oauth_client, "sqid", getattr(oauth_client, "pk", "")))
    if record.oauth_client_id != oauth_client_id or record.redirect_uri != redirect_uri:
        raise OidcFlowError(INVALID_STATE, 400)
