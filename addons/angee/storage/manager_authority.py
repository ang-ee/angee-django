"""Transaction-scoped authority for native file-attachment membership writes."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from angee.base.authority import TransactionBoundAuthority

_attachment_membership_authority = TransactionBoundAuthority[None](
    "storage_attachment_membership_authority",
    atomic_error="File-attachment membership writes require an atomic owner.",
    nested_error="File-attachment membership authorities cannot be nested.",
)


@contextmanager
def attachment_membership_authority(alias: str) -> Iterator[None]:
    """Authorize one native manager-owned membership transaction."""

    with _attachment_membership_authority.scope(alias, None):
        yield


def attachment_membership_is_authorized(alias: str) -> bool:
    """Return whether the current call is inside the exact owning transaction."""

    return _attachment_membership_authority.is_active(alias)
