"""OAuth account-connect completion: link an external account and store its credential.

The connect counterpart to OIDC login. It authenticates *out* to a provider on
behalf of the already-signed-in user, labels their ``ExternalAccount`` from the
userinfo profile (or the verified ID token when the grant cannot read userinfo),
and stores the OAuth ``Credential`` the integration runtime acts with. No user
resolution, no session — that is login, and lives in ``iam_integrate_oidc``.
Works for any ``OAuthClient`` (an OIDC login refinement is not required).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import AbstractBaseUser
from django.core.exceptions import ValidationError
from django.db import models
from django.http import HttpRequest
from rebac import system_context

from angee.integrate.oauth import flow
from angee.integrate.oauth.client import OAuthClientProtocol
from angee.integrate.oauth.errors import (
    ACCOUNT_ALREADY_LINKED,
    CONSENT_REQUIRED,
    EXTERNAL_ACCOUNT_RESOLUTION_FAILED,
    INVALID_STATE,
    REDIRECT_URI_REQUIRED,
    ConsentRequired,
    OAuthFlowError,
)
from angee.integrate.oauth.state import StateFlow, StateRecord

if TYPE_CHECKING:
    from angee.integrate.models import Credential as CredentialModel
    from angee.integrate.models import Integration, OAuthClient


def connect_integration_target(
    integration: Integration,
    oauth_client: OAuthClient,
    *,
    user: AbstractBaseUser,
    request: HttpRequest,
    redirect_uri: str,
    next_path: str,
) -> flow.OAuthStart | None:
    """Attach the bound account's live credential, or start the owner's OAuth flow.

    Only the credential of the account the integration is connected as is
    reused, and only when it holds the capability's ``oauth_scopes``; that
    account hints the flow, whose scopes keep what its grant already holds. An
    integration not yet connected as an account always starts the flow. None
    means attachment completed. OAuthStart is the existing native browser flow
    contract; transports own authentication/exposure and result projection.
    """

    integration.require_connect(user)
    oauth_client.require_configured()
    oauth_scopes = integration.connection_oauth_scopes
    account = integration.bound_account(oauth_client.pk)
    granted: tuple[str, ...] = ()
    if account is not None:
        credential_model = cast("type[CredentialModel]", apps.get_model("integrate", "Credential"))
        credential = credential_model.objects.live_oauth_for_user(
            user, oauth_client, external_account=account, oauth_scopes=oauth_scopes,
        )
        if credential is not None:
            integration.attach_credential(credential)
            return None
        with system_context(reason="integrate.oauth.connect.granted_scopes"):
            bound_credential = account.credential
        granted = () if bound_credential is None else tuple(bound_credential.granted_scopes or ())
    if not redirect_uri:
        raise OAuthFlowError(REDIRECT_URI_REQUIRED, 400, "OAuth redirect URI is required.")
    return flow.start(
        request,
        oauth_client,
        redirect_uri,
        user_id=str(user.pk),
        next_path=flow.coerce_next_path(next_path, request),
        flow=StateFlow.CONNECT,
        integration_id=str(integration.sqid),
        oauth_scopes=oauth_scopes,
        granted_scopes=granted,
        login_hint="" if account is None else account.email,
    )


@dataclass(frozen=True, slots=True)
class AccountConnectCompletion:
    """Connected account, captured credential, and provider profile claims."""

    account: models.Model
    credential: models.Model
    user: AbstractBaseUser
    claims: dict[str, Any]
    next_path: str
    integration_id: str = ""


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
    under ``(user, provider, account)``. A grant that cannot read userinfo
    identifies its account from token-response metadata or the code exchange's
    verified ID token. The grant checks are ``complete_external_account_link``'s.
    """

    record = flow.consume_validated_state(
        oauth_client,
        state_token,
        redirect_uri,
        expected_flow=StateFlow.CONNECT,
    )
    user = _state_user(record)
    protocol = OAuthClientProtocol(oauth_client)
    tokens, claims, id_token = protocol.exchange_identity(
        code=code,
        redirect_uri=redirect_uri,
        code_verifier=record.code_verifier,
        state=state_token,
    )
    external_id = oauth_client.external_id_from_claims(claims)
    if not external_id:
        # Restricted tokens may be unable to call userinfo. Anthropic's one-year
        # inference-only exchange returns the stable ``account`` mapping in the
        # token response instead, so use that provider-authenticated metadata as
        # a fallback without persisting it as credential secret material.
        token_claims = protocol.token_response_claims
        external_id = oauth_client.external_id_from_claims(token_claims)
        if external_id:
            claims = {**token_claims, **claims}
    if not external_id and id_token:
        claims = protocol.verified_claims(id_token, claims)
        external_id = oauth_client.external_id_from_claims(claims)
    if not external_id:
        raise OAuthFlowError(EXTERNAL_ACCOUNT_RESOLUTION_FAILED, 400)
    defaults = set(oauth_client.default_scope_values)
    return complete_external_account_link(
        oauth_client,
        user=user,
        external_id=external_id,
        tokens=tokens,
        claims=claims,
        next_path=record.next_path or "/",
        integration_id=record.integration_id,
        reason="integrate.oauth.connect",
        flow_record=record,
        required_scopes=[scope for scope in record.scopes if scope not in defaults],
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
    flow_record: StateRecord | None = None,
    required_scopes: Iterable[str] = (),
) -> AccountConnectCompletion:
    """Link an external account to ``user`` and store its OAuth credential (``connect_oauth``).

    A browser flow passes its consumed ``flow_record``: its first attempt must
    bring the refresh token a refreshable client needs, or ``ConsentRequired``
    asks the caller to restart it with consent; a consent attempt stores what the
    provider gave.
    """

    Account = cast(Any, apps.get_model("integrate", "ExternalAccount"))
    try:
        account = Account.objects.connect_oauth(
            oauth_client,
            user=user,
            external_id=external_id,
            tokens=tokens,
            claims=claims,
            reason=reason,
            require_refresh_token=flow_record is not None and not flow_record.consent,
            required_scopes=required_scopes,
        )
    except OAuthFlowError as error:
        if flow_record is not None and error.code == CONSENT_REQUIRED:
            raise ConsentRequired(flow_record) from error
        raise
    except ValidationError as error:
        if error.code == ACCOUNT_ALREADY_LINKED:
            raise OAuthFlowError(ACCOUNT_ALREADY_LINKED, 409) from error
        raise
    return AccountConnectCompletion(
        account=cast(models.Model, account),
        credential=cast(models.Model, account.credential),
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
    try:
        return cast(AbstractBaseUser, cast(Any, user_model.objects).get_for_session(record.user_id))
    except user_model.DoesNotExist as exc:
        raise OAuthFlowError(INVALID_STATE, 400) from exc
