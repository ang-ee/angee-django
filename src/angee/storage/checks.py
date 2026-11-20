"""Storage's sealed file permissions, checked by the base sealed-permission check."""

from __future__ import annotations

from functools import partial

from angee.base.checks import check_sealed_permissions

DERIVED_PERMISSIONS = {"storage/file": ("read", "write", "share", "inherit_visibility")}
"""File permissions whose expressions storage seals against fragment extensions.

``read``, ``write`` and ``share`` withhold a trashed file from everyone who
cannot delete it; fragments widen their ``*_granted`` arms instead.
``inherit_visibility`` admits only the drive's sharers to restore inheritance.
"""

check_derived_permissions = partial(check_sealed_permissions, "storage", DERIVED_PERMISSIONS, "storage.E001")
