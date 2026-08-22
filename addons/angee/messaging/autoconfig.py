"""Settings fragments contributed when the messaging addon is installed."""

from __future__ import annotations

SETTINGS = {
    # Channel backends a ``messaging.Channel`` row may select. ``manual`` is the
    # neutral null-object (no source; ``ImplClassField`` requires a non-empty
    # registry). Source addons add their own with a yamlconf dotted key, e.g.
    # ``"ANGEE_CHANNEL_BACKEND_CLASSES.imap"`` from ``messaging_integrate_imap``.
    "ANGEE_CHANNEL_BACKEND_CLASSES": {
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
}
