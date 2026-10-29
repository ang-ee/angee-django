"""Rebac-aware override of Django's ``createsuperuser``.

Like :mod:`~angee.iam.management.commands.changepassword`, the stock command
reaches the User model through ``User._default_manager`` with no actor bound —
both the uniqueness check (``get_by_natural_key``) and the final
``create_superuser`` call. Under Angee's rebac-scoped User manager with
``REBAC_STRICT_MODE=True`` that raises :class:`rebac.errors.MissingActorError`.

Every DB access in the stock command happens inside ``handle()`` (the interactive
prompt loop and the non-interactive path both validate and create there), so
wrapping ``handle()`` in ``system_context`` is sufficient. The host's native
command-discovery bridge selects this IAM-owned override despite auth's earlier
position in dependency order.

Note: Angee's first-class superuser path is ``angee.iam.bootstrap_admin`` (env/
settings-driven, idempotent, also grants the platform-admin role). This override
just keeps the stock ``createsuperuser`` usable for ad-hoc/interactive creation.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth.management.commands.createsuperuser import (
    Command as CreateSuperuserCommand,
)
from rebac import system_context


class Command(CreateSuperuserCommand):
    """``createsuperuser`` wrapped in an audited system context."""

    def handle(self, *args: Any, **options: Any) -> str | None:
        """Run the stock superuser creation with rebac scoping bypassed."""

        with system_context(reason="auth.createsuperuser"):
            return super().handle(*args, **options)
