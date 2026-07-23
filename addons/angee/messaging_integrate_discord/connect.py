"""Discord bot-token channel creation and credential attachment."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.db import transaction
from django.views.decorators.debug import sensitive_variables
from rebac import system_context

from angee.integrate.credentials import CredentialKind
from angee.messaging.connect import resume_channel_pairing
from angee.messaging_integrate_discord.backend import DiscordChannelBackend

Channel = apps.get_model("messaging", "Channel")
Credential = apps.get_model("integrate", "Credential")


@sensitive_variables("token")
def create_discord_channel(user: Any, name: str, token: str) -> Any:
    """Atomically persist one bot token and channel, then start Gateway login."""

    clean_name = str(name or "").strip()
    clean_token = str(token or "").strip()
    if not clean_name:
        raise ValueError("A Discord connection name is required.")
    if not clean_token:
        raise ValueError("A Discord bot token is required.")

    with system_context(reason="messaging_integrate_discord.create"), transaction.atomic():
        credential = Credential.objects.create_local_credential(
            user,
            kind=CredentialKind.STATIC_TOKEN,
            name=f"Discord — {clean_name}",
            material={"api_key": clean_token},
        )
        channel = Channel.objects.create_disconnected(
            user,
            name=clean_name,
            backend_class=DiscordChannelBackend.key,
        )
        channel.connect(credential=credential)
    resume_channel_pairing(channel)
    return channel


def discord_bot_token(credential: Any) -> str:
    """Return the bot token owned by one STATIC_TOKEN credential."""

    if credential.kind != CredentialKind.STATIC_TOKEN:
        raise ValueError("A Discord channel requires a static-token credential.")
    token = str(credential.reveal().get("api_key") or "").strip()
    if not token:
        raise ValueError("The Discord credential requires a bot token.")
    return token
