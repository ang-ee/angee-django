"""PostgreSQL test databases inherit the extensions their source models require."""

from pathlib import Path

import pytest
from django.db import connection

from tests.composed_host import run_composed_tests

pytestmark = pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL extensions required")


def test_pytest_database_has_vector_extension(db: None) -> None:
    """Native pytest database setup needs vector even with migrations disabled."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT extname FROM pg_extension WHERE extname = %s", ["vector"])
        assert cursor.fetchone() == ("vector",)


def test_composed_pgvector_database_inherits_vector(tmp_path: Path, subtests: pytest.Subtests) -> None:
    """The composed host creates its own database and the pgvector addon tables."""
    run_composed_tests(
        tmp_path, subtests, app="angee.knowledge_graph_pgvector", postgresql=("tests.native_postgresql_extensions",),
    )
