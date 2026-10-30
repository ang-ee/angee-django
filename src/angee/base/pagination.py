"""Signed timestamp/primary-key cuts for Django querysets."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class InvalidKeysetCursor(ValueError):
    """A cursor cannot be decoded in the current query's signing scope."""


def keyset_signer(scope: Sequence[Any], *, salt: str) -> signing.Signer:
    """Bind Django's native signature to the caller's effective query identity."""

    fingerprint = hashlib.sha256(json.dumps(scope).encode()).hexdigest()
    return signing.Signer(salt=f"{salt}.{fingerprint}")


@dataclass(frozen=True, slots=True)
class KeysetPage[Row]:
    """Authorized rows and stable cuts, independent of a domain or transport."""

    rows: Iterable[Row]
    count: int
    older_cursor: str | None
    newer_cursor: str | None
    has_older: bool
    has_newer: bool
    has_more_in_window: bool
    has_older_than_through: bool
    has_newer_than_before: bool


@dataclass(frozen=True, slots=True)
class KeysetOrder:
    """A non-null timestamp expression followed by the model's native primary key.

    The caller owns the timestamp annotation and readable queryset. These native
    Q expressions also work with Django querysets that do not inherit Angee.
    """

    field: str
    tie_breakers: tuple[str, ...] = ()
    """Native Django ordering terms between timestamp-desc and primary-key-desc."""

    def _cut(self, position: tuple[Any, ...], *, older: bool, inclusive: bool = False) -> models.Q:
        columns = (
            (self.field, True),
            *((term.removeprefix("-"), term.startswith("-")) for term in self.tie_breakers),
            ("pk", True),
        )
        if len(position) != len(columns):
            raise ValueError("Cursor does not match its declared keyset ordering.")
        predicate = models.Q(pk__in=[])
        equal: dict[str, Any] = {}
        for (name, descending), value in zip(columns, position, strict=True):
            comparison = "lt" if descending == older else "gt"
            predicate |= models.Q(**equal, **{f"{name}__{comparison}": value})
            equal[name] = value
        return predicate | models.Q(**equal) if inclusive else predicate

    def before(self, position: tuple[Any, ...], *, inclusive: bool = False) -> models.Q:
        """Select the older side, respecting each native tie-break direction."""

        return self._cut(position, older=True, inclusive=inclusive)

    def after(self, position: tuple[Any, ...]) -> models.Q:
        """Select the newer side of this same total ordering."""

        return self._cut(position, older=False)

    def position(self, row: models.Model) -> tuple[Any, ...]:
        """Read the exact tuple declared by the caller's SQL ordering."""

        return (
            getattr(row, self.field),
            *(getattr(row, term.removeprefix("-")) for term in self.tie_breakers),
            row.pk,
        )

    def sign_position(self, position: tuple[Any, ...], signer: signing.Signer) -> str:
        """Sign stored tuple values so a deleted or moved anchor remains usable."""

        if len(position) != len(self.tie_breakers) + 2:
            raise ValueError("Cursor does not match its declared keyset ordering.")
        at, *remaining = position
        return signer.sign_object([at.isoformat(), *(str(value) for value in remaining)])

    def sign(self, row: models.Model, signer: signing.Signer) -> str:
        """Sign this row through the same tuple codec used by heterogeneous readers."""

        return self.sign_position(self.position(row), signer)

    def unsign(self, value: str, signer: signing.Signer, pk_field: models.Field) -> tuple[Any, ...]:
        """Validate the native signature and let the owning field decode its key."""

        try:
            payload = signer.unsign_object(value)
            if (
                not isinstance(payload, list)
                or len(payload) != len(self.tie_breakers) + 2
                or not all(isinstance(part, str) for part in payload)
            ):
                raise ValueError("Cursor does not match its declared keyset ordering.")
            at, *ties, pk = payload
            timestamp = datetime.fromisoformat(at)
            if timezone.is_naive(timestamp):
                raise ValueError("Cursor timestamp must be timezone-aware.")
            return timestamp, *ties, pk_field.to_python(pk)
        except (signing.BadSignature, ValidationError, ValueError, TypeError) as error:
            raise InvalidKeysetCursor("Invalid keyset cursor for this scope.") from error
