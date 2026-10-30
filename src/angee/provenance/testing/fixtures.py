"""Opt-in pytest attribution boundaries for hosts installing provenance."""

from collections.abc import Iterator

import pytest
from pytest_django.django_compat import is_django_unittest

from angee.provenance.context import changeset


def pytest_configure(config: pytest.Config) -> None:
    """Register the marker a module uses to exercise production boundaries itself."""

    config.addinivalue_line(
        "markers",
        "entrypoint_boundaries: the module opens its own changeset boundaries; the autouse test boundary stays closed",
    )


@pytest.fixture(autouse=True)
def captured_changeset(request: pytest.FixtureRequest) -> Iterator[None]:
    """Give every test one attribution boundary, as an entrypoint gives a request or a command.

    Strict mode refuses a captured write without an open changeset, so a host that
    loads this plugin gets the boundary with it. The boundary declares no activity,
    so a seed load or any other explicit inner boundary refines it instead of
    conflicting. It depends on the test's database fixture so it is torn down, and
    its changeset closed, while database access is still allowed. It is published to
    the process (``process=True``): a thread or pool the test starts writes under it
    in its own row, as under a management command. Modules marked
    ``entrypoint_boundaries`` exercise the production entrypoints' own boundaries and
    run without this one.
    """

    if request.node.get_closest_marker("entrypoint_boundaries"):
        yield
        return
    names = set(request.fixturenames)
    if not is_django_unittest(request) and (
        request.node.get_closest_marker("django_db") or {"db", "transactional_db", "composed_tables"} & names
    ):
        request.getfixturevalue("transactional_db" if {"transactional_db", "composed_tables"} & names else "db")
    with changeset(fresh=True, change_reason="pytest", process=True):
        yield
