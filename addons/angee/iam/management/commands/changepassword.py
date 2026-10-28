"""Rebac-aware override of Django's ``changepassword``.

The stock command loads the target user through ``User._default_manager``
with no actor bound (``django/contrib/auth/management/commands/changepassword``).
Under Angee's rebac-scoped User manager with ``REBAC_STRICT_MODE=True`` that
materialisation raises :class:`rebac.errors.MissingActorError`, so the vanilla
command cannot run.

IAM owns this override. The host command-discovery bridge selects it because
Django installs IAM after its auth dependency. It runs the operation inside
``system_context``: the same audited bypass the framework already uses for
out-of-request maintenance jobs (see ``angee.iam.bootstrap_admin``). Behaviour
is otherwise identical to the stock command (prompting, validation, retries).
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth.management.commands.changepassword import (
    Command as ChangePasswordCommand,
)
from rebac import system_context


class Command(ChangePasswordCommand):
    """``changepassword`` wrapped in an audited system context."""

    def handle(self, *args: Any, **options: Any) -> str | None:
        """Run the stock password change with rebac scoping bypassed."""

        with system_context(reason="auth.changepassword"):
            return super().handle(*args, **options)
