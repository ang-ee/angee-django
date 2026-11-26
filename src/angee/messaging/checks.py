"""Messaging's system checks: its sealed permissions (``messaging.E003``) and its transport (``messaging.E004``)."""

from __future__ import annotations

from collections.abc import Sequence
from functools import partial

from django.apps import AppConfig
from django.core import checks
from django.core.checks.mail import NON_PRODUCTION_EMAIL_BACKENDS
from django.core.mail import mailers
from django.core.mail.handler import DEFAULT_MAILER_BACKEND

from angee.base.checks import check_sealed_permissions
from angee.messaging.constants import TRANSPORT_MAILER_ALIAS

DERIVED_READS = {"messaging/thread": ("read", "read_inbox"), "messaging/message": ("read", "read_inbox")}
"""The reads messaging derives from its extension points, which fragments never extend.

Fragments widen ``messaging/thread#read_granted`` with grants that hold for any
conversation, which both reads take, and ``messaging/thread#chatter_read`` with a
record's read of its own chatter, which only ``read`` takes; message grants go to
``messaging/message#read_granted``. An arm unioned straight into a derived read
reaches one of the two reads and not the other: the inbox would drop a grant
generic reads keep, or keep one they refuse.
"""

SEALED = {"messaging/channel": ("read", "reply", "write", "annotate", "delete")}
"""The channel permissions that subtract the system channel's seal, which fragments never extend.

Each admits administrators alone on the system channel (backend ``system``), so
an arm unioned into one would reach that channel's mail past the seal. Fragments
widen ``integrate/integration#read`` and ``#write`` instead, which the seal
subtracts.
"""

check_derived_reads = partial(check_sealed_permissions, "messaging", {**DERIVED_READS, **SEALED}, "messaging.E003")
"""``messaging.E003``: the derived thread and message reads, and the channel permissions the system seal guards."""


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
