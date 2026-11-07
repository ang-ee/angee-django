"""Angee permission, role, resource and web capabilities compose native owners."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError, models
from django_compose.components import enabled_apps
from django_compose.declarations import model_declaration
from django_compose.model_composition import ModelComposition
from django_compose.models import Composable
from django_compose.rendering import render_models

from angee.base.build import PermissionIntegration, composed_grants
from angee.base.models.base import AngeeModel
from angee.base.models.roles import role_anchor
from angee.web.build import WebRuntime
from tests.conftest import make_addon


class FirstRenderPlanMetaThing(AngeeModel, concrete=True):
    """Source-owned Meta must survive the native permission declaration hook."""

    class Meta:
        abstract = True
        app_label = "tests"
        rebac_resource_type = "tests/first-render-plan"


class SecondRenderPlanMetaThing(AngeeModel, concrete=True):
    """A second model keeps its own permission type in the same module."""

    class Meta:
        abstract = True
        app_label = "tests"
        rebac_resource_type = "tests/second-render-plan"


def _source_model(module, name, label, *, concrete=False, extends=None, inherits=None, **body):
    declaration = {"concrete": True} if concrete else {}
    if extends is not None:
        declaration["extends"] = extends
    if inherits is not None:
        declaration["inherits"] = inherits
    model = type(name, (Composable, models.Model), {
        "__module__": module.__name__,
        "Meta": type("Meta", (), {"abstract": True, "app_label": label}),
        **body,
    }, **declaration)
    setattr(module, name, model)
    return cast(type[models.Model], model)


def _web_runtime(configs, **options):
    """Select native fixture configs before asking the web owner for its projection."""

    closure = enabled_apps(tuple(config.name for config in configs), group="angee.addons")
    return WebRuntime(configs, dependencies=closure.dependencies, **options)


def test_runtime_model_render_plan_keeps_model_owned_meta(tmp_path: Path) -> None:
    """Each named render plan carries the Meta facts for its own model."""

    composition = ModelComposition({"tests": (FirstRenderPlanMetaThing, SecondRenderPlanMetaThing)}, {})
    source = render_models(composition, "tests", declarations={
        model: model_declaration(composition, model, (PermissionIntegration(),))
        for model in composition.ordered_models
    })

    first_source = source[
        source.index("class FirstRenderPlanMetaThing") : source.index("class SecondRenderPlanMetaThing")
    ]
    assert "rebac_resource_type = 'tests/first-render-plan'" in first_source
    assert "rebac_resource_type = 'tests/second-render-plan'" not in first_source
    assert "rebac_resource_type = 'tests/second-render-plan'" in source


def test_runtime_composes_additive_grants_without_parent_authority(monkeypatch) -> None:
    """Addon grants belong to the extended record, never its MTI descendants."""

    module = ModuleType("tests.grant_composition")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    parent = _source_model(module, "GrantParent", "tests", concrete=True, rebac_grantable={"viewer": "share"})
    donor = _source_model(
        module,
        "GrantDonor",
        "tests",
        extends="tests.GrantParent",
        rebac_grantable={"reviewer": "write"},
    )
    child = type("GrantChild", (Composable, models.Model), {
        "__module__": module.__name__,
        "Meta": type("Meta", (), {"abstract": True, "app_label": "tests"}),
    }, inherits="tests.GrantParent")
    module.GrantChild = child
    composition = ModelComposition(
        {"tests": (parent, child)}, {"tests.grantparent": (donor,)}, model_owners={donor: module.__name__},
    )

    assert composed_grants(composition, parent) == {"viewer": "share", "reviewer": "write"}
    assert composed_grants(composition, child) == {}
    rendered = render_models(composition, "tests", declarations={
        model: model_declaration(composition, model, (PermissionIntegration(),))
        for model in composition.ordered_models
    })
    assert "rebac_grantable = {'reviewer': 'write', 'viewer': 'share'}" in rendered
    assert "rebac_grantable = {}" in rendered[rendered.index("class GrantChild("):]


def test_runtime_rejects_conflicting_donor_grant_authority(monkeypatch) -> None:
    """An addon cannot silently weaken an existing relationship's grant gate."""

    module = ModuleType("tests.grant_conflict")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    source = _source_model(module, "GrantSource", "tests", concrete=True, rebac_grantable={"reviewer": "admin"})
    donor = _source_model(
        module,
        "GrantConflict",
        "tests",
        extends="tests.GrantSource",
        rebac_grantable={"reviewer": "write"},
    )
    composition = ModelComposition(
        {"tests": (source,)}, {"tests.grantsource": (donor,)}, model_owners={donor: module.__name__},
    )
    with pytest.raises(ImproperlyConfigured, match="conflicting rebac_grantable"):
        model_declaration(composition, source, (PermissionIntegration(),))


@pytest.mark.parametrize("invalid", [None, [], {"": "write"}, {"reviewer": 42}])
def test_runtime_validates_donor_grants_with_model_owner(invalid: Any, monkeypatch) -> None:
    """Malformed addon declarations retain the model's configuration error contract."""

    module = ModuleType("tests.grant_invalid")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    source = _source_model(module, "GrantValid", "tests", concrete=True)
    donor = _source_model(module, "GrantInvalid", "tests", extends="tests.GrantValid", rebac_grantable=invalid)
    composition = ModelComposition(
        {"tests": (source,)}, {"tests.grantvalid": (donor,)}, model_owners={donor: module.__name__},
    )
    with pytest.raises(ImproperlyConfigured, match="rebac_grantable"):
        model_declaration(composition, source, (PermissionIntegration(),))


def test_web_runtime_projects_addon_web_packages_in_composed_order() -> None:
    """Addon web package declarations feed one generated web manifest."""

    first = make_addon(name="tests.first", web={"package": "@demo/first"})
    backend_only = make_addon(name="tests.backend")
    second = make_addon(name="tests.second", web={"package": "@demo/second"})

    manifest = _web_runtime((first, backend_only, second)).manifest_json()

    assert manifest.index('"package": "@demo/first"') < manifest.index('"package": "@demo/second"')
    assert "tests.backend" not in manifest
    # The composer holds no schema-name knowledge — the CLI discovers schemas
    # from the SDL on disk — so the manifest carries no schema list.
    assert '"schemas"' not in manifest


def test_web_runtime_projects_addon_web_root_relative_to_runtime(
    tmp_path: Path,
) -> None:
    """An addon's resolved Django path becomes a relocatable manifest web root."""

    addon = make_addon(name="tests.addon", path=tmp_path / "addon", web={"package": "@demo/addon"})

    manifest = json.loads(_web_runtime((addon,), runtime_dir=tmp_path / "runtime").manifest_json())

    assert manifest["addonPackages"] == [
        {
            "app": "tests.addon",
            "label": "addon",
            "dependsOn": [],
            "package": "@demo/addon",
            "root": "../../addon/web",
            "sourceRoot": "src",
        }
    ]


def test_web_runtime_projects_package_ancestry_through_backend_only_addons() -> None:
    """Each web package lists the web packages it depends on, transitively, by app name."""

    base = make_addon(name="tests.base", web={"package": "@demo/base"})
    bridge = make_addon(name="tests.bridge", depends_on=("tests.base",))
    product = make_addon(name="tests.product", depends_on=("tests.bridge",), web={"package": "@demo/product"})
    unrelated = make_addon(name="tests.unrelated", web={"package": "@demo/unrelated"})

    manifest = json.loads(_web_runtime((base, bridge, product, unrelated)).manifest_json())

    assert {entry["app"]: entry["dependsOn"] for entry in manifest["addonPackages"]} == {
        "tests.base": [],
        "tests.product": ["tests.base"],
        "tests.unrelated": [],
    }


def test_nexus_aggregation_dependencies_reach_web_runtime() -> None:
    """Nexus alone selects every web package whose menu it includes."""

    configs = enabled_apps(("angee.nexus",), group="angee.addons").app_configs
    manifest = _web_runtime(configs).manifest
    nexus = next(entry for entry in manifest["addonPackages"] if entry["app"] == "angee.nexus")
    assert {"angee.messaging", "angee.parties", "angee.spaces", "angee.posts"} <= set(nexus["dependsOn"])


def test_oidc_web_documents_follow_the_enabled_backend_addon() -> None:
    """An IAM-only host never scans operations belonging to the optional OIDC schema."""

    iam_configs = enabled_apps(("angee.iam",), group="angee.addons").app_configs
    oidc_configs = enabled_apps(("angee.iam_integrate_oidc",), group="angee.addons").app_configs
    iam_manifest = _web_runtime(iam_configs).manifest
    oidc_manifest = _web_runtime(oidc_configs).manifest

    assert "@angee/iam-integrate-oidc" not in {entry["package"] for entry in iam_manifest["addonPackages"]}
    oidc = next(entry for entry in oidc_manifest["addonPackages"] if entry["app"] == "angee.iam_integrate_oidc")
    assert oidc["package"] == "@angee/iam-integrate-oidc"
    assert {"angee.iam", "angee.integrate", "angee.parties"} <= set(oidc["dependsOn"])
    iam_config = next(config for config in iam_configs if config.name == "angee.iam")
    assert not (Path(iam_config.path) / "web/src/documents.public.ts").exists()
    oidc_config = next(config for config in oidc_configs if config.name == "angee.iam_integrate_oidc")
    assert (Path(oidc_config.path) / "web/src/documents.public.ts").is_file()


def test_web_runtime_carries_the_deployment_ui_layer() -> None:
    """``ANGEE_UI`` reaches the web runtime as the last layer; unknown keys fail composition."""

    addon = make_addon(name="tests.addon", web={"package": "@demo/addon"})
    ui = {
        "shell": {
            "brand": {"name": "Suite", "mark": "suite"},
            "theme": "suite.light",
            "apps": {"payables": {"rail": ("accounting", "files"), "home": "accounting.bills", "railExpanded": False}},
            "hosts": {"payables.example.test": "payables", "work.example.test": "pm"},
        }
    }

    deployment = json.loads(_web_runtime((addon,), ui=ui).manifest_json())["deployment"]
    assert deployment["shell"]["apps"]["payables"]["rail"] == ["accounting", "files"]
    assert deployment["shell"]["hosts"] == {"payables.example.test": "payables", "work.example.test": "pm"}
    assert "deployment" not in json.loads(_web_runtime((addon,), ui={}).manifest_json())
    with pytest.raises(ImproperlyConfigured, match="unknown keys \\['rail'\\]"):
        _web_runtime((addon,), ui={"rail": {}})
    with pytest.raises(ImproperlyConfigured, match="unknown keys \\['perspectives'\\]"):
        _web_runtime((addon,), ui={"perspectives": {"pm": {"root": "pm"}}})
    with pytest.raises(ImproperlyConfigured, match="must be a mapping"):
        _web_runtime((addon,), ui=["shell"])


def test_web_runtime_transports_shell_values_for_the_shared_js_validator() -> None:
    """Python owns JSON transport; app boot and codegen share shell validation."""

    addon = make_addon(name="tests.addon", web={"package": "@demo/addon"})
    assert _web_runtime((addon,), ui={"shell": {"apps": {"ap": {"railExpanded": False}}}}).manifest[
        "deployment"
    ]["shell"]["apps"]["ap"]["railExpanded"] is False
    with pytest.raises(ImproperlyConfigured, match="only JSON values"):
        _web_runtime((addon,), ui={"shell": {"not_json": object()}})


def test_web_runtime_projects_external_codegen_entries() -> None:
    """An addon's web_codegen declaration projects into the manifest."""

    daemon = make_addon(
        name="tests.daemon",
        web={
            "package": "@demo/daemon",
            "codegen": {
                "schema": "operator",
                "sdl": "schema/operator.graphql",
                "documents": "documents.daemon.ts",
                "types": True,
            },
        },
    )

    manifest = _web_runtime((daemon,)).manifest_json()

    assert '"schema": "operator"' in manifest
    assert '"package": "@demo/daemon"' in manifest
    assert '"sdl": "schema/operator.graphql"' in manifest
    assert '"documents": "documents.daemon.ts"' in manifest
    assert '"app": "tests.daemon"' in manifest


def test_web_runtime_rejects_codegen_without_web_package() -> None:
    """An external codegen entry requires its addon to ship a web package."""

    daemon = make_addon(
        name="tests.daemon",
        web={
            "codegen": {"schema": "operator", "sdl": "s.graphql", "documents": "d.ts"},
        },
    )

    with pytest.raises(ImproperlyConfigured, match=r"name must be a valid npm package name"):
        _web_runtime((daemon,))


def test_web_runtime_rejects_duplicate_addon_web_packages() -> None:
    """Two addons cannot claim the same web package identity."""

    first = make_addon(name="tests.first", web={"package": "@demo/shared"})
    second = make_addon(name="tests.second", web={"package": "@demo/shared"})

    with pytest.raises(ImproperlyConfigured, match=r"Duplicate web/package.json name"):
        _web_runtime((first, second))


def test_web_runtime_rejects_invalid_package_names() -> None:
    """The web package contract fails before a broken manifest is emitted."""

    broken = make_addon(name="tests.broken", web={"package": "../broken"})

    with pytest.raises(ImproperlyConfigured, match="valid npm package name"):
        _web_runtime((broken,))


def test_role_anchor_factory_pins_the_hand_rolled_anchor_shape() -> None:
    """``role_anchor`` emits the abstract, table-less anchor the adopters declared by hand."""

    anchor = role_anchor("storage/role")

    assert anchor.__name__ == "StorageRole"
    assert anchor.__module__ == __name__
    assert anchor._meta.abstract is True
    assert anchor._meta.managed is False
    assert anchor._meta.rebac_resource_type == "storage/role"
    assert anchor.__dict__["compose_concrete"] is True
    assert issubclass(anchor, AngeeModel)
    # The name derives from the resource type; a symbol that differs is overridable.
    assert role_anchor("operator/role").__name__ == "OperatorRole"
    assert role_anchor("tags/role", name="Role").__name__ == "Role"


def test_role_anchor_emits_the_hand_rolled_runtime_source(tmp_path: Path, monkeypatch) -> None:
    """A ``role_anchor`` model composes into the same concrete runtime an addon shipped by hand."""

    module = ModuleType("tests.role_anchor_probe")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    probe = role_anchor("tests/role", name="ProbeRole", module=module.__name__)
    setattr(module, "ProbeRole", probe)

    composition = ModelComposition({"tests": (probe,)}, {})
    source = render_models(composition, "tests", declarations={
        probe: model_declaration(composition, probe, (PermissionIntegration(),)),
    })

    assert "from tests.role_anchor_probe import ProbeRole as _SourceProbeRole" in source
    assert "class ProbeRole(_SourceProbeRole):" in source
    assert "class Meta(_SourceProbeRole.Meta):" in source
    assert "abstract = False" in source
    assert "rebac_resource_type = 'tests/role'" in source


def test_role_anchor_wrapper_miscapture_fails_at_emission(tmp_path: Path) -> None:
    """A ``role_anchor`` whose captured module does not bind it fails loudly (F-b).

    A wrapper indirecting ``role_anchor`` makes ``sys._getframe`` capture the
    wrapper's module, not the adopter's, so the emitted import would resolve to
    nothing. The composer proves the captured module actually binds the anchor and
    refuses to emit a broken import.
    """

    module = ModuleType("tests.role_anchor_wrapper_probe")
    sys.modules[module.__name__] = module
    try:
        # The anchor claims this module, but the symbol is never bound there (the
        # mis-capture a wrapper would produce).
        stray = role_anchor("tests/role", name="StrayRole", module=module.__name__)
        with pytest.raises(ImproperlyConfigured, match="is not importable"):
            render_models(ModelComposition({"tests": (stray,)}, {}), "tests")
    finally:
        del sys.modules[module.__name__]


def test_tailwind_sources_resolve_workspace_package_links(tmp_path: Path) -> None:
    """The watcher sees real package source directories, not dependency symlinks."""
    runtime = tmp_path / "runtime"
    for package in ("app", "ui"):
        source = tmp_path / "slot" / "packages" / package
        (source / "src").mkdir(parents=True)
        link = tmp_path / "web" / "node_modules" / "@angee" / package
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(source, target_is_directory=True)
    css = _web_runtime((), runtime_dir=runtime).tailwind_sources_css()
    assert '@source "../../slot/packages/ui/src";' in css
    assert '@source "../../slot/packages/app/src";' in css
    assert "node_modules" not in css
    assert css == _web_runtime((), runtime_dir=runtime).tailwind_sources_css()




@pytest.mark.parametrize(("label", "name", "resource_type"), [
    ("resources", "Resource", None),
    ("iam", "User", "auth/user"),
])
def test_capability_models_render_with_native_permission_meta(label, name, resource_type):
    config = apps.get_app_config(label)
    composition = ModelComposition.discover((config,))
    source = render_models(composition, label, declarations={
        model: model_declaration(composition, model, (PermissionIntegration(),))
        for model in composition.ordered_models
    })
    assert f"class {name}(" in source
    assert f"app_label = {label!r}" in source
    if resource_type is not None:
        assert f"rebac_resource_type = {resource_type!r}" in source
    assert "swappable = 'AUTH_USER_MODEL'" not in source


def _configured_namespace(root: Path, **authored):
    from angee.base.conf import configure

    namespace = {
        "__file__": str(root / "settings.py"), "__name__": "native_capability_settings",
        "COMPOSE_OUTPUT_DIR": root / "runtime", "INSTALLED_APPS": ["angee.base"],
        **authored,
    }
    configure(namespace)
    return namespace


def test_project_env_file_loads_without_overriding_process_env(tmp_path, monkeypatch):
    """The authored settings directory owns .env; explicit process inputs win."""

    import os

    monkeypatch.setenv("DJANGO_COMPOSE_MODE", "write")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("YAMLCONF_SECRET_KEY", "from-process-env")
    (tmp_path / ".env").write_text(
        'DATABASE_URL="postgres://angee:pw@127.0.0.1:5433/angee"\nYAMLCONF_SECRET_KEY="from-env-file"\n',
    )
    try:
        namespace = _configured_namespace(tmp_path)
        assert os.environ["DATABASE_URL"] == "postgres://angee:pw@127.0.0.1:5433/angee"
        assert namespace["SECRET_KEY"] == "from-process-env"
        assert namespace["DATABASES"]["default"]["PORT"] == 5433
    finally:
        os.environ.pop("DATABASE_URL", None)


def test_project_env_file_is_optional(tmp_path, monkeypatch):
    monkeypatch.setenv("DJANGO_COMPOSE_MODE", "write")
    assert _configured_namespace(tmp_path)["COMPOSE_ROOTS"] == ("angee.base",)


def test_database_pool_follows_authored_settings_and_native_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DJANGO_COMPOSE_MODE", "write")
    monkeypatch.setenv("DATABASE_URL", "postgres://user:password@localhost:5432/db")
    monkeypatch.delenv("ANGEE_DB_POOL", raising=False)
    assert "pool" not in _configured_namespace(tmp_path)["DATABASES"]["default"]["OPTIONS"]
    monkeypatch.setenv("ANGEE_DB_POOL", "true")
    monkeypatch.setenv("ANGEE_DB_POOL_MAX_SIZE", "3")
    monkeypatch.setenv("ANGEE_DB_POOL_TIMEOUT", "7")
    database = _configured_namespace(tmp_path)["DATABASES"]["default"]
    assert database["OPTIONS"]["pool"] == {"min_size": 0, "max_size": 3, "timeout": 7.0}
    assert "CONN_MAX_AGE" not in database and "CONN_HEALTH_CHECKS" not in database
    monkeypatch.setenv("DATABASE_URL", "postgres://user:password@localhost:5432/db?conn_max_age=600")
    with pytest.raises(ImproperlyConfigured, match="ANGEE_DB_POOL.*DATABASE_URL CONN_MAX_AGE"):
        _configured_namespace(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgres://user:password@localhost:5432/db?conn_max_age=0")
    assert _configured_namespace(tmp_path)["DATABASES"]["default"]["CONN_MAX_AGE"] == 0
    monkeypatch.setenv("ANGEE_DB_POOL", "false")
    assert "pool" not in _configured_namespace(tmp_path)["DATABASES"]["default"]["OPTIONS"]
    monkeypatch.delenv("ANGEE_DB_POOL")
    assert "pool" not in _configured_namespace(tmp_path, ANGEE_DB_POOL="false")["DATABASES"]["default"]["OPTIONS"]
    assert _configured_namespace(tmp_path, ANGEE_DB_POOL=True, ANGEE_DB_POOL_MAX_SIZE=4)[
        "DATABASES"]["default"]["OPTIONS"]["pool"]["max_size"] == 3
    monkeypatch.delenv("ANGEE_DB_POOL_MAX_SIZE")
    assert _configured_namespace(tmp_path, ANGEE_DB_POOL=True, ANGEE_DB_POOL_MAX_SIZE=4)[
        "DATABASES"]["default"]["OPTIONS"]["pool"]["max_size"] == 4
    explicit = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(tmp_path / "app.sqlite3")}}
    assert _configured_namespace(tmp_path, DATABASES=explicit)["DATABASES"] == explicit


@pytest.fixture
def postgres_pool_connection() -> Any:
    from django.db import connections

    alias = "angee_pool_probe"
    connections.databases[alias] = {
        **connections.databases["default"],
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "unreachable",
        "HOST": "127.0.0.1",
        "PORT": "1",
        "USER": "probe",
        "PASSWORD": "probe",
        "CONN_MAX_AGE": 0,
        "OPTIONS": {"pool": {"min_size": 0, "max_size": 2, "timeout": 0.2}},
    }
    connection = connections[alias]
    try:
        yield connection
    finally:
        connection.close_pool()
        del connections[alias]
        del connections.databases[alias]



def test_postgres_connection_builds_native_pool(postgres_pool_connection: Any) -> None:
    from psycopg_pool import ConnectionPool

    pool = postgres_pool_connection.pool
    assert isinstance(pool, ConnectionPool)
    assert pool.min_size == 0
    assert pool.max_size == 2
    assert pool.timeout == 0.2



def test_postgres_pool_timeout_is_explicit(postgres_pool_connection: Any, django_db_blocker: Any) -> None:
    from psycopg_pool import PoolTimeout

    with django_db_blocker.unblock(), pytest.raises(
        OperationalError, match="couldn't get a connection after 0.20 sec"
    ) as error:
        postgres_pool_connection.ensure_connection()
    assert isinstance(error.value.__cause__, PoolTimeout)



def test_resources_catalogue_hook_preserves_source_local_markers(monkeypatch):
    """Native model declarations retain catalogue tiers without granting them to an MTI child."""

    from angee.resources.build import CatalogueIntegration
    from angee.resources.mixins import ResourceLoadMixin

    module = ModuleType("tests.catalogue_emission")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    parent = type("CatalogueThing", (ResourceLoadMixin, AngeeModel), {
        "__module__": module.__name__, "catalogue": True,
        "catalogue_tier": "install", "catalogue_tiers": ("install", "demo"),
        "Meta": type("Meta", (), {"abstract": True, "app_label": "catalogue"}),
    }, concrete=True)
    module.CatalogueThing = parent
    child = _source_model(module, "CatalogueChild", "catalogue", inherits="catalogue.CatalogueThing")
    composition = ModelComposition({"catalogue": (parent, child)}, {})
    source = render_models(composition, "catalogue", declarations={
        model: model_declaration(composition, model, (CatalogueIntegration(),))
        for model in composition.ordered_models
    })
    parent_body = source[source.index("class CatalogueThing"):source.index("class CatalogueChild")]
    child_body = source[source.index("class CatalogueChild"):]
    assert "catalogue = True" in parent_body
    assert "catalogue_tier = 'install'" in parent_body
    assert "catalogue_tiers = ('install', 'demo')" in parent_body
    assert "catalogue = True" not in child_body
    assert "catalogue_tier" not in child_body
