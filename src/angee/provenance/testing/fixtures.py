"""Opt-in pytest support for hosts installing provenance: attribution boundaries and statement principals."""

from collections.abc import Iterator
from typing import Any

import pytest
from django.apps import apps
from django.db.models.signals import post_migrate
from pytest_django.django_compat import is_django_unittest
from pytest_django.plugin import DjangoDbBlocker

from angee.provenance.context import changeset
from angee.provenance.testing import load_statement_sources


def pytest_configure(config: pytest.Config) -> None:
    """Register the marker a module uses to exercise production boundaries itself."""

    config.addinivalue_line(
        "markers",
        "entrypoint_boundaries: the module opens its own changeset boundaries; the autouse test boundary stays closed",
    )


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup: None, django_db_blocker: DjangoDbBlocker) -> Iterator[None]:
    """The session baseline also holds every installed addon's statement principals.

    Extends the ``angee.base.testing`` fixture of the same name, so this plugin loads
    after it: the install-tier principals and grants that admission reads
    (:func:`~angee.provenance.testing.load_statement_sources`) load once, as a
    composed host's install tier loads them, and again at provenance's turn of the
    ``post_migrate`` a transactional flush sends, after REBAC's resync.
    """

    provenance = apps.get_app_config("provenance")

    def load(**kwargs: Any) -> None:
        load_statement_sources()

    post_migrate.connect(load, sender=provenance, weak=False)
    try:
        with django_db_blocker.unblock():
            load()
        yield
    finally:
        post_migrate.disconnect(load, sender=provenance)


@pytest.fixture(autouse=True)
def captured_changeset(request: pytest.FixtureRequest) -> Iterator[None]:
    """Give every test one attribution boundary, as an entrypoint gives a request or a command.

    Strict mode refuses a captured write without an open changeset, so a host that
    loads this plugin gets the boundary with it. The boundary declares no activity,
    so a seed load or any other explicit inner boundary refines it instead of
    conflicting. It depends on the test's ``db`` fixture, so it is torn down, and
    its changeset closed, while database access is still allowed; the test's
    ``django_db`` mark or ``transactional_db`` request still chooses rollback or
    transactional isolation. It is published to the process (``process=True``): a
    thread or pool the test starts writes under it in its own row, as under a
    management command. Modules marked ``entrypoint_boundaries`` exercise the
    production entrypoints' own boundaries and run without this one.
    """

    if request.node.get_closest_marker("entrypoint_boundaries"):
        yield
        return
    if not is_django_unittest(request) and (
        request.node.get_closest_marker("django_db") or {"db", "transactional_db"} & set(request.fixturenames)
    ):
        request.getfixturevalue("db")
    with changeset(fresh=True, change_reason="pytest", process=True):
        yield
