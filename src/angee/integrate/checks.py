"""Integrate's sealed credential replacement permission."""

from __future__ import annotations

from functools import partial

from angee.base.checks import check_sealed_permissions

REPLACE_SECRET = {"integrate/credential": ("replace_secret",)}
"""The administrator/write intersection; fragments cannot bypass either gate."""

check_replace_secret_permissions = partial(
    check_sealed_permissions, "integrate", REPLACE_SECRET, "integrate.E001",
    "Drop the fragment's arm: replacing a secret stays with the credential's administrators, "
    "and an arm on its write gate cannot widen the intersection.",
)
