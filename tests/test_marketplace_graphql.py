"""Marketplace source console mutations — ``addSource`` + ``scan``.

The VCS marketplace tier contributes two admin-gated source controls onto the
platform console, composing integrate's existing owners (it never re-implements
bridge/repo creation or the discovery walk):

- ``addSource`` inventories a repository on an existing ``VcsBridge``
  (``import_repository``) and points an addon ``Source`` at it;
- ``scan`` runs ``Source.refresh()`` (→ ``AddonCatalog.sync_from_source``), which
  discovers ``addon.toml`` rows into the one ``platform.Addon`` marketplace registry.

Driven over the stub VCS backend (canned tree/blobs ride on the bridge config), the
same path the ``marketplace sync`` command and integrate's ``refresh_source`` use.
"""

from __future__ import annotations

import importlib
from typing import Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from rebac import system_context

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from tests.conftest import (
    Repository,
    SchemaAddon,
    Source,
    execute_schema,
    make_integration,
)
from tests.conftest import create_platform_admin as _platform_admin
from tests.conftest import result_data as _data

User = get_user_model()
platform_schema = importlib.import_module("angee.platform.schema")
vcs_schema = importlib.import_module("angee.platform_integrate_vcs.schema")
Addon = apps.get_model("platform", "Addon")
VcsBridge = apps.get_model("integrate_vcs", "VcsBridge")

_REPOS = [
    {
        "name": "acme/addons",
        "org": "acme",
        "remote": "https://github.com/acme/addons.git",
        "default_branch": "main",
        "visibility": "public",
    },
]
_TREE = [
    {"path": "addons/angee/demo/addon.toml", "type": "blob", "oid": "a"},
    {"path": "addons/angee/demo/README.md", "type": "blob", "oid": "b"},
]
_BLOBS = {
    "addons/angee/demo/addon.toml": '[addon]\nname = "angee.demo"\ndescription = "A demo addon."\n'
    'depends_on = ["angee.platform"]\n',
}

_ADD_SOURCE = """
mutation AddSource($data: AddonSourceInput!) {
  add_source(data: $data) { ok message }
}
"""
_SCAN = "mutation($id: ID!){ scan(source_id: $id){ ok message } }"


def _bridge() -> Any:
    """Create a stub-backed VCS bridge serving one repo + one discoverable addon."""

    return make_integration(
        "mkt",
        model=VcsBridge,
        backend_class="stub",
        config={"stub_repos": _REPOS, "stub_tree": _TREE, "stub_blobs": _BLOBS},
    )


def test_add_source_inventories_the_repo_and_points_an_addon_source_at_it(
    composed_tables: None,
) -> None:
    """``addSource`` imports the repo and creates one ``Source(kind="addon")``."""

    del composed_tables
    bridge = _bridge()
    admin = _platform_admin("add-source-admin")

    result = _data(
        _execute(
            _schema(),
            _ADD_SOURCE,
            {"data": {"vcs_bridge_id": _public_id(bridge), "name": "acme/addons", "path": "addons"}},
            user=admin,
        )
    )["add_source"]

    assert result["ok"] is True
    assert "acme/addons" in result["message"]
    with system_context(reason="test.marketplace.add_source.verify"):
        repository = Repository.objects.get(name="acme/addons")
        source = Source.objects.get(repository=repository)
    assert (source.kind, source.path) == ("addon", "addons")


def test_scan_discovers_addon_toml_rows_into_the_registry(
    composed_tables: None,
) -> None:
    """``scan`` runs the discovery walk and writes a REMOTE ``platform.Addon`` row."""

    del composed_tables
    bridge = _bridge()
    admin = _platform_admin("scan-admin")
    _data(
        _execute(
            _schema(),
            _ADD_SOURCE,
            {"data": {"vcs_bridge_id": _public_id(bridge), "name": "acme/addons", "path": "addons"}},
            user=admin,
        )
    )
    with system_context(reason="test.marketplace.scan.lookup"):
        source = Source.objects.get(kind="addon")

    result = _data(_execute(_schema(), _SCAN, {"id": _public_id(source)}, user=admin))["scan"]

    assert result["ok"] is True
    assert "1 addon" in result["message"]
    with system_context(reason="test.marketplace.scan.verify"):
        row = Addon.objects.get(name="angee.demo")
    assert str(row.source) == Addon.Source.REMOTE
    assert row.vcs_path == "addons/angee/demo"


def test_scan_refuses_a_non_addon_source(
    composed_tables: None,
) -> None:
    """``scan`` of a non-addon source kind is reported, never dispatched."""

    del composed_tables
    bridge = _bridge()
    admin = _platform_admin("scan-wrong-kind-admin")
    with system_context(reason="test.marketplace.scan.template_seed"):
        repository = Repository.objects.create(vcs_bridge=bridge, org="acme", name="acme/addons", remote="r")
        source = Source.objects.create(repository=repository, kind="template", path="templates")

    result = _data(_execute(_schema(), _SCAN, {"id": _public_id(source)}, user=admin))["scan"]

    assert result["ok"] is False
    assert "not an addon source" in result["message"].lower()


def test_add_source_denies_a_non_admin(
    composed_tables: None,
) -> None:
    """The REBAC admin gate denies a non-admin actor."""

    del composed_tables
    bridge = _bridge()
    plain = User.objects.create_user(username="mkt-plain", password="mkt-plain")

    result = _execute(
        _schema(),
        _ADD_SOURCE,
        {"data": {"vcs_bridge_id": _public_id(bridge), "name": "acme/addons", "path": "addons"}},
        user=plain,
    )

    assert result.errors is not None
    with system_context(reason="test.marketplace.deny.verify"):
        assert not Source.objects.filter(kind="addon").exists()


def _schema() -> Any:
    """Build the merged platform + VCS-marketplace ``console`` schema for these tests."""

    addons = [
        SchemaAddon({"console": {key: tuple(module.schemas["console"].get(key, ())) for key in SCHEMA_PART_KEYS}})
        for module in (platform_schema, vcs_schema)
    ]
    return GraphQLSchemas(addons).build("console")


def _execute(schema: Any, query: str, variables: dict[str, Any] | None = None, *, user: Any | None = None) -> Any:
    """Execute one operation against the merged console schema as ``user``."""

    request = RequestFactory().post("/graphql/console/")
    request.user = user or AnonymousUser()
    return execute_schema(schema, query, variables, request=request)


def _public_id(value: Any) -> str:
    """Return the public id mutations resolve for ``value``."""

    return str(getattr(value, "sqid", value))
