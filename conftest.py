"""Repository-wide attribution and principal bootstrap for native pytest hosts."""

from collections.abc import Iterator

import pytest
from pytest_django.django_compat import is_django_unittest

from angee.base.changesets import changeset
from tests import conftest as source_models  # noqa: F401 -- register the shared source graph before database setup
from tests.bootstrap import statement_sources

pytest_plugins = ("angee.testing.fixtures", "angee.workflows.testing.fixtures")


@pytest.fixture(autouse=True)
def captured_changeset(request: pytest.FixtureRequest) -> Iterator[None]:
    """Carry entrypoint defaults through source and addon tests, including native TestCases."""

    if (
        is_django_unittest(request)
        or request.node.get_closest_marker("django_db")
        or {"db", "transactional_db", "composed_tables"}.intersection(request.fixturenames)
    ):
        request.getfixturevalue("composed_permissions")
        statement_sources()
    with changeset(fresh=True, change_reason="pytest"):
        yield
