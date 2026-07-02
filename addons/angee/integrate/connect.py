"""OAuth account-connect completion: link an external account and store its credential.

The connect counterpart to OIDC login. It authenticates *out* to a provider on
behalf of the already-signed-in user, labels their ``ExternalAccount`` from the
userinfo profile, and stores the OAuth ``Credential`` the integration runtime
acts with. No ID token, no user resolution, no session — that is login, and lives
in ``iam_integrate_oidc``. Works for any ``OAuthClient`` (an OIDC refinement is
not required).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import AbstractBaseUser
from django.db import models, transaction
from rebac import system_context

from angee.integrate.credentials import CredentialKind
from angee.integrate.oauth import flow
from angee.integrate.oauth.client import OAuthClientProtocol
from angee.integrate.oauth.errors import (
    EXTERNAL_ACCOUNT_RESOLUTION_FAILED,
    INVALID_STATE,
    OAuthFlowError,
)
from angee.integrate.oauth.state import StateFlow, StateRecord


@dataclass(frozen=True, slots=True)
class AccountConnectCompletion:
    """Connected account, captured credential, and provider profile claims."""

    account: models.Model
    credential: models.Model
    user: AbstractBaseUser
    claims: dict[str, Any]
    next_path: str
    integration_id: str = ""


def enabled_oauth_client_from_hint(
    hint: Any,
    *,
    owner_label: str,
    reason: str,
    vendor_slug: str = "",
) -> Any:
    """Resolve an enabled OAuth client from a backend-declared client hint."""

    raw_hint = str(hint or "").strip()
    if not raw_hint:
        raise OAuthFlowError(
            "oauth_client_not_connectable",
            400,
            f"{owner_label} has no OAuth client.",
        )
    slug = raw_hint.format(vendor=vendor_slug)
    OAuthClient = cast(Any, apps.get_model("integrate", "OAuthClient"))
    with system_context(reason=reason):
        oauth_client = OAuthClient.objects.enabled_for_slug(slug)
    if oauth_client is None:
        raise OAuthFlowError(
            "oauth_client_not_connectable",
            400,
            f"{owner_label} has no enabled OAuth client.",
        )
    return oauth_client


def complete_account_connect(
    oauth_client: Any,
    *,
    code: str,
    state_token: str,
    redirect_uri: str,
) -> AccountConnectCompletion:
    """Complete an authenticated OAuth account-connect redirect.

    The connecting user is the one captured in state when the flow started; the
    account is labelled from the provider userinfo and its OAuth credential stored
    under ``(user, provider)``.
    """

    record = flow.consume_validated_state(
        oauth_client,
        state_token,
        redirect_uri,
        expected_flow=StateFlow.CONNECT,
    )
    user = _state_user(record)
    protocol = OAuthClientProtocol(oauth_client)
    tokens = protocol.exchange_code(
        code=code,
        redirect_uri=redirect_uri,
        code_verifier=record.code_verifier,
        state=state_token,
    )
    claims = protocol.fetch_userinfo(str(tokens.get("access_token", "") or ""))
    external_id = oauth_client.external_id_from_claims(claims)
    if not external_id:
        raise OAuthFlowError(EXTERNAL_ACCOUNT_RESOLUTION_FAILED, 400)
    return complete_external_account_link(
        oauth_client,
        user=user,
        external_id=external_id,
        tokens=tokens,
        claims=claims,
        next_path=record.next_path or "/",
        integration_id=record.integration_id,
        reason="integrate.oauth.connect",
    )


def complete_external_account_link(
    oauth_client: Any,
    *,
    user: AbstractBaseUser,
    external_id: str,
    tokens: dict[str, Any],
    claims: dict[str, Any],
    next_path: str = "/",
    integration_id: str = "",
    reason: str = "integrate.oauth.connect",
) -> AccountConnectCompletion:
    """Link an external account to ``user`` and store its OAuth credential."""

    email = oauth_client.email_from_claims(claims) or ""
    Account = cast(Any, apps.get_model("integrate", "ExternalAccount"))
    Credential = cast(Any, apps.get_model("integrate", "Credential"))
    with system_context(reason=reason), transaction.atomic():
        account = Account.objects.filter(oauth_client=oauth_client, external_id=external_id).first()
        if account is not None:
            owner = Account.objects.owner_for(account)
            if owner is not None and owner.pk != user.pk:
                raise OAuthFlowError("account_already_linked", 409)
        account = Account.objects.link(
            oauth_client,
            external_id,
            owner=user,
            email=email,
            identity_claims=claims,
            display_name=oauth_client.display_name_from_claims(claims, email),
            avatar_url=oauth_client.avatar_url_from_claims(claims),
        )
        credential = Credential.objects.upsert_for_user(
            user,
            oauth_client,
            CredentialKind.OAUTH,
            tokens,
            external_account=account,
        )
        account.credential = credential
        account.save(update_fields=["credential", "updated_at"])
    return AccountConnectCompletion(
        account=cast(models.Model, account),
        credential=cast(models.Model, credential),
        user=user,
        claims=claims,
        next_path=next_path or "/",
        integration_id=integration_id,
    )


def _state_user(record: StateRecord) -> AbstractBaseUser:
    """Return the user captured when the authenticated connect flow started."""

    if not record.user_id:
        raise OAuthFlowError(INVALID_STATE, 400)
    user_model = get_user_model()
    with system_context(reason="integrate.oauth.connect.user"):
        try:
            user = cast(Any, user_model.objects).get(pk=record.user_id)
        except user_model.DoesNotExist as exc:
            raise OAuthFlowError(INVALID_STATE, 400) from exc
    return cast(AbstractBaseUser, user)
