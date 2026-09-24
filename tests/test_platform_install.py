"""Install/disable console mutations — ``settings.yaml`` is the install source.

``install``/``disable`` are thin admin-gated edges over the addon manager, which
uses ``AddonInstaller`` to edit ``settings.yaml`` while preserving comments.
These drive the resolvers over the concrete ``platform.Addon``
reflection table the way the composed console does:

- install appends an available addon's root to a temp ``settings.yaml`` (it would
  compose on the next boot) and the in-process reconcile flips its reflected row to
  ``pending``;
- disable removes the root;
- an addon required by the loaded graph refuses disable regardless of catalogue state;
- a non-admin actor is denied by the REBAC gate.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory
from hatch_angee import AddonManifest
from rebac import system_context, to_object_ref

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.platform import models as platform_models
from angee.platform.models import _preview_revision
from tests.conftest import PLATFORM_TEST_MODELS, SchemaAddon, execute_schema
from tests.conftest import _create_missing_tables as _create_tables
from tests.conftest import create_platform_admin as _platform_admin
from tests.conftest import result_data as _data

User = get_user_model()
platform_schema = importlib.import_module("angee.platform.schema")
Addon = apps.get_model("platform", "Addon")

# An installed bundle that is available (advertised via entry points) but not enabled
# in the test ``INSTALLED_APPS`` — the marketplace "to install" case.
_AVAILABLE_ADDON = "angee.knowledge_graph_pgvector"

_SETTINGS_YAML = """\
# Project composition facts — operator comments must survive an install edit.
SECRET_KEY: dev-key

INSTALLED_APPS:
  - angee.platform  # the console host
ANGEE_RUNTIME_DIR: "{BASE_DIR}/runtime"
"""

_INSTALL = "mutation($addon: String!){ install(addon: $addon){ ok message } }"
_DISABLE = "mutation($addon: String!){ disable(addon: $addon){ ok message } }"


@pytest.fixture()
def platform_tables(transactional_db: Any) -> Iterator[None]:
    """Create the ``platform.Addon`` reflection table and sync the REBAC schema."""

    del transactional_db
    created = _create_tables(PLATFORM_TEST_MODELS)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        if created:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created):
                    schema_editor.delete_model(model)


@pytest.fixture()
def project_settings_yaml(tmp_path: Path, settings: Any) -> Path:
    """Point the local installer at a temp ``settings.yaml`` and return its path."""

    path = tmp_path / "settings.yaml"
    path.write_text(_SETTINGS_YAML, encoding="utf-8")
    settings.BASE_DIR = tmp_path
    settings.ANGEE_PROJECT_YAML_SETTINGS = frozenset({"INSTALLED_APPS"})
    return path


@pytest.mark.parametrize("label", [None, "", "remote_label"])
def test_change_impact_uses_addon_display_label(label: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Preview and catalogue labels agree for resolved and unresolved addons."""

    name = "example.remote"
    config = SimpleNamespace(label=label) if label is not None else None
    monkeypatch.setattr(platform_models, "resolve_app_config", lambda *_args, **_kwargs: config)
    impacts = Addon.objects._change_impacts([name], {name: AddonManifest(name=name)}, [name], {}, {})
    assert impacts[0].label == (label or name)


def test_install_appends_the_root_and_reflects_pending(
    platform_tables: None,
    project_settings_yaml: Path,
) -> None:
    """Install adds the root to ``settings.yaml`` and flips the reflected row to pending."""

    del platform_tables
    admin = _platform_admin("install-admin")

    result = _data(_execute(_schema(), _INSTALL, {"addon": _AVAILABLE_ADDON}, user=admin))["install"]

    assert result["ok"] is True
    assert _AVAILABLE_ADDON in result["message"]
    # The root now sits in INSTALLED_APPS (it composes on the next boot), comments intact.
    text = project_settings_yaml.read_text(encoding="utf-8")
    assert _AVAILABLE_ADDON in text
    assert "# the console host" in text
    # The in-process reconcile reflects the desired-but-not-composed addon as pending.
    with system_context(reason="test.platform.install.verify"):
        row = Addon.objects.get(name=_AVAILABLE_ADDON)
    assert row.pending is True
    assert str(row.state) == Addon.State.DISABLED  # not composed until the next boot
    assert to_object_ref(row).resource_id == str(row.pk)


def test_install_is_idempotent_for_an_already_listed_root(
    platform_tables: None,
    project_settings_yaml: Path,
) -> None:
    """Installing a root already in ``INSTALLED_APPS`` reports the no-op, file unchanged."""

    del platform_tables
    admin = _platform_admin("install-idempotent-admin")
    before = project_settings_yaml.read_text(encoding="utf-8")

    result = _data(_execute(_schema(), _INSTALL, {"addon": "angee.platform"}, user=admin))["install"]

    assert result["ok"] is True
    assert "already installed" in result["message"]
    assert project_settings_yaml.read_text(encoding="utf-8") == before


def test_install_refuses_a_non_materialised_addon(
    platform_tables: None,
    project_settings_yaml: Path,
) -> None:
    """Install of a name no bundle/local addon provides is refused; the file is untouched.

    A marketplace (``REMOTE``) or mistyped name is not importable, so adding it to
    ``INSTALLED_APPS`` would brick the next boot — the manager validates against the
    available set and refuses before any edit.
    """

    del platform_tables
    admin = _platform_admin("install-unknown-admin")
    before = project_settings_yaml.read_text(encoding="utf-8")

    result = _data(_execute(_schema(), _INSTALL, {"addon": "not.a.real.addon"}, user=admin))["install"]

    assert result["ok"] is False
    assert "not available" in result["message"]
    assert project_settings_yaml.read_text(encoding="utf-8") == before  # refusal never edits


def test_disable_preview_refuses_an_unknown_addon(
    platform_tables: None,
    project_settings_yaml: Path,
) -> None:
    """A crafted Disable target is a refusal rather than a successful no-op."""

    del platform_tables, project_settings_yaml
    with system_context(reason="test.platform.disable-preview.unknown"):
        preview = Addon.objects.change_preview("not.a.real.addon", "disable")

    assert preview.can_apply is False
    assert preview.refusal
    assert preview.roots_after == preview.roots_before


def test_preview_revision_binds_every_change_decision() -> None:
    """A confirmation receipt cannot authorize another action, target, or snapshot."""

    revision = _preview_revision
    manifest = AddonManifest(name="example.demo", description="original")
    baseline = revision(
        "INSTALLED_APPS: []\n",
        (manifest,),
        action="install",
        addon="example.demo",
        roots_after=("example.demo",),
    )
    alternatives = (
        revision(
            "INSTALLED_APPS: []\n",
            (manifest,),
            action="disable",
            addon="example.demo",
            roots_after=("example.demo",),
        ),
        revision(
            "INSTALLED_APPS: []\n",
            (manifest,),
            action="install",
            addon="example.other",
            roots_after=("example.demo",),
        ),
        revision(
            "INSTALLED_APPS:\n  - angee.platform\n",
            (manifest,),
            action="install",
            addon="example.demo",
            roots_after=("example.demo",),
        ),
        revision(
            "INSTALLED_APPS: []\n",
            (AddonManifest(name="example.demo", description="changed"),),
            action="install",
            addon="example.demo",
            roots_after=("example.demo",),
        ),
        revision(
            "INSTALLED_APPS: []\n",
            (AddonManifest(name="example.demo", description="original", depends_on=("example.dependency",)),),
            action="install",
            addon="example.demo",
            roots_after=("example.demo",),
        ),
        revision(
            "INSTALLED_APPS: []\n",
            (manifest,),
            action="install",
            addon="example.demo",
            roots_after=("example.demo", "example.other"),
        ),
    )

    assert all(candidate != baseline for candidate in alternatives)
    assert len(set(alternatives)) == len(alternatives)


def test_data_inventory_lists_donor_fields_separately(monkeypatch: pytest.MonkeyPatch) -> None:
    """A disabled donor reports copied fields without claiming ownership of the target model."""

    config = SimpleNamespace(name="example.donor")
    monkeypatch.setattr(platform_models.composed, "addons", lambda: [config])
    monkeypatch.setattr(platform_models.composed, "data_models", lambda owner: [])
    monkeypatch.setattr(
        platform_models.composed,
        "contributed_fields",
        lambda owner: [
            SimpleNamespace(
                model_label="example_owner.record",
                field_name="external_id",
                verbose_name="External ID",
            )
        ],
    )

    inventory = Addon.objects._data_inventory((config.name,))

    assert inventory[0].models == ()
    assert inventory[0].contributed_fields[0].model_label == "example_owner.record"
    assert inventory[0].contributed_fields[0].field_name == "external_id"


def test_disable_removes_the_root(
    platform_tables: None,
    project_settings_yaml: Path,
) -> None:
    """Disable drops the root from ``settings.yaml`` (it leaves on the next boot)."""

    del platform_tables
    # Seed an extra root so there is something to disable in the file.
    _data(_execute(_schema(), _INSTALL, {"addon": _AVAILABLE_ADDON}, user=_platform_admin("seed-admin")))
    admin = _platform_admin("disable-admin")

    result = _data(_execute(_schema(), _DISABLE, {"addon": _AVAILABLE_ADDON}, user=admin))["disable"]

    assert result["ok"] is True
    assert "Disabled" in result["message"]
    assert _AVAILABLE_ADDON not in project_settings_yaml.read_text(encoding="utf-8")


@pytest.mark.parametrize("persisted_forced", [True, False, None])
def test_disable_refuses_an_addon_required_by_the_loaded_graph(
    platform_tables: None,
    project_settings_yaml: Path,
    monkeypatch: pytest.MonkeyPatch,
    persisted_forced: bool | None,
) -> None:
    """The loaded dependency graph refuses disable even with a stale or absent row."""

    del platform_tables
    admin = _platform_admin("forced-admin")
    monkeypatch.setattr(apps.get_app_config("iam"), "angee_forced", True, raising=False)
    if persisted_forced is not None:
        with system_context(reason="test.platform.forced.seed"):
            Addon.objects.update_or_create(
                name="angee.iam",
                defaults={"forced": persisted_forced, "state": Addon.State.ENABLED},
            )
    before = project_settings_yaml.read_text(encoding="utf-8")

    result = _data(_execute(_schema(), _DISABLE, {"addon": "angee.iam"}, user=admin))["disable"]

    assert result["ok"] is False
    assert "cannot be disabled" in result["message"]
    assert "angee.platform" in result["message"]  # a dependant from the loaded manifest
    assert project_settings_yaml.read_text(encoding="utf-8") == before  # refusal never edits


def test_disable_ignores_stale_catalogue_dependency_flags(
    platform_tables: None,
    project_settings_yaml: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale forced row and catalogue reverse edges cannot block a free loaded root."""

    del platform_tables
    admin = _platform_admin("stale-forced-admin")
    monkeypatch.setattr(apps.get_app_config("platform"), "angee_forced", False, raising=False)
    with system_context(reason="test.platform.stale-forced.seed"):
        Addon.objects.update_or_create(
            name="angee.platform",
            defaults={
                "forced": True,
                "depended_by": ["example.disabled"],
                "state": Addon.State.ENABLED,
            },
        )

    result = _data(_execute(_schema(), _DISABLE, {"addon": "angee.platform"}, user=admin))["disable"]

    assert result["ok"] is True
    assert "Disabled" in result["message"]
    assert "angee.platform" not in project_settings_yaml.read_text(encoding="utf-8")


def test_install_denies_a_non_admin(
    platform_tables: None,
    project_settings_yaml: Path,
) -> None:
    """The REBAC admin gate denies a non-admin actor (the file stays unedited)."""

    del platform_tables
    plain = User.objects.create_user(username="plain-user", password="plain-user")
    before = project_settings_yaml.read_text(encoding="utf-8")

    result = _execute(_schema(), _INSTALL, {"addon": _AVAILABLE_ADDON}, user=plain)

    assert result.errors is not None
    assert project_settings_yaml.read_text(encoding="utf-8") == before


def _schema() -> Any:
    """Build the platform ``console`` schema for these tests."""

    parts = {key: tuple(platform_schema.schemas["console"].get(key, ())) for key in SCHEMA_PART_KEYS}
    return GraphQLSchemas([SchemaAddon({"console": parts})]).build("console")


def _execute(schema: Any, query: str, variables: dict[str, Any] | None = None, *, user: Any | None = None) -> Any:
    """Execute one operation against the platform console schema as ``user``."""

    request = RequestFactory().post("/graphql/console/")
    request.user = user or AnonymousUser()
    return execute_schema(schema, query, variables, request=request)
