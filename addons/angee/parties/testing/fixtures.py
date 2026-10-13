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
from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.test import override_settings
from rebac import backend, system_context

from angee.resources.entries import ResourceEntry, resource_manifest_for


def load_statement_sources() -> None:
    """Load parties' install declarations once per database state, masters excluded.

    A single existence query when the principals are present; a reload after a
    flush. Both relationship stores receive the grants because a suite may switch
    storage after bootstrap. Fixtures keep their own relationship vocabulary, so
    the master tier is excluded through the resource loader's native contract.
    """

    if "provenance" not in apps.app_configs or "parties" not in apps.app_configs:
        return
    if backend().schema().get_definition("provenance/role") is None:
        # A host may call this before its first native sync.
        call_command("rebac", "sync", verbosity=0)
    rule = apps.get_model("provenance", "Rule")
    with system_context(reason="parties.testing.statement_sources"):
        if rule._base_manager.exists():
            return
        addon = apps.get_app_config("parties")
        masters = tuple(
            ":".join(ResourceEntry.from_declaration(addon, "master", declaration).key)
            for declaration in resource_manifest_for(addon)["master"]
        )
        resource = apps.get_model("resources", "Resource")
        with override_settings(
            ANGEE_RESOURCE_EXCLUDED_ENTRIES=(*getattr(settings, "ANGEE_RESOURCE_EXCLUDED_ENTRIES", ()), *masters)
        ):
            for storage in ("denormalized", "registry"):
                with override_settings(REBAC_LOCAL_BACKEND_STORAGE=storage):
                    resource.objects.load_addons((addon,), tiers=("install",))


@pytest.fixture
def statement_sources(db: None) -> None:
    """The statement principals and grants parties declares, loaded into this test's database."""

    del db
    load_statement_sources()
