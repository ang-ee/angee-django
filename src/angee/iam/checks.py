"""IAM's sealed account permissions, checked by the base sealed-permission check."""

from __future__ import annotations

from functools import partial

from angee.base.checks import check_sealed_permissions

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
