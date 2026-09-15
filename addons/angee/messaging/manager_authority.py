"""Transaction-scoped authority for exact attachment-claim Part writes."""

from __future__ import annotations

from collections.abc import Collection, Iterator
from contextlib import contextmanager

from django.core.exceptions import FieldDoesNotExist

from angee.base.authority import TransactionBoundAuthority

PART_CLAIM_IDENTITY_FIELDS = frozenset(
    {
        "source_claim_id",
        "message_id",
        "file_id",
        "external_link_id",
        "parent_id",
        "fragment_id",
        "disposition",
    }
)


def normalized_part_claim_write_fields(
    model: type[object], fields: Collection[str]
) -> set[str]:
    """Normalize model field names to stored attnames for claim guards."""

    normalized: set[str] = set()
    for name in fields:
        try:
            field = model._meta.get_field(name)  # type: ignore[attr-defined]
        except FieldDoesNotExist:
            normalized.add(name)
        else:
            normalized.add(field.attname)
    return normalized


_part_claim_write_authority = TransactionBoundAuthority[None](
    "messaging_part_claim_write_authority",
    atomic_error="Claim-backed Part writes require an atomic owner.",
    nested_error="Claim-backed Part authorities cannot be nested.",
)


@contextmanager
def part_claim_write_authority(alias: str) -> Iterator[None]:
    """Authorize one native manager-owned claimed-Part mutation."""

    with _part_claim_write_authority.scope(alias, None):
        yield


def part_claim_write_is_authorized(alias: str) -> bool:
    """Return whether this call owns the exact live claimed-Part transaction."""

    return _part_claim_write_authority.is_active(alias)
