"""Messaging's system checks: its seals (``messaging.E003``), its transport (``E004``) and its error mail (``W001``)."""

from __future__ import annotations

from collections.abc import Sequence
from functools import partial

from django.apps import AppConfig
from django.conf import settings
from django.core import checks
from django.core.checks.mail import NON_PRODUCTION_EMAIL_BACKENDS
from django.core.mail import mailers
from django.core.mail.handler import DEFAULT_MAILER_ALIAS, DEFAULT_MAILER_BACKEND

from angee.base.checks import check_sealed_permissions
from angee.messaging.constants import SYSTEM_MAILER_BACKEND, TRANSPORT_MAILER_ALIAS

DERIVED_READS = {"messaging/thread": ("read", "read_inbox"), "messaging/message": ("read", "read_inbox")}
"""The reads messaging derives from its extension points, which fragments never extend.

Fragments widen ``messaging/thread#read_granted`` with grants that hold for any
conversation, which both reads take, and ``messaging/thread#chatter_read`` with a
record's read of its own chatter, which only ``read`` takes; message grants go to
``messaging/message#read_granted``. An arm unioned straight into a derived read
reaches one of the two reads and not the other: the inbox would drop a grant
generic reads keep, or keep one they refuse.
"""

SEALED = {
    "messaging/channel": ("read", "reply", "write", "annotate", "delete"),
    "messaging/thread": ("share", "delete", "annotate"),
    "messaging/message": ("write", "delete", "annotate"),
}
"""The permissions that subtract the system channel's seal, which fragments never extend.

On the system channel (backend ``system``) each admits administrators alone; a
system thread's ``share`` and a system message's ``write`` admit nobody, so no verb
grants on either; their deletes keep the administrator arm. An arm unioned into
one would reach system mail past the seal. Fragments widen
``integrate/integration#read`` and ``#write`` instead, which the channel's seal
subtracts. The thread's ``write``, which fragments extend, subtracts the seal from
its own arms, administrators' included, and so is not sealed here.
"""

check_messaging_seals = partial(
    check_sealed_permissions, "messaging", {
        resource_type: (*DERIVED_READS.get(resource_type, ()), *SEALED.get(resource_type, ()))
        for resource_type in sorted({*DERIVED_READS, *SEALED})
    }, "messaging.E003",
    "Union the fragment's arm into an extension point the sealed expression composes, "
    "or grant the need through the fragment's own record's read.",
)
"""``messaging.E003``: the derived thread and message reads, and the permissions the system channel's seal guards."""


def check_transport_mailer(
    app_configs: Sequence[AppConfig] | None = None, **kwargs: object,
) -> list[checks.CheckMessage]:
    """``messaging.E004``: the transport mailer is a development-only backend, so messaging's mail is never sent.

    Django's ``mail.E001`` inspects only ``MAILERS["default"]``, which is messaging's
    system mailer. A deployment with no transport mailer is not refused here: its
    mail is refused where it is sent.
    """

    del app_configs, kwargs
    transport = mailers.settings.get(TRANSPORT_MAILER_ALIAS)
    if transport is None:
        return []
    backend = transport.get("BACKEND", DEFAULT_MAILER_BACKEND)
    if backend not in NON_PRODUCTION_EMAIL_BACKENDS:
        return []
    return [checks.Error(
        f'MAILERS["transport"] is a development-only email backend ({backend}), so messaging\'s mail is not sent.',
        hint="Point MAILERS.transport at a production backend, such as Django's SMTP backend or an Anymail backend.",
        id="messaging.E004",
    )]


_ADMIN_EMAIL_HANDLER = "django.utils.log.AdminEmailHandler"


def check_error_mail(app_configs: Sequence[AppConfig] | None = None, **kwargs: object) -> list[checks.CheckMessage]:
    """``messaging.W001``: a configured error-mail handler sends through the system mailer.

    Where messaging's system mailer is Django's default mailer, an ``AdminEmailHandler``
    in ``LOGGING`` that names no ``using`` (or the default) records each error report in
    the database the error may be about. Messaging's own ``LOGGING`` names the
    transport; a host that states its own names it too.
    """

    del app_configs, kwargs
    default = mailers.settings.get(DEFAULT_MAILER_ALIAS) or {}
    if default.get("BACKEND") != SYSTEM_MAILER_BACKEND:
        return []
    handlers = (getattr(settings, "LOGGING", None) or {}).get("handlers", {})
    recorded = sorted(
        name for name, handler in handlers.items()
        if _ADMIN_EMAIL_HANDLER in (handler.get("class"), handler.get("()"))
        and handler.get("using") in (None, "", DEFAULT_MAILER_ALIAS)
    )
    if not recorded:
        return []
    return [checks.Warning(
        f"LOGGING's {', '.join(recorded)} handler sends error mail through Django's default mailer, messaging's "
        "system mailer, which records it in the database the error may be about.",
        hint='Name "using: transport" on it (messaging\'s README, Mailers).',
        id="messaging.W001",
    )]
