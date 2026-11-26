"""IAM's start-up checks: its sealed account permissions and the open set of account actions."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from functools import partial
from typing import Any

from django.apps import AppConfig
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import checks
from django.utils.module_loading import import_string

from angee.base.checks import check_sealed_permissions
from angee.base.permissions import declared_rebac_definition
from angee.iam.auth import SESSION_BACKEND

ACCOUNT_PREVIEW = {"auth/user": ("view_as",)}
"""Account preview, ``admin->member`` alone: a platform-admin action.

A fragment arm on ``auth/user#view_as`` would let that relation act as the
account; each addon grants its display need through its own record's read.
"""

check_view_as_admin_only = partial(
    check_sealed_permissions, "iam", ACCOUNT_PREVIEW, "angee.iam.E001",
    "Drop the fragment's arm: preview stays a platform-admin action; grant the display need "
    "through the fragment's own record's read.",
)

SET_PASSWORD = {"auth/user": ("set_password",)}
"""The platform-admin/write/field-write intersection; fragments cannot bypass its gates."""

check_set_password_permissions = partial(
    check_sealed_permissions, "iam", SET_PASSWORD, "angee.iam.E003",
    "Drop the fragment's arm: Set password stays with platform admins through both write gates, "
    "and an arm on either gate cannot widen the intersection.",
)

ACCOUNT_DISCLOSURES = {"auth/user": ("active_account", "nonstaff_account")}
"""Who is active and who is not staff, derived from their ``_granted`` grants, which fragments extend.

A fragment's action unions into ``auth/user#active_account_granted`` or
``#nonstaff_account_granted``. An arm unioned straight into a disclosure would
land outside its ``& active->disclosed`` or ``- staff->disclosed``, disclosing
every inactive or staff account to that arm's holders.
"""

check_account_disclosures = partial(
    check_sealed_permissions, "iam", ACCOUNT_DISCLOSURES, "angee.iam.E004",
    "Union the fragment's arm into auth/user#active_account_granted or #nonstaff_account_granted, "
    "from which IAM derives its disclosures.",
)


def check_account_actions(
    app_configs: Sequence[AppConfig] | None = None, *, model: Any = None, **kwargs: object,
) -> list[checks.CheckMessage]:
    """``angee.iam.E005``: the composed account actions are unique, and each is a declared ``auth/user`` permission.

    A donor adds its action to ``User.account_actions`` once, and declares its
    permission in its own fragment; an action offered twice, or one the
    composed ``auth/user`` does not declare, is reported at start-up instead of
    on the first users list. ``model`` stands in for the user model in tests.
    Protection needs no check: every action meets it through
    ``account_action_refusals``.
    """

    del app_configs, kwargs
    users = get_user_model()
    owner = users if model is None else model
    actions = owner.account_actions()
    errors: list[checks.CheckMessage] = []
    if duplicated := sorted(action for action, count in Counter(actions).items() if count > 1):
        errors.append(checks.Error(
            f"{users._meta.label}.account_actions() offers {', '.join(duplicated)} more than once.",
            hint="Each addon adds its own action once; leave an action another addon offers to that addon.",
            obj=users,
            id="angee.iam.E005",
        ))
    definition = declared_rebac_definition(users)
    declared = {permission.name for permission in definition.permissions} if definition is not None else set()
    if undeclared := sorted(set(actions) - declared):
        errors.append(checks.Error(
            f"{users._meta.label}.account_actions() offers {', '.join(undeclared)}, "
            "which the composed auth/user does not declare.",
            hint="Declare each action's permission on auth/user, in the addon's permissions.extends.zed.",
            obj=users,
            id="angee.iam.E005",
        ))
    return errors


MOVED_BACKENDS = {"angee.iam.auth.SignInLinkBackend": "angee.iam_messaging.auth.SignInLinkBackend"}
"""Backend paths IAM no longer provides, each with the path that replaced it, for hosts that list the old one."""


def check_authentication_backends(
    app_configs: Sequence[AppConfig] | None = None, **kwargs: object,
) -> list[checks.CheckMessage]:
    """``angee.iam.E006``: every ``AUTHENTICATION_BACKENDS`` entry imports, and ``SESSION_BACKEND`` is listed.

    Django imports the backends only when someone signs in, so an entry that
    no longer imports makes every sign-in raise, a password's included, while
    start-up says nothing. Every session records ``SESSION_BACKEND``, so a list
    without it signs people in and then loads none of their sessions.
    """

    del app_configs, kwargs
    errors: list[checks.CheckMessage] = []
    backends = list(settings.AUTHENTICATION_BACKENDS)
    for path in backends:
        try:
            import_string(path)
        except ImportError as error:
            moved = MOVED_BACKENDS.get(path)
            errors.append(checks.Error(
                f"AUTHENTICATION_BACKENDS lists {path!r}, which does not import ({error}).",
                hint=f"Replace it with {moved!r}." if moved else "Remove it, or install the addon that provides it.",
                id="angee.iam.E006",
            ))
    if SESSION_BACKEND not in backends:
        errors.append(checks.Error(
            f"AUTHENTICATION_BACKENDS does not list {SESSION_BACKEND!r}, which loads every session IAM opens.",
            hint="Keep IAM's ModelBackend in AUTHENTICATION_BACKENDS; IAM's autoconfig appends it.",
            id="angee.iam.E006",
        ))
    return errors
