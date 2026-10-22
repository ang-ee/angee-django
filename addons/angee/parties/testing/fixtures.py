"""Opt-in pytest support: the statement principals parties declares.

Admission is a schema fact fed by seeded install resources (``provenance.Rule``
rows and their role grants); the runtime only reads them, and a stack loads them
with its install tier. A test database starts without them, so a host whose tests
reach a statement source loads them after its REBAC sync: the framework's source
and composed hosts call :func:`load_statement_sources`; a consumer host lists this
module in ``pytest_plugins`` and requests the ``statement_sources`` fixture.
"""

from __future__ import annotations

import pytest

from angee.provenance.testing import load_statement_sources as load_addon_statement_sources


def load_statement_sources() -> None:
    """Load parties' statement principals through provenance's shared test seam."""

    load_addon_statement_sources("parties")


@pytest.fixture
def statement_sources(db: None) -> None:
    """The statement principals and grants parties declares, loaded into this test's database."""

    del db
    load_statement_sources()
