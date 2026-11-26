"""Settings fragments contributed when the messaging addon is installed."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any, TypedDict

from django.core.exceptions import ImproperlyConfigured
from django.core.mail.handler import DEFAULT_MAILER_ALIAS, DEFAULT_MAILER_BACKEND
from django.utils.log import DEFAULT_LOGGING

from angee.messaging.constants import (
    RELEASE_MESSAGES_TASK,
    SYSTEM_CHANNEL_BACKEND,
    SYSTEM_MAILER_BACKEND,
    TRANSPORT_MAILER_ALIAS,
)


def _error_mail_logging() -> dict[str, Any]:
    """Django's ``DEFAULT_LOGGING``, with error mail sent through the transport mailer.

    One change: ``mail_admins`` names ``using="transport"``, so an error report never
    reaches the system mailer, which writes to the database the error may be about;
    with no transport mailer Django drops the report. Every logger, handler and
    formatter keeps Django's own configuration, so configuring them resets none.
    """

    logging = copy.deepcopy(DEFAULT_LOGGING)
    logging["handlers"]["mail_admins"]["using"] = TRANSPORT_MAILER_ALIAS
    return logging


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
    # registry); ``system`` is the system channel's, which is system-only. Source addons
    # add their own with a yamlconf dotted key, e.g.
    # ``"ANGEE_CHANNEL_BACKEND_CLASSES.imap"`` from ``messaging_integrate_imap``.
    "ANGEE_CHANNEL_BACKEND_CLASSES": {
        "email": "angee.messaging.email.AnymailEmailChannelBackend",
        "manual": "angee.messaging.backends.ManualChannelBackend",
        SYSTEM_CHANNEL_BACKEND: "angee.messaging.email.SystemChannelBackend",
    },
    # A channel that stores ``webform`` needs the web forms bridge, which registers
    # that backend; ``angee.E052`` gives this to a host that holds one without it.
    "ANGEE_IMPL_KEY_REMEDIES.ANGEE_CHANNEL_BACKEND_CLASSES.webform": (
        "Install angee.webforms_messaging, which registers the webform channel backend."
    ),
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

_OWN_DEFAULT = (
    "MAILERS[\"default\"] is this deployment's own mailer ({backend}). With angee.messaging installed, Django's "
    "default mailer is messaging's system mailer: it records every email on the system channel, and messaging "
    "sends it through MAILERS[\"transport\"]. {remedy}"
)
_RENAME = (
    "Rename MAILERS.default to MAILERS.transport where the deployment states MAILERS, keeping its BACKEND and OPTIONS."
)
_REMOVE = "Remove MAILERS.default where the deployment states MAILERS."
_LOOP = (
    "MAILERS[\"transport\"] is messaging's system mailer, which records mail and sends none. Point "
    "MAILERS.transport at the mailer that sends: Django's SMTP backend or an Anymail backend."
)


def settings(namespace: Mapping[str, Any]) -> dict[str, Any]:
    """Make the system mailer Django's default mailer, refusing an authored mailer setting that collides with it.

    Messaging owns ``MAILERS["default"]``: a deployment that states its own, or a
    ``transport`` that is the system mailer, is refused at settings load with the
    exact edit to make. Otherwise the default is contributed when the deployment
    states none, and the mail-provider values are derived from the host
    environment. Django itself refuses legacy ``EMAIL_*`` settings beside ``MAILERS``.

    ``LOGGING`` is contributed only when nothing states one: Django's default, with
    error mail through the transport (:func:`_error_mail_logging`), marked raw so the
    settings loader keeps the ``django.server`` formatter's ``{server_time}`` as text.
    A host that states its own keeps it (``messaging.W001`` reports one whose error
    mail would reach the system mailer).
    """

    mailers = namespace.get("MAILERS") or {}
    default, transport = mailers.get(DEFAULT_MAILER_ALIAS), mailers.get(TRANSPORT_MAILER_ALIAS)
    if default is not None and (backend := default.get("BACKEND", DEFAULT_MAILER_BACKEND)) != SYSTEM_MAILER_BACKEND:
        raise ImproperlyConfigured(_OWN_DEFAULT.format(backend=backend, remedy=_REMOVE if transport else _RENAME))
    if transport is not None and transport.get("BACKEND", DEFAULT_MAILER_BACKEND) == SYSTEM_MAILER_BACKEND:
        raise ImproperlyConfigured(_LOOP)
    contributed: dict[str, Any] = {}
    if default is None:
        contributed[f"MAILERS.{DEFAULT_MAILER_ALIAS}"] = {"BACKEND": SYSTEM_MAILER_BACKEND}
    if "LOGGING" not in namespace:
        contributed["LOGGING:raw"] = True
        contributed["LOGGING"] = _error_mail_logging()
    if "ANYMAIL" in namespace:
        anymail = namespace["ANYMAIL"]
        contributed["ANYMAIL"] = json.loads(anymail) if isinstance(anymail, str) else anymail
    contributed.update({name: value for name, value in sorted(namespace.items()) if name.startswith("ANYMAIL_")})
    return contributed
