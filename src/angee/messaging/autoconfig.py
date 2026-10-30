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
        "ANGEE_CHANNEL_BACKEND_CLASSES": dict[str, str],
        "ANGEE_WEBFORM_MAX_BODY_BYTES": int,
        "ANGEE_WEBFORM_RATE_LIMIT_BURST": int,
        "ANGEE_WEBFORM_RATE_LIMIT_WINDOW": int,
        "ANGEE_WEBFORM_HONEYPOT_FIELD": str,
        "ANGEE_WEBFORM_TOKEN_HOOK": str,
        "ANGEE_EMAIL_DELIVERY_CONFIGURED": bool,
        "ANGEE_MESSAGING_PROTECTED_LOCAL_KEYS": list[str],
    },
)

SETTINGS: _Settings = {
    "CELERY_BEAT_SCHEDULE:append": {
        RELEASE_MESSAGES_TASK: {"task": RELEASE_MESSAGES_TASK, "schedule": 60.0},
    },
    "ANGEE_PARTIES_SHARED_SENDER_PROVIDERS:append": ["angee.messaging.evidence.shared_senders"],
    "ANGEE_PARTIES_SIGNING_PROVIDERS:append": ["angee.messaging.evidence.signings"],
    "ANGEE_TIMELINE_KINDS:append": ["angee.messaging.timeline.timeline_kinds"],
    # Channel backends a ``messaging.Channel`` row may select. ``manual`` is the
    # neutral null-object (no source; ``ImplClassField`` requires a non-empty
    # registry). Source addons add their own with a yamlconf dotted key, e.g.
    # ``"ANGEE_CHANNEL_BACKEND_CLASSES.imap"`` from ``messaging_integrate_imap``.
    "ANGEE_CHANNEL_BACKEND_CLASSES": {
        "email": "angee.messaging.email.AnymailEmailChannelBackend",
        "manual": "angee.messaging.backends.ManualChannelBackend",
        "webform": "angee.messaging.backends.WebformChannelBackend",
    },
    # Public form ingress remains safe with no project settings. Deployments may
    # lower these bounds or contribute a dotted token hook; the reusable guard
    # fails closed if its cache/token dependency fails.
    "ANGEE_WEBFORM_MAX_BODY_BYTES": 65_536,
    "ANGEE_WEBFORM_RATE_LIMIT_BURST": 10,
    "ANGEE_WEBFORM_RATE_LIMIT_WINDOW": 60,
    "ANGEE_WEBFORM_HONEYPOT_FIELD": "_website",
    "ANGEE_WEBFORM_TOKEN_HOOK": "",
    # Only an explicitly declared native default mailer configures delivery;
    # Django's legacy implicit localhost SMTP backend is not a transport choice.
    "ANGEE_EMAIL_DELIVERY_CONFIGURED": False,
    # Addons append provenance keys written by their trusted local owners.
    "ANGEE_MESSAGING_PROTECTED_LOCAL_KEYS": [
        "delivery_token", "delivery_conflict", "delivery_error", "delivery_attempts",
    ],
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
