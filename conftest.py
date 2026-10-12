"""Repository-wide attribution and principal bootstrap for native pytest hosts."""

from collections.abc import Iterator
from contextlib import nullcontext

import pytest
from pytest_django.django_compat import is_django_unittest

from angee.base.changesets import changeset
from tests import conftest as source_models  # noqa: F401 -- register the shared source graph before database setup
from tests.bootstrap import statement_sources

pytest_plugins = ("angee.testing.fixtures", "angee.workflows.testing.fixtures")


@pytest.fixture(scope="session", autouse=True)
def statement_principals(django_db_setup: None, django_db_blocker: object) -> None:
    """Install the declared statement principals and grants once per test database.

    The rows commit outside any test transaction, so every test that runs inside
    pytest-django's per-test transaction sees them without reloading.
    """

    del django_db_setup
    with django_db_blocker.unblock():  # type: ignore[attr-defined]
        statement_sources()


@pytest.fixture(autouse=True)
def captured_changeset(request: pytest.FixtureRequest, django_db_blocker: object) -> Iterator[None]:
    """Carry entrypoint defaults through source and addon tests, including native TestCases.

    A test that flushed the database (``transactional_db``, ``TransactionTestCase``)
    leaves the next database test without principals; ``statement_sources`` is a
    single existence query when they are present and reloads them when not.
    """

    names = set(request.fixturenames)
    unittest = is_django_unittest(request)
    uses_db = bool(
        unittest
        or request.node.get_closest_marker("django_db")
        or {"db", "transactional_db", "composed_tables"} & names
    )
    if uses_db and not unittest:
        # Depend on the database fixture explicitly so this boundary is torn down
        # (and its changeset closed) while database access is still allowed.
        request.getfixturevalue("transactional_db" if {"transactional_db", "composed_tables"} & names else "db")
    with django_db_blocker.unblock() if uses_db else nullcontext():  # type: ignore[attr-defined]
        if uses_db:
            statement_sources()
        with changeset(fresh=True, change_reason="pytest"):
            yield
