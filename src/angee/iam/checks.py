"""IAM's sealed account permissions, checked by the base sealed-permission check."""

from __future__ import annotations

from functools import partial

from angee.base.checks import check_sealed_permissions

ACCOUNT_PREVIEW = {"auth/user": ("view_as",)}
"""Account preview, ``admin->member`` alone: a platform-admin action.

A fragment arm on ``auth/user#view_as`` would let that relation act as the
account; each addon grants its display need through its own record's read.
"""

check_view_as_admin_only = partial(check_sealed_permissions, "iam", ACCOUNT_PREVIEW, "angee.iam.E001")

SET_PASSWORD = {"auth/user": ("set_password",)}
"""The platform-admin/write/field-write intersection; fragments cannot bypass its gates."""

check_set_password_permissions = partial(check_sealed_permissions, "iam", SET_PASSWORD, "angee.iam.E003")
