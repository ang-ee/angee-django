"""Small abstract model mixins shared by source addons.

These are independent abstract bases an addon mixes into its source models.
The default base model that composes the REBAC-scoped behavior lives in
``angee.base.models`` as :class:`~angee.base.models.AngeeModel`.
"""

from __future__ import annotations

from typing import Any, ClassVar

import reversion
from django.db import models


class TimestampMixin(models.Model):
    """Add creation and update timestamps to a source model."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        """Django model options."""

        abstract = True


class SqidMixin(models.Model):
    """Lookup helper for models with an explicit ``sqid`` field."""

    class Meta:
        """Django model options."""

        abstract = True

    @classmethod
    def from_sqid(cls, sqid: str) -> Any | None:
        """Return the row with ``sqid`` or ``None``."""

        return cls._default_manager.filter(sqid=sqid).first()

    @property
    def public_id(self) -> str:
        """Return the opaque sqid for this model instance."""

        return str(self.sqid)

    @classmethod
    def from_public_id(cls, value: str) -> Any | None:
        """Return the row with this opaque external id or ``None``."""

        return cls.from_sqid(value)


class HistoryMixin(models.Model):
    """Marker: audit a source model with django-simple-history.

    The composer emits ``HistoricalRecords`` onto the composed concrete model
    (with its app label), so each save appends to a ``Historical<Model>``
    shadow table exposed as ``instance.history``. Addons just mix this in.
    """

    class Meta:
        """Django model options."""

        abstract = True


class RevisionMixin(models.Model):
    """Snapshot named fields into django-reversion versions.

    A model declares ``revisioned_fields``; the base addon registers the
    concrete model with django-reversion so edits made inside a revision block
    (every request, via the revision middleware) are versioned and revertible.
    Use this for large content fields that would bloat the history table.
    """

    revisioned_fields: ClassVar[tuple[str, ...]] = ()

    class Meta:
        """Django model options."""

        abstract = True

    @property
    def revisions(self) -> Any:
        """Return this row's versions, newest first."""

        return reversion.models.Version.objects.get_for_object(self)

    def revert_to(self, version: Any) -> None:
        """Restore the revisioned fields from a version and save.

        Only the declared fields are versioned, so the row is restored field by
        field rather than through a whole-object deserialization.
        """

        data = version.field_dict
        for name in self.revisioned_fields:
            if name in data:
                setattr(self, name, data[name])
        self.save()
