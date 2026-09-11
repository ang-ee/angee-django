"""Django timestamp/primary-key ordering for bounded keyset reads."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class InvalidKeysetCursor(ValueError):
    """A cursor cannot be decoded in the current query's signing scope."""


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

    def before(self, position: tuple[Any, Any], *, inclusive: bool = False) -> models.Q:
        """Select positions below the cut, optionally including the cut itself."""

        at, pk = position
        return models.Q(**{f"{self.field}__lt": at}) | models.Q(
            **{self.field: at, "pk__lte" if inclusive else "pk__lt": pk}
        )

    def after(self, position: tuple[Any, Any]) -> models.Q:
        """Select positions strictly above the cut."""

        at, pk = position
        return models.Q(**{f"{self.field}__gt": at}) | models.Q(**{self.field: at, "pk__gt": pk})

    def position(self, row: models.Model) -> tuple[datetime, Any]:
        """Read the exact annotated tuple used by SQL ordering."""

        return getattr(row, self.field), row.pk

    def sign(self, row: models.Model, signer: signing.Signer) -> str:
        """Sign a tuple rather than a row reference, so deleted anchors stay usable."""

        at, pk = self.position(row)
        return signer.sign_object([at.isoformat(), str(pk)])

    def unsign(self, value: str, signer: signing.Signer, pk_field: models.Field) -> tuple[datetime, Any]:
        """Verify a signed cut and coerce its PK through the model's own field."""

        try:
            match signer.unsign_object(value):
                case [str(at), str(pk)]:
                    timestamp = datetime.fromisoformat(at)
                    if timezone.is_naive(timestamp):
                        raise ValueError("Cursor timestamp must be timezone-aware.")
                    return timestamp, pk_field.to_python(pk)
                case _:
                    raise ValueError("Cursor must carry a timestamp and primary key.")
        except (signing.BadSignature, ValidationError, ValueError, TypeError) as error:
            raise InvalidKeysetCursor("Invalid keyset cursor for this scope.") from error
