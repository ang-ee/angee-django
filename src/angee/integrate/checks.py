"""Integrate's sealed credential replacement permission."""

from __future__ import annotations

from functools import partial

from angee.base.checks import check_sealed_permissions

REPLACE_SECRET = {"integrate/credential": ("replace_secret",)}
"""The administrator/write intersection; fragments cannot bypass either gate."""

check_replace_secret_permissions = partial(check_sealed_permissions, "integrate", REPLACE_SECRET, "integrate.E001")
