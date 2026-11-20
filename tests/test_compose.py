"""Angee permission, role, resource and web capabilities compose native owners."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
import yaml
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError, models
from django_compose.components import enabled_apps
from django_compose.declarations import model_declaration
from django_compose.model_composition import ModelComposition
from django_compose.models import Composable
from django_compose.rendering import render_models
from django_compose.serving import GENERATED_SENTINEL
from graphql import concat_ast, specified_rules, validate

from angee.base.build import PermissionIntegration, composed_grants
from angee.base.models.base import AngeeModel
from angee.base.models.roles import role_anchor
from angee.base.testing.packages import write_addon_distribution
from angee.graphql.schema import GraphQLSchemas
from angee.graphql.testing.documents import authored_documents
from angee.web.build import WebRuntime
from tests.conftest import addon_schema, make_addon


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


def _codegen_document_files(schema_name: str) -> tuple[str, ...]:
    """Return the files host codegen reads ``schema_name``'s documents from (``documentFiles``, web-codegen.mjs)."""

    return ("documents.public.ts",) if schema_name == "public" else ("documents.ts", f"documents.{schema_name}.ts")


_CODEGEN_RULES = tuple(
    rule
    for rule in specified_rules
    if not rule.__name__.startswith(("NoUnusedFragments", "NoUnusedVariables", "KnownDirectives"))
)
"""graphql-codegen's document rules: all specified rules but three, so a fragment another package spreads validates."""


def _web_addons() -> list[str]:
    """Return every installed addon that ships a web package."""

    return sorted(entry["app"] for entry in _web_runtime(apps.get_app_configs()).manifest["addonPackages"])


@pytest.mark.parametrize("root", _web_addons())
def test_authored_documents_validate_against_the_root_closure(root: str) -> None:
    """A host enabling ``root`` alone validates every authored document against its own schemas.

    Host codegen (``generateWebRuntime``) validates the documents of every enabled
    web package, found anywhere under its source root, as one document per schema,
    and a validation error fails the frontend build. A document naming a type only
    a dependent addon brings into the schema fails every host without that addon,
    as integrate's ``ConnectWithAccountFields`` would on a host composing no
    implementer of the interface, were integrate's ``types`` bucket not to register it.
    """

    closure = enabled_apps((root,), group="angee.addons")
    configs = {config.name: config for config in closure.app_configs}
    sources = [
        Path(configs[entry["app"]].path) / "web" / entry["sourceRoot"]
        for entry in _web_runtime(closure.app_configs).manifest["addonPackages"]
    ]
    schemas = GraphQLSchemas(closure.app_configs)
    for name in schemas.names():
        paths = sorted(
            path for source in sources for file in _codegen_document_files(name) for path in source.rglob(file)
        )
        if not paths:
            continue
        errors = validate(schemas.graphql_schema(name), concat_ast(authored_documents(*paths)), _CODEGEN_RULES)
        assert [str(error) for error in errors] == [], name


def test_oidc_web_documents_follow_the_enabled_backend_addon() -> None:
    """An IAM-only host never scans operations belonging to the optional OIDC schema.

    IAM owns public operations of its own (sign-in link redemption): an
    IAM-only host's public schema validates every one, and none of OIDC's.
    """

    iam_configs = enabled_apps(("angee.iam",), group="angee.addons").app_configs
    oidc_configs = enabled_apps(("angee.iam_integrate_oidc",), group="angee.addons").app_configs
    iam_manifest = _web_runtime(iam_configs).manifest
    oidc_manifest = _web_runtime(oidc_configs).manifest

    assert "@angee/iam-integrate-oidc" not in {entry["package"] for entry in iam_manifest["addonPackages"]}
    oidc = next(entry for entry in oidc_manifest["addonPackages"] if entry["app"] == "angee.iam_integrate_oidc")
    assert oidc["package"] == "@angee/iam-integrate-oidc"
    assert {"angee.iam", "angee.integrate", "angee.parties"} <= set(oidc["dependsOn"])
    iam_config = next(config for config in iam_configs if config.name == "angee.iam")
    oidc_config = next(config for config in oidc_configs if config.name == "angee.iam_integrate_oidc")
    iam_public = addon_schema(import_module("angee.iam.schema").schemas, "public")._schema
    iam_operations = authored_documents(Path(iam_config.path) / "web" / "src" / "documents.public.ts")
    oidc_operations = authored_documents(Path(oidc_config.path) / "web" / "src" / "documents.public.ts")
    assert iam_operations and oidc_operations
    assert [validate(iam_public, operation) for operation in iam_operations] == [[]] * len(iam_operations)
    assert all(validate(iam_public, operation) for operation in oidc_operations)


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


class _TemplateHost:
    """A subprocess host running the project template's launcher and settings bootstrap.

    ``angee.base.conf.configure`` reads the host's ``settings.yaml`` and, through
    ``ANGEE_PROJECT_DIR``, the overlay in ``project_dir``, as a stack loads them.
    """

    def __init__(self, root: Path, *installed_apps: str, project_dir: Path | None = None) -> None:
        template = Path(__file__).resolve().parents[1] / "templates/projects/web/template"
        root.mkdir()
        (root / "settings.py").write_text((template / "settings.py").read_text())
        (root / "manage.py").write_text(
            (template / "manage.py.jinja").read_text().replace("{{ project_name }}", "compose-probe")
        )
        self.root = root
        self.authored = {
            "SECRET_KEY": "compose-probe",
            "COMPOSE_OUTPUT_DIR": str(root / "runtime"),
            "COMPOSE_OUTPUT_MODULE": "runtime",
            "INSTALLED_APPS": list(installed_apps),
            "DATABASES": {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        }
        self.environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("ANGEE_", "COMPOSE_", "YAMLCONF_", "SENTRY_"))
            and key not in {"DJANGO_SETTINGS_MODULE", "DJANGO_COMPOSE_MODE", "DATABASE_URL", "PYTHONPATH"}
        }
        self.environment["ANGEE_PROJECT_DIR"] = str(project_dir or root)

    def write_settings(self, **values: Any) -> None:
        """Author the host's ``settings.yaml``: the compiler inputs plus ``values``."""

        (self.root / "settings.yaml").write_text(yaml.safe_dump({**self.authored, **values}))

    def compose(self, *arguments: str) -> str:
        """Run ``manage.py compose`` and return its output."""

        result = subprocess.run(
            [sys.executable, str(self.root / "manage.py"), "compose", *arguments],
            cwd=self.root, env=self.environment, capture_output=True, text=True, timeout=120, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout


@pytest.mark.parametrize("overlay", ["host", "deployment"])
def test_if_stale_recomposes_when_only_the_deployment_ui_layer_changes(tmp_path: Path, overlay: str) -> None:
    """``ANGEE_UI`` is a snapshot input, so ``compose --if-stale`` sees a settings-file change alone.

    ``ANGEE_UI`` sits in the host's own ``settings.yaml`` (a process stack's
    ``ANGEE_PROJECT_DIR``) or in a deployment directory's overlay.
    """

    deployment = tmp_path / "deployment"
    host = _TemplateHost(tmp_path / "host", "angee.web", project_dir=deployment if overlay == "deployment" else None)

    def deploy(hosts: dict[str, str]) -> None:
        ui = {"ANGEE_UI": {"shell": {"apps": {"payables": {"rail": ["accounting"]}}, "hosts": hosts}}}
        if overlay == "host":
            host.write_settings(**ui)
        else:
            host.write_settings()
            deployment.mkdir(exist_ok=True)
            (deployment / "settings.yaml").write_text(yaml.safe_dump(ui))

    def deployed_hosts() -> dict[str, str]:
        manifest = json.loads((host.root / "runtime/web/manifest.json").read_text())
        return cast(dict[str, str], manifest["deployment"]["shell"]["hosts"])

    deploy({"old.example.test": "payables"})
    assert "Composition generated." in host.compose()
    assert "Composition is current." in host.compose("--if-stale")

    deploy({"new.example.test": "payables"})
    assert "Composition generated." in host.compose("--if-stale")
    assert deployed_hosts() == {"new.example.test": "payables"}
    assert "Composition is current." in host.compose("--check")


def test_if_stale_tracks_asset_declarations_but_not_image_bytes(tmp_path: Path) -> None:
    host = _TemplateHost(tmp_path / "host", "site_probe")
    addon = host.root / "site_probe"
    addon.mkdir()
    (addon / "__init__.py").touch()
    write_addon_distribution(addon, "site_probe", depends_on=("angee.web",))
    web = addon / "web"
    web.mkdir()
    package = {"name": "@example/site", "angee": {"assets": {"site.image": "./preview.png"}}}
    (web / "package.json").write_text(json.dumps(package))
    (web / "preview.png").write_bytes(b"first image")
    host.write_settings()
    assert "Composition generated." in host.compose()
    assert "Composition is current." in host.compose("--if-stale")
    (web / "preview.png").write_bytes(b"changed image")
    assert "Composition is current." in host.compose("--if-stale")
    package["angee"]["assets"]["site.image"] = "./replacement.png"
    (web / "package.json").write_text(json.dumps(package))
    assert "Composition generated." in host.compose("--if-stale")
    manifest = json.loads((host.root / "runtime/web/manifest.json").read_text())
    entry = next(entry for entry in manifest["addonPackages"] if entry["app"] == "site_probe")
    assert entry["assets"] == {"site.image": "./replacement.png"}
    assert "Composition is current." in host.compose("--check")


def test_compose_publishes_the_page_ingress_snippet_and_follows_page_declarations(tmp_path: Path) -> None:
    """``runtime/web/pages.caddy`` is a published artifact: importable without pages, recomposed with one."""

    host = _TemplateHost(tmp_path / "host", "site_probe")
    addon = host.root / "site_probe"
    addon.mkdir()
    (addon / "__init__.py").touch()
    write_addon_distribution(addon, "site_probe", depends_on=("angee.web",))
    (addon / "web/src").mkdir(parents=True)
    (addon / "web/src/form.html").write_text("<!doctype html>")
    package: dict[str, Any] = {"name": "@example/site"}
    (addon / "web/package.json").write_text(json.dumps(package))
    host.write_settings()
    assert "Composition generated." in host.compose()
    snippet = host.root / "runtime/web/pages.caddy"
    assert snippet.read_text() == f"{GENERATED_SENTINEL}\n"
    package["angee"] = {
        "pages": {"site.form": {"entry": "./src/form.html", "paths": ["/forms/w/*"], "accept": "text/html"}},
    }
    (addon / "web/package.json").write_text(json.dumps(package))
    assert "Composition generated." in host.compose("--if-stale")
    assert "rewrite * /site.form.html" in snippet.read_text()
    manifest = json.loads((host.root / "runtime/web/manifest.json").read_text())
    entry = next(entry for entry in manifest["addonPackages"] if entry["app"] == "site_probe")
    assert entry["pages"] == package["angee"]["pages"]
    assert "Composition is current." in host.compose("--check")


@pytest.mark.parametrize(
    ("setting", "override"),
    [
        ("STRAWBERRY_DJANGO", {"TYPE_DESCRIPTION_FROM_MODEL_DOCSTRING": True}),
        ("ANGEE_GRAPHQL_RESOURCE_FILTERS", ["host_filters.resource_filters"]),
    ],
)
def test_if_stale_recomposes_when_only_a_schema_setting_changes(tmp_path: Path, setting: str, override: Any) -> None:
    """GraphQL declares the settings its SDL follows, so a host override alone recomposes."""

    host = _TemplateHost(tmp_path / "host", "angee.tags")
    (host.root / "host_filters.py").write_text("def resource_filters(model):\n    return {}\n")
    host.write_settings()
    assert "Composition generated." in host.compose()
    assert "Composition is current." in host.compose("--if-stale")

    host.write_settings(**{setting: override})
    assert "Composition generated." in host.compose("--if-stale")
    assert "Composition is current." in host.compose("--if-stale")


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


# DATABASE_URL inputs whose parse ANGEE_DB_PREPARED_STATEMENTS must leave alone while it is unset.
DATABASE_URLS = (
    "postgres://u:p@h:5432/db",
    "postgresql://u:p@h:5432/db",
    "pgsql://u:p@h:5432/db",
    "postgis://u:p@h:5432/db",
    "postgres://u:p@h:5432/db?conn_max_age=600",
    "postgres://u:p@h:5432/db?sslmode=require",
    "postgres://u:p@h:5432/db?options=-c%20statement_timeout%3D5000",
    *(f"postgres://u:p@h:5432/db?server_side_binding={value}" for value in ("true", "True", "1", "0", "false", "")),
    *(f"postgres://u:p@h:5432/db?prepare_threshold={value}" for value in ("0", "1", "5", "-1", "007", "None", "")),
    "postgres://u:p@h:5432/db?server_side_binding=true&prepare_threshold=5",
    "sqlite:////tmp/app.sqlite3?server_side_binding=true",
    "mysql://u:p@h:3306/db?server_side_binding=true",
)


def _deployed_urls(monkeypatch, url: str, **environment: str) -> dict[str, Any]:
    import environ

    from angee.base.conf import _deployment_urls

    monkeypatch.setenv("DATABASE_URL", url)
    for name in ("ANGEE_DB_PREPARED_STATEMENTS", "ANGEE_DB_POOL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    namespace: dict[str, Any] = {}
    _deployment_urls(namespace, environ.Env())
    return namespace


@pytest.mark.parametrize("unset", [{}, {"ANGEE_DB_PREPARED_STATEMENTS": "false"}], ids=["unset", "false"])
def test_prepared_statements_off_leave_every_database_url_as_django_environ_parses_it(monkeypatch, unset):
    """Off, every URL builds the database django-environ parses, plus ``-c jit=off`` on PostgreSQL.

    ``repr`` compares types too: ``1`` and ``True`` are equal under ``==``.
    """

    import environ

    for url in DATABASE_URLS:
        expected = environ.Env.db_url_config(url)
        if "postgresql" in expected.get("ENGINE", ""):
            expected.setdefault("OPTIONS", {}).setdefault("options", "-c jit=off")
        namespace = _deployed_urls(monkeypatch, url, **unset)
        assert repr(namespace) == repr({"DATABASES": {"default": expected}}), url


def test_prepared_statements_follow_authored_settings_and_native_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DJANGO_COMPOSE_MODE", "write")
    monkeypatch.setenv("DATABASE_URL", "postgres://user:password@localhost:5432/db")
    monkeypatch.delenv("ANGEE_DB_PREPARED_STATEMENTS", raising=False)
    assert repr(_configured_namespace(tmp_path)["DATABASES"]["default"]["OPTIONS"]) == repr({"options": "-c jit=off"})
    monkeypatch.setenv("ANGEE_DB_PREPARED_STATEMENTS", "true")
    assert repr(_configured_namespace(tmp_path)["DATABASES"]["default"]["OPTIONS"]) == repr(
        {"options": "-c jit=off", "server_side_binding": True, "prepare_threshold": 1}
    )
    monkeypatch.delenv("ANGEE_DB_PREPARED_STATEMENTS")
    authored = _configured_namespace(tmp_path, ANGEE_DB_PREPARED_STATEMENTS=True)
    assert authored["DATABASES"]["default"]["OPTIONS"]["server_side_binding"] is True
    assert _configured_namespace(tmp_path, ANGEE_DB_PREPARED_STATEMENTS="false")[
        "DATABASES"]["default"]["OPTIONS"] == {"options": "-c jit=off"}


@pytest.mark.parametrize(
    ("url", "named"),
    [
        ("postgres://u:p@h/db?server_side_binding=true", "server_side_binding"),
        ("postgres://u:p@h/db?server_side_binding=false", "server_side_binding"),
        ("postgres://u:p@h/db?prepare_threshold=1", "prepare_threshold"),
        ("postgres://u:p@h/db?server_side_binding=1&prepare_threshold=0", "server_side_binding or prepare_threshold"),
    ],
)
def test_prepared_statements_refuse_a_database_url_that_names_their_options(monkeypatch, url, named):
    refusal = f"ANGEE_DB_PREPARED_STATEMENTS cannot be used with DATABASE_URL {named}$"
    with pytest.raises(ImproperlyConfigured, match=refusal):
        _deployed_urls(monkeypatch, url, ANGEE_DB_PREPARED_STATEMENTS="true")


def test_prepared_statements_apply_where_the_pool_applies(monkeypatch):
    """The setting applies where ``ANGEE_DB_POOL`` applies: a PostgreSQL ``DATABASE_URL``. A project's own
    ``DATABASES`` and other URL schemes are left alone, while a URL naming the setting's options still fails."""

    import environ

    from angee.base.conf import _deployment_urls

    on = {"ANGEE_DB_PREPARED_STATEMENTS": "true"}
    for url in ("sqlite:////tmp/app.sqlite3?server_side_binding=true", "mysql://u:p@h:3306/db", "postgis://u:p@h/db"):
        expected = {"DATABASES": {"default": environ.Env.db_url_config(url)}}
        assert repr(_deployed_urls(monkeypatch, url, **on)) == repr(expected), url
    explicit = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": "x", "OPTIONS": {}}}

    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h/db")
    monkeypatch.setenv("ANGEE_DB_PREPARED_STATEMENTS", "true")
    namespace = {"DATABASES": explicit}
    _deployment_urls(namespace, environ.Env())
    assert namespace["DATABASES"] is explicit and explicit["default"]["OPTIONS"] == {}
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h/db?prepare_threshold=1")
    with pytest.raises(ImproperlyConfigured, match="ANGEE_DB_PREPARED_STATEMENTS"):
        _deployment_urls({"DATABASES": explicit}, environ.Env())


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
