"""Fieldless IAM principal donor for provenance statement attribution."""

from __future__ import annotations

from typing import ClassVar, cast

from django.db import models

from angee.provenance.statements import SourceKind, StatementSourceMixin


class UserSource(StatementSourceMixin, models.Model):
    """Classify IAM's active people without making IAM depend on provenance."""

    extends: ClassVar[str | None] = "iam.User"
    is_active: bool
    is_person: bool

    @property
    def source_kind(self) -> SourceKind | None:
        return cast(SourceKind, SourceKind.PERSON) if self.is_active and self.is_person else None

    class Meta:
        abstract = True
