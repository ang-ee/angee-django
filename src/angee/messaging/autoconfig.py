"""Settings fragments contributed when the messaging addon is installed."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, TypedDict

from angee.messaging.constants import RELEASE_MESSAGES_TASK

_Settings = TypedDict(
    "_Settings",
    {
        "CELERY_BEAT_SCHEDULE:append": dict[str, dict[str, object]],
        "ANGEE_PARTIES_SHARED_SENDER_PROVIDERS:append": list[str],
        "ANGEE_PARTIES_SIGNING_PROVIDERS:append": list[str],
        "ANGEE_TIMELINE_KINDS:append": list[str],
        "ANGEE_GRAPHQL_RELATED_COUNTS:append": list[str],
        "ANGEE_CHANNEL_BACKEND_CLASSES": dict[str, str],
        "ANGEE_IMPL_KEY_REMEDIES.ANGEE_CHANNEL_BACKEND_CLASSES.webform": str,
        "ANGEE_EMAIL_DELIVERY_CONFIGURED": bool,
        "ANGEE_MESSAGING_PROTECTED_LOCAL_KEYS": list[str],
        "ANGEE_MESSAGING_MAX_RECIPIENTS": int,
        "ANGEE_MESSAGING_MAX_ATTACHMENTS": int,
    },
)

SETTINGS: _Settings = {
    "CELERY_BEAT_SCHEDULE:append": {
        RELEASE_MESSAGES_TASK: {"task": RELEASE_MESSAGES_TASK, "schedule": 60.0},
    },
    "ANGEE_PARTIES_SHARED_SENDER_PROVIDERS:append": ["angee.messaging.evidence.shared_senders"],
    "ANGEE_PARTIES_SIGNING_PROVIDERS:append": ["angee.messaging.evidence.signings"],
    "ANGEE_TIMELINE_KINDS:append": ["angee.messaging.timeline.timeline_kinds"],
    "ANGEE_GRAPHQL_RELATED_COUNTS:append": ["angee.messaging.schema.related_counts"],
    # Channel backends a ``messaging.Channel`` row may select. ``manual`` is the
    # neutral null-object (no source; ``ImplClassField`` requires a non-empty
    # registry). Source addons add their own with a yamlconf dotted key, e.g.
    # ``"ANGEE_CHANNEL_BACKEND_CLASSES.imap"`` from ``messaging_integrate_imap``.
    "ANGEE_CHANNEL_BACKEND_CLASSES": {
        "email": "angee.messaging.email.AnymailEmailChannelBackend",
        "manual": "angee.messaging.backends.ManualChannelBackend",
    },
    # A channel that stores ``webform`` needs the web forms bridge, which registers
    # that backend; ``angee.E052`` gives this to a host that holds one without it.
    "ANGEE_IMPL_KEY_REMEDIES.ANGEE_CHANNEL_BACKEND_CLASSES.webform": (
        "Install angee.webforms_messaging, which registers the webform channel backend."
    ),
    # Only an explicitly declared native default mailer configures delivery;
    # Django's legacy implicit localhost SMTP backend is not a transport choice.
    "ANGEE_EMAIL_DELIVERY_CONFIGURED": False,
    # Addons append provenance keys written by their trusted local owners.
    "ANGEE_MESSAGING_PROTECTED_LOCAL_KEYS": [
        "delivery_token", "delivery_conflict", "delivery_error", "delivery_attempts",
    ],
    # The most addresses one composed message takes across to, cc and bcc: the
    # per-message limit of the strictest common mail provider. Each new address
    # costs one transaction about 34 statements; bulk sending is a path of its own.
    "ANGEE_MESSAGING_MAX_RECIPIENTS": 50,
    # The most files one composed message attaches, about 17 statements each.
    # Providers bound a message's attachments by total size, not count, so this
    # bounds the transaction: at both bounds a call stays near 2,000 statements.
    "ANGEE_MESSAGING_MAX_ATTACHMENTS": 20,
}


def settings(namespace: Mapping[str, Any]) -> dict[str, Any]:
    """Derive mail-provider defaults from the host environment and transport."""

    contributed: dict[str, Any] = {
        "ANGEE_EMAIL_DELIVERY_CONFIGURED": "default" in namespace.get("MAILERS", {}),
    }
    if "ANYMAIL" in namespace:
        anymail = namespace["ANYMAIL"]
        contributed["ANYMAIL"] = json.loads(anymail) if isinstance(anymail, str) else anymail
    contributed.update({name: value for name, value in sorted(namespace.items()) if name.startswith("ANYMAIL_")})
    return contributed
