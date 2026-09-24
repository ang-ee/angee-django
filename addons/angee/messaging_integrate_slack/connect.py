"""Slack channel connection service with network preflight before persistence."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.db import transaction
from rebac import system_context
from slack_sdk.errors import SlackApiError

from angee.integrate.credentials import CredentialKind
from angee.messaging_integrate_slack.backend import SlackChannelBackend
from angee.messaging_integrate_slack.identity import response_data

Channel = apps.get_model("messaging", "Channel")
Credential = apps.get_model("integrate", "Credential")

_CREDENTIAL_NAME_MAX_LENGTH = 255


def create_slack_channel(user: Any, *, name: str, token: str) -> Any:
    """Probe a Slack user token, then atomically persist its workspace channel."""

    clean_token = str(token).strip()
    if not clean_token:
        raise ValueError("A Slack User OAuth token is required.")
    requested_name = str(name).strip()
    probe_values = {
        "kind": CredentialKind.STATIC_TOKEN,
        "name": "Slack connection probe",
        "material": {"api_key": clean_token},
    }

    # The SDK call is deliberately outside both system_context and transaction:
    # failed auth must leave no credential/channel rows and hold no database lock.
    probe_credential = Credential.objects.prepare_local_credential(user, **probe_values)
    try:
        response = SlackChannelBackend.client_class(token=probe_credential.secret_value()).auth_test()
    except SlackApiError as error:
        raise ValueError("Slack rejected the token or it lacks the required scopes.") from error
    data = response_data(response)
    team_id = str(data.get("team_id") or "").strip()
    own_id = str(data.get("user_id") or "").strip()
    workspace_name = str(data.get("team") or requested_name or team_id).strip()
    if not team_id or not own_id:
        raise ValueError("Slack auth.test returned no workspace or authenticated user id.")

    credential_name = _credential_name(requested_name or workspace_name, team_id)
    with system_context(reason="messaging_integrate_slack.create"), transaction.atomic():
        credential = Credential.objects.create_local_credential(
            user,
            kind=CredentialKind.STATIC_TOKEN,
            name=credential_name,
            material={"api_key": clean_token},
        )
        channel = Channel.objects.create_disconnected(
            user,
            name=workspace_name,
            backend_class=SlackChannelBackend.key,
            subscription_state={"team_id": team_id, "own_id": own_id},
        )
        channel.connect(credential=credential)
    return channel


def _credential_name(name: str, team_id: str) -> str:
    """Return a stable workspace-scoped local credential name."""

    suffix = f" ({team_id})"
    prefix = f"Slack — {name}"
    return f"{prefix[: _CREDENTIAL_NAME_MAX_LENGTH - len(suffix)]}{suffix}"
