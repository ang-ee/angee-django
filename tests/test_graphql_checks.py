"""Focused checks for GraphQL composition validation."""

from __future__ import annotations

from typing import Any

import pytest

from angee.graphql.checks import check_graphql_schemas
from angee.graphql.schema import GraphQLSchemas


def test_graphql_check_reports_discovery_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken addon declaration becomes an actionable Django check error."""

    def fail_discovery(cls: type[GraphQLSchemas]) -> Any:
        del cls
        raise RuntimeError("invalid addon schema reference")

    monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(fail_discovery))

    [error] = check_graphql_schemas()

    assert error.id == "angee.graphql.E001"
    assert error.msg == "GraphQL schema discovery failed: invalid addon schema reference"
    assert error.hint == "Fix the owning addon manifest or schema declaration, then rerun manage.py check."
