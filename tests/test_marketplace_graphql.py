"""Marketplace source console mutations — ``addSource`` + ``scan``.

The VCS marketplace tier contributes two admin-gated source controls onto the
platform console, composing integrate's existing owners (it never re-implements
bridge/repo creation or metadata projection):

- ``addSource`` inventories a repository on an existing ``VcsBridge``
  (``import_repository``) and points an addon ``Source`` at it;
- ``scan`` runs ``Source.refresh()`` (→ ``AddonCatalog.sync_from_source``), which
  projects built wheel metadata into the one ``platform.Addon`` marketplace registry.

Driven over the native local VCS backend with already-built wheel fixtures, the
same path the ``marketplace sync`` command and integrate's ``refresh_source`` use.
"""

from __future__ import annotations

import importlib
from pathlib import Path
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
from tests.wheel_fixtures import write_wheel

User = get_user_model()
platform_schema = importlib.import_module("angee.platform.schema")
vcs_schema = importlib.import_module("angee.platform_integrate_vcs.schema")
Addon = apps.get_model("platform", "Addon")
VcsBridge = apps.get_model("integrate_vcs", "VcsBridge")

_ADD_SOURCE = """
mutation AddSource($data: AddonSourceInput!) {
  add_source(data: $data) { ok message }
}
"""
_SCAN = "mutation($id: ID!){ scan(source_id: $id){ ok message } }"


def _bridge(root: Path) -> Any:
    """Create a native local VCS bridge for the fixture repository."""

    return make_integration(
        "mkt",
        model=VcsBridge,
        backend_class="local",
        config={"local_root": str(root), "local_name": "acme/addons", "local_org": "acme"},
    )


def test_add_source_inventories_the_repo_and_points_an_addon_source_at_it(
    composed_tables: None, tmp_path: Path,
) -> None:
    """``addSource`` imports the repo and creates one ``Source(kind="addon")``."""

    del composed_tables
    bridge = _bridge(tmp_path)
    admin = _platform_admin("add-source-admin")

    result = _data(
        _execute(
            _schema(),
            _ADD_SOURCE,
            {"data": {"vcs_bridge_id": _public_id(bridge), "name": "acme/addons", "path": "src"}},
            user=admin,
        )
    )["add_source"]

    assert result["ok"] is True
    assert "acme/addons" in result["message"]
    with system_context(reason="test.marketplace.add_source.verify"):
        repository = Repository.objects.get(name="acme/addons")
        source = Source.objects.get(repository=repository)
    assert (source.kind, source.path) == ("addon", "src")


def test_scan_prepares_native_package_metadata_into_the_registry(
    composed_tables: None, tmp_path: Path,
) -> None:
    """``scan`` projects an already-built wheel into a REMOTE ``platform.Addon`` row."""

    del composed_tables
    write_wheel(
        tmp_path / "src/angee_demo.whl", "angee.demo", "angee-demo",
        requirements=("angee-platform",), description="A demo addon.",
    )
    bridge = _bridge(tmp_path)
    admin = _platform_admin("scan-admin")
    _data(
        _execute(
            _schema(),
            _ADD_SOURCE,
            {"data": {"vcs_bridge_id": _public_id(bridge), "name": "acme/addons", "path": "src"}},
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
    assert str(row.source) == Addon.AddonSource.REMOTE
    assert row.vcs_path == "src/angee_demo.whl"
    assert row.distribution == "angee-demo"
    assert row.description == "A demo addon."
    assert row.depends_on == ["angee.platform"]


def test_scan_refuses_a_non_addon_source(
    composed_tables: None, tmp_path: Path,
) -> None:
    """``scan`` of a non-addon source kind is reported, never dispatched."""

    del composed_tables
    bridge = _bridge(tmp_path)
    admin = _platform_admin("scan-wrong-kind-admin")
    with system_context(reason="test.marketplace.scan.template_seed"):
        repository = Repository.objects.create(vcs_bridge=bridge, org="acme", name="acme/addons", remote="r")
        source = Source.objects.create(repository=repository, kind="template", path="templates")

    result = _data(_execute(_schema(), _SCAN, {"id": _public_id(source)}, user=admin))["scan"]

    assert result["ok"] is False
    assert "not an addon source" in result["message"].lower()


def test_add_source_denies_a_non_admin(
    composed_tables: None, tmp_path: Path,
) -> None:
    """The REBAC admin gate denies a non-admin actor."""

    del composed_tables
    bridge = _bridge(tmp_path)
    plain = User.objects.create_user(username="mkt-plain", password="mkt-plain")

    result = _execute(
        _schema(),
        _ADD_SOURCE,
        {"data": {"vcs_bridge_id": _public_id(bridge), "name": "acme/addons", "path": "src"}},
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
