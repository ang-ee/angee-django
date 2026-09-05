"""Settings fragments contributed when the messaging addon is installed."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import environ

SETTINGS = {
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
    # Django supplies an implicit localhost SMTP backend when EMAIL_BACKEND is
    # absent. Outbound messaging must not mistake that floor for an explicitly
    # configured delivery transport.
    "ANGEE_EMAIL_DELIVERY_CONFIGURED": False,
}


def settings(namespace: Mapping[str, Any]) -> dict[str, Any]:
    """Derive mail-provider defaults from the host environment and transport."""

    contributed: dict[str, Any] = {
        "ANGEE_EMAIL_DELIVERY_CONFIGURED": bool(namespace.get("EMAIL_BACKEND")),
    }
    if "ANYMAIL" in os.environ:
        contributed["ANYMAIL"] = environ.Env().json("ANYMAIL")
    contributed.update({name: value for name, value in sorted(os.environ.items()) if name.startswith("ANYMAIL_")})
    return contributed
