"""Small abstract model mixins shared by source addons."""

from __future__ import annotations

from typing import Any

from django.db import models
from rebac import RebacMixin


class TimestampMixin(models.Model):
    """Add creation and update timestamps to a source model."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        """Django model options."""

        abstract = True


class AngeeModel(TimestampMixin):
    """Default abstract base for composed Angee source models."""

    class Meta:
        """Django model options."""

        abstract = True

    @property
    def public_id(self) -> str:
        """Return the stable external id for this model instance."""

        return str(self.pk)

    @classmethod
    def from_public_id(cls, value: str) -> Any | None:
        """Return the row with this external id or ``None``."""

        return cls._default_manager.filter(pk=value).first()


class RebacModelMixin(RebacMixin):
    """Opt a source model into django-zed-rebac enforcement."""

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
