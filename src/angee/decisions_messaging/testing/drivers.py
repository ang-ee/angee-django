"""What a bare test host loads before it mails a decision, as a host's provision loads it."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.contrib.auth import get_user_model
from rebac import system_context

MAIL_ADDONS = ("brandkit", "messaging_templates", "decisions_messaging")
"""The addons whose install tiers decision mail reads: the brand kit, the template library and this addon's rows."""


def addressed_user(username: str, **fields: Any) -> Any:
    """An active person with an address at ``example.test``, through the user manager's own factory."""

    fields.setdefault("email", f"{username}@example.test")
    return get_user_model().objects.create_user(username=username, password=username, **fields)


def load_mail_templates() -> None:
    """Load the install tiers of :data:`MAIL_ADDONS` in one load, as a host's provision does."""

    resource = apps.get_model("resources", "Resource")
    resource.objects.load_addons(
        tuple(apps.get_app_config(label) for label in MAIL_ADDONS), tiers=[resource.Tier.INSTALL],
    )


def install_decision_mail() -> None:
    """Load messaging's system channel (its vendor first) and the mail templates.

    The host's settings supply the rest of system mail: a ``MAILERS["transport"]``
    mailer, ``DEFAULT_FROM_EMAIL`` and ``ANGEE_PUBLIC_ORIGIN``.
    """

    resources = apps.get_model("resources", "Resource").objects
    with system_context(reason="tests.decisions_messaging.install"):
        resources.load_xref("messaging.email", model=apps.get_model("integrate", "Vendor"))
        resources.load_xref("messaging.system_channel", model=apps.get_model("messaging", "Channel"))
    load_mail_templates()
