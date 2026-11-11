"""Storage's sealed file permissions, checked by the base sealed-permission check."""

from __future__ import annotations

from functools import partial

from angee.base.checks import check_sealed_permissions

DERIVED_PERMISSIONS = {"storage/file": ("read", "write", "share")}
"""The file permissions storage derives from their ``*_granted`` arms, which fragments never extend.

Each withholds a trashed file from everyone who cannot delete it. An arm
unioned straight into one would reach the file outside that withholding;
fragments widen ``read_granted``, ``write_granted`` and ``share_granted`` instead.
"""

check_derived_permissions = partial(check_sealed_permissions, "storage", DERIVED_PERMISSIONS, "storage.E001")
