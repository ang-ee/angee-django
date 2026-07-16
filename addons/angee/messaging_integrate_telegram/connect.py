"""Telegram channel creation and application-key attachment."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from rebac import system_context

from angee.integrate.credentials import CredentialKind
from angee.integrate.models import IntegrationLifecycle
from angee.messaging.connect import resume_channel_pairing
from angee.messaging_integrate_telegram.backend import TelegramChannelBackend

Channel = apps.get_model("messaging", "Channel")
Credential = apps.get_model("integrate", "Credential")
Vendor = apps.get_model("integrate", "Vendor")

_TELEGRAM_SLUG = TelegramChannelBackend.key


def create_telegram_channel(
    user: Any,
    *,
    name: str,
    api_id: str,
    api_hash: str,
) -> Any:
    """Create a connected Telegram channel and start QR pairing.

    Telegram requires one API id per phone number. Its published sample id is
    unsuitable for bridges and fails with ``API_ID_PUBLISHED_FLOOD``; operators
    must copy their own ``api_id`` and ``api_hash`` from my.telegram.org.
    """

    display_name = str(name).strip()
    if not display_name:
        raise ValueError("A channel name is required.")
    try:
        normalized_api_id = int(api_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Telegram API ID must be an integer.") from exc
    with system_context(reason="messaging_integrate_telegram.create"), transaction.atomic():
        channel = Channel.objects.create(
            vendor=_telegram_vendor(),
            owner=user,
            backend_class=_TELEGRAM_SLUG,
            display_name=display_name,
            lifecycle=IntegrationLifecycle.DISCONNECTED,
            created_by_id=user.pk,
        )
        credential = Credential.objects.create_local_credential(
            user,
            kind=CredentialKind.APP_KEYS,
            name=f"Telegram - {display_name} ({channel.sqid})",
            material={"app_id": str(normalized_api_id), "app_secret": api_hash},
        )
        channel.connect(credential=credential)
    resume_channel_pairing(channel)
    return channel


def _telegram_vendor() -> Any:
    """Return the addon-seeded Telegram vendor row, failing clearly on drift."""

    try:
        return Vendor.objects.get(slug=_TELEGRAM_SLUG)
    except Vendor.DoesNotExist as exc:
        raise ImproperlyConfigured(
            "Telegram vendor is missing. Load messaging_integrate_telegram resources "
            "before connecting Telegram channels."
        ) from exc
