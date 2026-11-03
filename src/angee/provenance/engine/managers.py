"""Native changeset document scopes and transaction-owned creation receipts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connections, models, transaction

from .context import current_changeset, discard_changeset_claim, require_changeset
from .exceptions import CreationKeyConflict
from .querysets import ChangesetQuerySet as ChangesetQuerySet
from .serialization import creation_fingerprint

if TYPE_CHECKING:
    _ChangesetManager = models.Manager[Any]
else:
    _ChangesetManager = models.Manager.from_queryset(ChangesetQuerySet)


class ChangesetManager(_ChangesetManager):
    creation_conflict: type[Exception] = CreationKeyConflict
    creation_fingerprint = staticmethod(creation_fingerprint)

    @staticmethod
    def claim_field_name(name: str) -> str:
        """Public input name for a persisted claim field."""
        return name

    if TYPE_CHECKING:

        def touching(self, record: Any) -> ChangesetQuerySet: ...
        def records_touched(self) -> Any: ...

    def claim(self, scope: str, key: str, *, fingerprint: str) -> Any:
        for name, value in (("scope", scope), ("key", key), ("fingerprint", fingerprint)):
            error_name = self.claim_field_name(name)
            try:
                self.model._meta.get_field(name).clean(value, None)
            except ValidationError as error:
                raise ValidationError({error_name: error}) from error
            if not value.strip():
                raise ValidationError({error_name: "A claim value must not be blank."})
        if not connections[self.db].in_atomic_block:
            raise RuntimeError("Claims require an outer transaction.atomic().")
        row = require_changeset(using=self.db)
        if not isinstance(row, self.model) or row.pk is not None or not row._provenance_fresh:
            raise RuntimeError("Claim a fresh idempotency key before the boundary records any writes.")
        row.scope, row.key, row.fingerprint = scope, key, fingerprint
        try:
            with transaction.atomic(using=self.db):
                current_changeset(required=True, record=True, using=self.db)
        except IntegrityError:
            discard_changeset_claim(row)
            existing = self.model._base_manager.using(self.db).filter(scope=scope, key=key).first()
            if existing is None:
                raise
            if existing.fingerprint != fingerprint:
                raise self.creation_conflict("The creation key already identifies different content.") from None
            return existing
        return None

    def claim_created[CreatedT: models.Model](
        self,
        scope: str,
        key: str,
        *,
        values: Any,
        model: type[CreatedT],
        unavailable: Exception | None = None,
    ) -> CreatedT | None:
        receipt = self.claim(scope, key, fingerprint=self.creation_fingerprint(values))
        if receipt is None:
            return None
        created = receipt.created_row
        if not isinstance(created, model):
            raise unavailable or self.creation_conflict("The claimed result is unavailable or has another type.")
        return created
