"""Opt-in pytest support kept for consumers that request ``statement_sources``.

Admission is a schema fact fed by seeded install resources (``provenance.Rule``
rows and their role grants). ``angee.provenance.testing.fixtures`` loads every
installed addon's principals into the session baseline, so this fixture finds
parties' already loaded; it remains until its consumers stop requesting it.
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
