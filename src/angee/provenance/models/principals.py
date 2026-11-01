"""Angee provenance entities and statement-principal declarations."""

from __future__ import annotations

from typing import ClassVar, cast

from django.db import models

from angee.iam.models.base import ProtectedDataModel
from angee.provenance.statements import (
    SourceKind,
    StatementSourceMixin,
)


class StatementPrincipal(StatementSourceMixin, ProtectedDataModel):
    """Persisted statement-source principal keyed by its declared identity."""

    key = models.CharField(max_length=255, unique=True)
    statement_source_kind: ClassVar[SourceKind]

    @property
    def source_kind(self) -> SourceKind:
        return self.statement_source_kind

    class Meta:
        abstract = True


class Rule(StatementPrincipal, concrete=True):
    """Persisted principal for one registered rule or matcher, keyed by its name."""

    sqid_prefix = "rul_"
    statement_source_kind = cast(SourceKind, SourceKind.RULE)

    class Meta(StatementPrincipal.Meta):
        abstract = True
        rebac_resource_type = "provenance/rule"


class Seed(StatementPrincipal, concrete=True):
    """Persisted principal for one seed load, keyed by that load's identity."""

    sqid_prefix = "sed_"
    statement_source_kind = cast(SourceKind, SourceKind.SEED)

    class Meta(StatementPrincipal.Meta):
        abstract = True
        rebac_resource_type = "provenance/seed"
