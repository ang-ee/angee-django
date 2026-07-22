"""Telegram channel creation and application-key attachment."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.db import transaction
from rebac import system_context

from angee.integrate.credentials import CredentialKind
from angee.integrate.models import IntegrationLifecycle
from angee.messaging.connect import resume_channel_pairing
from angee.messaging_integrate_telegram.backend import TelegramChannelBackend

Channel = apps.get_model("messaging", "Channel")
Vendor = apps.get_model("integrate", "Vendor")

_TELEGRAM_SLUG = TelegramChannelBackend.key


def create_telegram_channel(
    user: Any,
    *,
    name: str,
    credential: Any,
) -> Any:
    """Create a connected Telegram channel on an app-key credential, then start QR pairing.

    The credential carries an ``api_id``/``api_hash`` registered at
    my.telegram.org. Telegram's published sample id is unsuitable for bridges and
    fails with ``API_ID_PUBLISHED_FLOOD``, so the registration must be the
    operator's own. It identifies the *application*, not a phone number, and one
    registration drives any number of accounts — so channels select a shared
    credential rather than each minting a copy.
    """

    display_name = str(name).strip()
    if not display_name:
        raise ValueError("A channel name is required.")
    if credential.kind != CredentialKind.APP_KEYS:
        raise ValueError("A Telegram channel requires an app-keys credential.")
    telegram_app_keys(credential)
    with system_context(reason="messaging_integrate_telegram.create"), transaction.atomic():
        channel = Channel.objects.create(
            vendor=Vendor.objects.seeded(_TELEGRAM_SLUG),
            owner=user,
            backend_class=_TELEGRAM_SLUG,
            display_name=display_name,
            lifecycle=IntegrationLifecycle.DISCONNECTED,
            created_by_id=user.pk,
        )
        channel.connect(credential=credential)
    resume_channel_pairing(channel)
    return channel


def telegram_app_keys(credential: Any) -> tuple[int, str]:
    """Return one credential's Telegram ``(api_id, api_hash)``, or raise.

    Telegram's ``api_id`` is an integer, while ``app_keys`` material is generic
    strings — a vendor fact this addon owns, because the credential is minted
    through integrate's kind-generic create. The connect action reads the pair to
    fail before a channel persists, and the session reads it to build its client,
    so the rule states itself once.
    """

    material = credential.reveal()
    try:
        api_id = int(str(material.get("app_id") or ""))
    except ValueError as exc:
        raise ValueError("The Telegram credential has an invalid app_id.") from exc
    api_hash = str(material.get("app_secret") or "")
    if not api_id or not api_hash:
        raise ValueError("The Telegram credential requires app_id and app_secret.")
    return api_id, api_hash
