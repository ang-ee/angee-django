"""Native custom statement queryset, separate from model declarations."""

from typing import Self

from angee.provenance.statements import Statement, StatementQuerySet


class FixtureStatementQuerySet[T: Statement](StatementQuerySet[T]):
    def handles(self) -> Self:
        return self
