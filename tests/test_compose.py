"""Tests for build-time runtime composition."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
from django.apps import AppConfig, apps
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError, SystemCheckError
from django.db import OperationalError, migrations, models
from rebac import schema_changes

import angee.compose as compose_package
import angee.compose.runtime as runtime_module
from angee.addons import addon_manifest
from angee.apps import AngeeConfig
from angee.base.build import PermissionIntegration, composed_grants
from angee.base.models.base import AngeeModel
from angee.base.models.roles import role_anchor
from angee.build import ManifestDeclarations
from angee.compose.appgraph import AppGraph
from angee.compose.apps import BuildComposeConfig
from angee.compose.declarations import AppDeclarationSource, model_declaration
from angee.compose.model_composition import ModelComposition
from angee.compose.rendering import render_models
from angee.compose.runtime import GENERATED_SENTINEL, Runtime
from angee.conf import ProjectContract
from angee.management import provision
from angee.management.commands.angee import Command
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.resources.mixins import ResourceLoadMixin
from angee.web.build import WebRuntime
from tests.conftest import make_addon


class DecoratedHistoryThing(HistoryMixin, AngeeModel):
    """Abstract model used to test composer-emitted class attributes."""

    runtime = True

    title = models.CharField(max_length=64)

    class Meta:
        """Django model options for the test source model."""

        abstract = True
        app_label = "tests"


class SkippedRuntimeThing(AngeeModel):
    """Abstract model used to test app-level runtime model selection."""

    name = models.CharField(max_length=64)

    class Meta:
        """Django model options for the test source model."""

        abstract = True
        app_label = "tests"


class FirstRenderPlanMetaThing(AngeeModel):
    """Abstract model with model-specific REBAC Meta for render-plan tests."""

    runtime = True

    class Meta:
        """Django model options for the test source model."""

        abstract = True
        app_label = "tests"
        rebac_resource_type = "tests/first-render-plan"


class SecondRenderPlanMetaThing(AngeeModel):
    """Abstract model with model-specific REBAC Meta for render-plan tests."""

    runtime = True

    class Meta:
        """Django model options for the test source model."""

        abstract = True
        app_label = "tests"
        rebac_resource_type = "tests/second-render-plan"


def runtime_for(tmp_path: Path) -> Runtime:
    """Return a runtime that emits the installed resource addon."""

    return Runtime.discover(
        (apps.get_app_config("resources"),),
        runtime_dir=tmp_path / "runtime",
    )


def _source_model(module: ModuleType, name: str, label: str, **body: Any) -> type[AngeeModel]:
    """Register an abstract source model in ``module`` and return it."""

    model = type(
        name,
        (AngeeModel,),
        {
            "__module__": module.__name__,
            "Meta": type("Meta", (), {"abstract": True, "app_label": label}),
            **body,
        },
    )
    setattr(module, name, model)
    return cast(type[AngeeModel], model)


def _addon_config(label: str, models_module: ModuleType) -> SimpleNamespace:
    """Return an app-config stand-in contributing ``models_module``."""

    return SimpleNamespace(
        label=label,
        name=f"tests.{label}",
        module=ModuleType(f"tests.{label}"),
        models_module=models_module,
    )


def test_runtime_renders_resource_sources(tmp_path: Path) -> None:
    """The runtime renders source files for the resource ledger model."""

    sources = runtime_for(tmp_path).render_sources()

    assert Path("__init__.py") in sources
    assert Path("asgi.py") not in sources
    assert Path("urls.py") not in sources
    assert "ANGEE GENERATED RUNTIME" in sources[Path("__init__.py")]
    assert "RUNTIME_APPS = ['resources']" in sources[Path("__init__.py")]
    assert "class Resource" in sources[Path("resources/models.py")]
    assert "app_label = 'resources'" in sources[Path("resources/models.py")]
    assert ".angee-manifest.json" not in {str(path) for path in sources}
    assert Path("permissions.zed") not in sources
    assert Path("web/manifest.json") in sources
    assert Path("web/tailwind.sources.css") in sources
    # The composer is a pure package-graph projector: it emits the manifest and
    # Tailwind sources, never schema-shaped TypeScript. `runtime/web/app.ts` and
    # `runtime/gql/<schema>/*` are owned by the `angee-web-codegen` CLI.
    assert Path("web/app.ts") not in sources
    # The import ledger has no console capability of its own. Platform contributes
    # its UI only when that higher-level addon is composed.
    assert json.loads(sources[Path("web/manifest.json")])["addonPackages"] == []


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


def test_runtime_composes_additive_grants_without_parent_authority() -> None:
    """Addon grants belong to the extended record, never its MTI descendants."""

    module = ModuleType("tests.grant_composition")
    parent = _source_model(module, "GrantParent", "tests", runtime=True, rebac_grantable={"viewer": "share"})
    donor = _source_model(
        module,
        "GrantDonor",
        "tests",
        extends="tests.GrantParent",
        rebac_grantable={"reviewer": "write"},
    )
    child = type("GrantChild", (models.Model,), {
        "__module__": module.__name__, "runtime": True, "extends": "tests.GrantParent",
        "Meta": type("Meta", (), {"abstract": True, "app_label": "tests"}),
    })
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


def test_runtime_rejects_conflicting_donor_grant_authority() -> None:
    """An addon cannot silently weaken an existing relationship's grant gate."""

    module = ModuleType("tests.grant_conflict")
    source = _source_model(module, "GrantSource", "tests", runtime=True, rebac_grantable={"reviewer": "admin"})
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


def test_runtime_rejects_conflicting_donor_managers() -> None:
    """Two same-row donors cannot silently shadow one another's manager."""

    module = ModuleType("tests.manager_conflict")
    source = _source_model(module, "ManagerSource", "tests", runtime=True)
    first = _source_model(
        module, "FirstManagerDonor", "tests", extends="tests.ManagerSource", objects=models.Manager(),
    )
    second = _source_model(
        module, "SecondManagerDonor", "tests", extends="tests.ManagerSource", objects=models.Manager(),
    )

    with pytest.raises(ImproperlyConfigured, match="composes manager 'objects'"):
        ModelComposition(
            {"tests": (source,)},
            {"tests.managersource": (first, second)},
            model_owners={first: module.__name__, second: module.__name__},
        )


@pytest.mark.parametrize("invalid", [None, [], {"": "write"}, {"reviewer": 42}])
def test_runtime_validates_donor_grants_with_model_owner(invalid: Any) -> None:
    """Malformed addon declarations retain the model's configuration error contract."""

    module = ModuleType("tests.grant_invalid")
    source = _source_model(module, "GrantValid", "tests", runtime=True)
    donor = _source_model(module, "GrantInvalid", "tests", extends="tests.GrantValid", rebac_grantable=invalid)
    composition = ModelComposition(
        {"tests": (source,)}, {"tests.grantvalid": (donor,)}, model_owners={donor: module.__name__},
    )
    with pytest.raises(ImproperlyConfigured, match="rebac_grantable"):
        model_declaration(composition, source, (PermissionIntegration(),))


def test_runtime_honors_explicit_label_when_module_terminal_differs(
    tmp_path: Path,
    settings: Any,
) -> None:
    """A source module ending in ``base`` may emit under its explicit app label."""

    settings.MIGRATION_MODULES = {**settings.MIGRATION_MODULES, "beta": None}

    def temp_config(name: str, label: str, module: ModuleType, *, depends_on: tuple[str, ...] = ()) -> AppConfig:
        config = make_addon(name=name, label=label, path=tmp_path / label, depends_on=depends_on)
        config.module = module
        return config

    beta_module = ModuleType("tests.project_base")
    beta_models = ModuleType("tests.project_base.models")
    _source_model(beta_models, "ProjectThing", "beta", runtime=True)
    beta = temp_config("tests.project_base", "beta", beta_module)
    beta.models_module = beta_models
    alpha = temp_config("tests.alpha", "alpha", ModuleType("tests.alpha"), depends_on=("beta",))

    ordered = AppGraph(ManifestDeclarations()).resolve((alpha, beta))
    assert [config.label for config in ordered[:2]] == ["beta", "alpha"]

    runtime = Runtime.discover((beta,), runtime_dir=tmp_path / "runtime")
    sources = runtime.render_sources()

    assert set(runtime.composition.sources_by_label) == {"beta"}
    assert Path("beta/models.py") in sources
    assert Path("project_base/models.py") not in sources
    assert "app_label = 'beta'" in sources[Path("beta/models.py")]
    assert "beta.projectthing" in runtime.composition.models_by_label


def test_web_runtime_projects_addon_web_packages_in_composed_order() -> None:
    """Addon web package declarations feed one generated web manifest."""

    first = make_addon(name="tests.first", web={"package": "@demo/first"})
    backend_only = SimpleNamespace(name="tests.backend", label="backend")
    second = make_addon(name="tests.second", web={"package": "@demo/second"})

    manifest = WebRuntime((first, backend_only, second)).manifest_json()

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

    manifest = json.loads(WebRuntime((addon,), runtime_dir=tmp_path / "runtime").manifest_json())

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
    product = make_addon(name="tests.product", depends_on=("bridge",), web={"package": "@demo/product"})
    unrelated = make_addon(name="tests.unrelated", web={"package": "@demo/unrelated"})

    manifest = json.loads(WebRuntime((base, bridge, product, unrelated)).manifest_json())

    assert {entry["app"]: entry["dependsOn"] for entry in manifest["addonPackages"]} == {
        "tests.base": [],
        "tests.product": ["tests.base"],
        "tests.unrelated": [],
    }


def test_nexus_aggregation_dependencies_reach_web_runtime() -> None:
    """Nexus alone pulls in every app whose menu it includes, already in the test host."""

    configs = AppGraph(ManifestDeclarations()).resolve(("angee.nexus",))
    assert {config.name for config in configs if addon_manifest(config) is not None} <= {
        config.name for config in apps.get_app_configs()
    }
    manifest = WebRuntime(configs).manifest
    nexus = next(entry for entry in manifest["addonPackages"] if entry["app"] == "angee.nexus")
    assert {"angee.messaging", "angee.parties", "angee.spaces", "angee.posts"} <= set(nexus["dependsOn"])


def test_oidc_web_documents_follow_the_enabled_backend_addon() -> None:
    """An IAM-only host never scans operations belonging to the optional OIDC schema."""

    iam_configs = AppGraph(ManifestDeclarations()).resolve(("angee.iam",))
    oidc_configs = AppGraph(ManifestDeclarations()).resolve(("angee.iam_integrate_oidc",))
    iam_manifest = WebRuntime(iam_configs).manifest
    oidc_manifest = WebRuntime(oidc_configs).manifest

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
            "apps": {"payables": {"rail": ("accounting", "files"), "home": "accounting.bills"}},
            "hosts": {"payables.example.test": "payables", "work.example.test": "pm"},
        }
    }

    deployment = json.loads(WebRuntime((addon,), ui=ui).manifest_json())["deployment"]
    assert deployment["shell"]["apps"]["payables"]["rail"] == ["accounting", "files"]
    assert deployment["shell"]["hosts"] == {"payables.example.test": "payables", "work.example.test": "pm"}
    assert "deployment" not in json.loads(WebRuntime((addon,), ui={}).manifest_json())
    with pytest.raises(ImproperlyConfigured, match="unknown keys \\['rail'\\]"):
        WebRuntime((addon,), ui={"rail": {}})
    with pytest.raises(ImproperlyConfigured, match="unknown keys \\['perspectives'\\]"):
        WebRuntime((addon,), ui={"perspectives": {"pm": {"root": "pm"}}})
    with pytest.raises(ImproperlyConfigured, match="must be a mapping"):
        WebRuntime((addon,), ui=["shell"])


@pytest.mark.parametrize(
    ("shell", "message"),
    [
        (["pm"], "ANGEE_UI.shell must be a mapping"),
        ({"home": "pm.home"}, "ANGEE_UI.shell declares unknown keys \\['home'\\]"),
        ({"perspective": None}, "ANGEE_UI.shell declares unknown keys \\['perspective'\\]"),
        ({"brand": {"name": "Suite"}}, "ANGEE_UI.shell.brand.mark must be a non-empty string"),
        ({"brand": {"name": "Suite", "mark": "suite", "tone": "x"}}, "ANGEE_UI.shell.brand declares unknown keys"),
        ({"theme": " "}, "ANGEE_UI.shell.theme must be a non-empty string"),
        ({"apps": {"ap": {"rail": []}}}, "ANGEE_UI.shell.apps.ap.rail must be a non-empty list of menu root ids"),
        ({"apps": {"ap": {"rail": "accounting"}}}, "ANGEE_UI.shell.apps.ap.rail must be a non-empty list"),
        ({"apps": {"ap": {"rail": ["accounting", "accounting"]}}}, "ANGEE_UI.shell.apps.ap.rail lists a root twice"),
        ({"apps": {"ap": {"rail": ["accounting"], "hosts": {}}}}, "ANGEE_UI.shell.apps.ap declares unknown keys"),
        ({"apps": {"ap": {"rail": ["accounting"], "home": ""}}}, "ANGEE_UI.shell.apps.ap.home must be a non-empty"),
        ({"hosts": {"AP.example.test": "ap"}}, 'shell.hosts\\["AP.example.test"\\] must be a lower-case hostname'),
        ({"hosts": {"ap.example.test": None}}, 'shell.hosts\\["ap.example.test"\\] must be a non-empty string'),
    ],
)
def test_web_runtime_refuses_a_malformed_deployment_shell(shell: object, message: str) -> None:
    """``ANGEE_UI.shell`` fails the build when its shape would select the wrong app; ``@angee/app`` checks the rest."""

    addon = make_addon(name="tests.addon", web={"package": "@demo/addon"})
    with pytest.raises(ImproperlyConfigured, match=message):
        WebRuntime((addon,), ui={"shell": shell})


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

    manifest = WebRuntime((daemon,)).manifest_json()

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

    with pytest.raises(ImproperlyConfigured, match=r"requires \[web\]\.package"):
        WebRuntime((daemon,))


def test_web_runtime_rejects_duplicate_addon_web_packages() -> None:
    """Two addons cannot claim the same web package identity."""

    first = make_addon(name="tests.first", web={"package": "@demo/shared"})
    second = make_addon(name="tests.second", web={"package": "@demo/shared"})

    with pytest.raises(ImproperlyConfigured, match=r"Duplicate \[web\]\.package"):
        WebRuntime((first, second))


def test_web_runtime_rejects_invalid_package_names() -> None:
    """The web package contract fails before a broken manifest is emitted."""

    broken = make_addon(name="tests.broken", web={"package": "../broken"})

    with pytest.raises(ImproperlyConfigured, match="valid npm package name"):
        WebRuntime((broken,))


def test_runtime_records_native_effective_migration_mapping(tmp_path: Path, settings: Any) -> None:
    """The compiler projects native history ownership without changing it."""

    runtime = runtime_for(tmp_path)
    mapping = {"resources": "migrations.resources", "foreign": None}
    settings.MIGRATION_MODULES = mapping.copy()
    namespace: dict[str, Any] = {}
    exec(runtime.render_sources()[Path("__init__.py")], namespace)
    assert namespace["RUNTIME_MIGRATION_MODULES"] == {"resources": "migrations.resources"}
    assert settings.MIGRATION_MODULES == mapping
    assert not any("migrations" in path.parts for path in runtime.render_sources())


def test_runtime_renders_iam_user_sources(tmp_path: Path) -> None:
    """The IAM addon emits a concrete user that inherits Django-owned Meta options."""

    iam_config = apps.get_app_config("iam")
    runtime = Runtime.discover(
        (apps.get_app_config("resources"), iam_config),
        runtime_dir=tmp_path / "runtime",
    )

    sources = runtime.render_sources()
    user_source = sources[Path("iam/models.py")]

    assert "class User" in user_source
    assert "app_label = 'iam'" in user_source
    assert "rebac_resource_type = 'auth/user'" in user_source
    assert "class Meta(AbstractUser.Meta):" in user_source
    assert "swappable = 'AUTH_USER_MODEL'" not in user_source


def test_role_anchor_factory_pins_the_hand_rolled_anchor_shape() -> None:
    """``role_anchor`` emits the abstract, table-less anchor the adopters declared by hand."""

    anchor = role_anchor("storage/role")

    assert anchor.__name__ == "StorageRole"
    assert anchor.__module__ == __name__
    assert anchor._meta.abstract is True
    assert anchor._meta.managed is False
    assert anchor._meta.rebac_resource_type == "storage/role"
    assert anchor.__dict__["runtime"] is True
    assert issubclass(anchor, AngeeModel)
    # The name derives from the resource type; a symbol that differs is overridable.
    assert role_anchor("operator/role").__name__ == "OperatorRole"
    assert role_anchor("tags/role", name="Role").__name__ == "Role"


def test_role_anchor_emits_the_hand_rolled_runtime_source(tmp_path: Path) -> None:
    """A ``role_anchor`` model composes into the same concrete runtime an addon shipped by hand."""

    module = ModuleType("tests.role_anchor_probe")
    probe = role_anchor("tests/role", name="ProbeRole", module=module.__name__)
    setattr(module, "ProbeRole", probe)

    composition = ModelComposition({"tests": (probe,)}, {})
    source = render_models(composition, "tests", declarations={
        probe: model_declaration(composition, probe, (PermissionIntegration(),)),
    })

    assert "from tests.role_anchor_probe import ProbeRole as AbstractProbeRole" in source
    assert "class ProbeRole(AbstractProbeRole):" in source
    assert "class Meta(AbstractProbeRole.Meta):" in source
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
        with pytest.raises(ImproperlyConfigured, match="does not bind"):
            render_models(ModelComposition({"tests": (stray,)}, {}), "tests")
    finally:
        del sys.modules[module.__name__]


def test_django_reads_inherited_meta_defaults() -> None:
    """Runtime ``Meta(SourceMeta)`` carries Django options without re-emission."""

    class MetaInheritanceSource(models.Model):
        class Meta:
            abstract = True
            app_label = "tests"
            db_table = "compose_meta_inheritance_source"
            swappable = "COMPOSE_META_INHERITANCE_MODEL"

    class MetaInheritanceRuntime(MetaInheritanceSource):
        class Meta(MetaInheritanceSource.Meta):
            abstract = False
            app_label = "compose_meta_inheritance"

    assert MetaInheritanceRuntime._meta.db_table == "compose_meta_inheritance_source"
    assert MetaInheritanceRuntime._meta.swappable == "COMPOSE_META_INHERITANCE_MODEL"
    assert MetaInheritanceRuntime._meta.original_attrs["db_table"] == "compose_meta_inheritance_source"
    assert MetaInheritanceRuntime._meta.original_attrs["swappable"] == "COMPOSE_META_INHERITANCE_MODEL"


def test_runtime_emits_only_models_marked_runtime(tmp_path: Path) -> None:
    """Only abstract source models declaring ``runtime = True`` are emitted."""

    app_config = SimpleNamespace(
        label="tests",
        name=__name__,
        module=sys.modules[__name__],
        models_module=sys.modules[__name__],
    )

    source = Runtime.discover((app_config,), runtime_dir=tmp_path / "runtime").render_sources()[Path("tests/models.py")]

    assert "class DecoratedHistoryThing" in source
    assert "class SkippedRuntimeThing" not in source


def test_runtime_carries_catalogue_markers_on_emitted_concrete_model(tmp_path: Path, settings: Any) -> None:
    """Catalogue declarations survive the abstract-source to concrete-runtime hop."""

    settings.MIGRATION_MODULES = {**settings.MIGRATION_MODULES, "catalogue": None}
    module = ModuleType("tests.catalogue_emission.models")
    CatalogueThing = type(
        "CatalogueThing",
        (ResourceLoadMixin, AngeeModel),
        {
            "__module__": module.__name__,
            "runtime": True,
            "catalogue": True,
            "catalogue_tier": "install",
            "catalogue_tiers": ("install", "demo"),
            "name": models.CharField(max_length=32),
            "Meta": type("Meta", (), {"abstract": True, "app_label": "catalogue"}),
        },
    )
    CatalogueChild = type(
        "CatalogueChild",
        (models.Model,),
        {
            "__module__": module.__name__,
            "runtime": True,
            "extends": "catalogue.CatalogueThing",
            "child_value": models.CharField(max_length=16),
            "Meta": type("Meta", (), {"abstract": True, "app_label": "catalogue"}),
        },
    )
    module.CatalogueThing = CatalogueThing
    module.CatalogueChild = CatalogueChild
    app_config = SimpleNamespace(
        label="catalogue",
        name="tests.catalogue_emission",
        module=ModuleType("tests.catalogue_emission"),
        models_module=module,
    )

    source = Runtime.discover((app_config,), runtime_dir=tmp_path / "runtime").render_sources()[
        Path("catalogue/models.py")
    ]
    parent_body = source[source.index("class CatalogueThing") : source.index("class CatalogueChild")]
    child_body = source[source.index("class CatalogueChild") :]

    assert "catalogue = True" in parent_body
    assert "catalogue_tier = 'install'" in parent_body
    assert "catalogue_tiers = ('install', 'demo')" in parent_body
    assert "catalogue = True" not in child_body
    assert "catalogue_tier" not in child_body
    assert "catalogue_tiers" not in child_body


def test_runtime_renders_materialized_child_extension(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``extends`` + ``runtime = True`` emits a concrete MTI child model."""

    class RuntimeChild(models.Model):
        runtime = True
        extends = "tests.DecoratedHistoryThing"
        child_value = models.CharField(max_length=16)

        class Meta:
            abstract = True
            app_label = "tests"

    monkeypatch.setattr(sys.modules[__name__], "RuntimeChild", RuntimeChild, raising=False)
    app_config = SimpleNamespace(
        label="tests",
        name=__name__,
        module=sys.modules[__name__],
        models_module=SimpleNamespace(
            DecoratedHistoryThing=DecoratedHistoryThing,
            RuntimeChild=RuntimeChild,
        ),
    )

    source = Runtime.discover((app_config,), runtime_dir=tmp_path / "runtime").render_sources()[Path("tests/models.py")]

    assert "from runtime.tests.models import DecoratedHistoryThing" not in source
    assert "class DecoratedHistoryThing(AbstractDecoratedHistoryThing):" in source
    assert "class RuntimeChild(AbstractRuntimeChild, DecoratedHistoryThing):" in source


def test_runtime_renders_materialized_child_extension_across_apps(tmp_path: Path, settings: Any) -> None:
    """Materialized children import the generated parent from the target runtime app."""

    settings.MIGRATION_MODULES = {**settings.MIGRATION_MODULES, "target": None, "child": None}
    target_module = ModuleType("tests.target.models")
    child_module = ModuleType("tests.child.models")
    TargetRuntime = type(
        "TargetRuntime",
        (AngeeModel,),
        {
            "__module__": target_module.__name__,
            "runtime": True,
            "name": models.CharField(max_length=32),
            "Meta": type("Meta", (), {"abstract": True, "app_label": "target"}),
        },
    )
    RuntimeChild = type(
        "RuntimeChild",
        (models.Model,),
        {
            "__module__": child_module.__name__,
            "runtime": True,
            "extends": "target.TargetRuntime",
            "child_value": models.CharField(max_length=16),
            "Meta": type("Meta", (), {"abstract": True, "app_label": "child"}),
        },
    )
    target_module.TargetRuntime = TargetRuntime
    child_module.RuntimeChild = RuntimeChild

    runtime = Runtime.discover(
        (
            SimpleNamespace(
                label="target",
                name="tests.target",
                module=ModuleType("tests.target"),
                models_module=target_module,
            ),
            SimpleNamespace(
                label="child",
                name="tests.child",
                module=ModuleType("tests.child"),
                models_module=child_module,
            ),
        ),
        runtime_dir=tmp_path / "runtime",
    )

    sources = runtime.render_sources()
    child_source = sources[Path("child/models.py")]

    assert "from runtime.target.models import TargetRuntime as RuntimeChildParent" in child_source
    assert "from tests.child.models import RuntimeChild as AbstractRuntimeChild" in child_source
    assert "class RuntimeChild(AbstractRuntimeChild, RuntimeChildParent):" in child_source
    assert "class TargetRuntime(AbstractTargetRuntime):" in sources[Path("target/models.py")]


def test_runtime_rejects_mismatched_runtime_model_label(tmp_path: Path) -> None:
    """Runtime source models must belong to the app config that contributes them."""

    class MismatchedRuntimeLabel(AngeeModel):
        runtime = True

        class Meta:
            abstract = True
            app_label = "wrong"

    app_config = SimpleNamespace(
        label="owner",
        name=__name__,
        module=sys.modules[__name__],
        models_module=SimpleNamespace(MismatchedRuntimeLabel=MismatchedRuntimeLabel),
    )

    with pytest.raises(ImproperlyConfigured, match="expected 'owner'"):
        Runtime.discover((app_config,), runtime_dir=tmp_path / "runtime")


def test_runtime_rejects_mismatched_extension_model_label(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Extension source models must also belong to their contributing app config."""

    class LabelTarget(AngeeModel):
        runtime = True

        class Meta:
            abstract = True
            app_label = "target"

    class MismatchedExtensionLabel(AngeeModel):
        extends = "target.LabelTarget"

        class Meta:
            abstract = True
            app_label = "wrong"

    monkeypatch.setattr(sys.modules[__name__], "LabelTarget", LabelTarget, raising=False)
    target_config = SimpleNamespace(
        label="target",
        name=__name__,
        module=sys.modules[__name__],
        models_module=SimpleNamespace(LabelTarget=LabelTarget),
    )
    extension_config = SimpleNamespace(
        label="extension",
        name=__name__,
        module=sys.modules[__name__],
        models_module=SimpleNamespace(MismatchedExtensionLabel=MismatchedExtensionLabel),
    )

    with pytest.raises(ImproperlyConfigured, match="expected 'extension'"):
        Runtime.discover((target_config, extension_config), runtime_dir=tmp_path / "runtime")


def test_explicit_source_emission_repairs_drift_and_prunes_removed_artifacts(tmp_path: Path) -> None:
    """Only explicit compilation repairs stale sources and removes obsolete output."""

    runtime = runtime_for(tmp_path)
    runtime.emit_sources()
    runtime.check()
    models_path = runtime.runtime_dir / "resources" / "models.py"
    models_path.write_text("# stale\n", encoding="utf-8")
    orphan = runtime.runtime_dir / "removed" / "models.py"
    orphan.parent.mkdir()
    orphan.write_text("# orphan\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="stale"):
        runtime.check()
    assert runtime.emit_sources() is True
    assert models_path.read_text(encoding="utf-8") == runtime.render_sources()[Path("resources/models.py")]
    assert not orphan.exists()
    runtime.check()


def test_runtime_check_ignores_schema_command_output(tmp_path: Path) -> None:
    """GraphQL SDL files are checked by the schema command, not build."""

    runtime = runtime_for(tmp_path)
    runtime.emit_sources()
    schema_path = tmp_path / "runtime" / "schemas" / "public.graphql"
    schema_path.parent.mkdir()
    schema_path.write_text("type Query { ok: Boolean! }\n", encoding="utf-8")

    runtime.check()


def test_runtime_check_ignores_graphql_codegen_output(tmp_path: Path) -> None:
    """Generated GraphQL client code is checked by its frontend owner, not build."""

    runtime = runtime_for(tmp_path)
    runtime.emit_sources()
    gql_path = tmp_path / "runtime" / "gql" / "public" / "graphql.ts"
    gql_path.parent.mkdir(parents=True)
    gql_path.write_text("export const ok = true;\n", encoding="utf-8")

    runtime.check()


def test_runtime_check_ignores_web_codegen_output(tmp_path: Path) -> None:
    """Generated web entry code is checked by the frontend CLI, not build."""

    runtime = runtime_for(tmp_path)
    runtime.emit_sources()
    app_path = tmp_path / "runtime" / "web" / "app.ts"
    app_path.write_text("export const ok = true;\n", encoding="utf-8")
    routes_path = tmp_path / "runtime" / "web" / "routes.gen.ts"
    routes_path.write_text("export const routes = [];\n", encoding="utf-8")

    runtime.check()


def test_runtime_extensions_follow_app_graph_order_not_class_names(tmp_path: Path, settings: Any) -> None:
    """Renaming extension classes must not change extension base precedence."""

    settings.MIGRATION_MODULES = {**settings.MIGRATION_MODULES, "target": None}
    target_module = ModuleType("tests.target.models")
    preferred_module = ModuleType("tests.preferred.models")
    fallback_module = ModuleType("tests.fallback.models")

    TargetRuntime = type(
        "TargetRuntime",
        (AngeeModel,),
        {
            "__module__": target_module.__name__,
            "runtime": True,
            "Meta": type("Meta", (), {"abstract": True, "app_label": "target"}),
        },
    )
    ZPreferredExtension = type(
        "ZPreferredExtension",
        (AngeeModel,),
        {
            "__module__": preferred_module.__name__,
            "extends": "target.TargetRuntime",
            "Meta": type("Meta", (), {"abstract": True, "app_label": "preferred"}),
        },
    )
    AFallbackExtension = type(
        "AFallbackExtension",
        (AngeeModel,),
        {
            "__module__": fallback_module.__name__,
            "extends": "target.TargetRuntime",
            "Meta": type("Meta", (), {"abstract": True, "app_label": "fallback"}),
        },
    )
    target_module.TargetRuntime = TargetRuntime
    preferred_module.ZPreferredExtension = ZPreferredExtension
    fallback_module.AFallbackExtension = AFallbackExtension

    runtime = Runtime.discover(
        (
            SimpleNamespace(
                label="target",
                name="tests.target",
                module=ModuleType("tests.target"),
                models_module=target_module,
            ),
            SimpleNamespace(
                label="preferred",
                name="tests.preferred",
                module=ModuleType("tests.preferred"),
                models_module=preferred_module,
            ),
            SimpleNamespace(
                label="fallback",
                name="tests.fallback",
                module=ModuleType("tests.fallback"),
                models_module=fallback_module,
            ),
        ),
        runtime_dir=tmp_path / "runtime",
    )

    source = runtime.render_sources()[Path("target/models.py")]

    assert "from tests.preferred.models import ZPreferredExtension as TargetRuntimeExtension1" in source
    assert "from tests.fallback.models import AFallbackExtension as TargetRuntimeExtension2" in source
    assert "class TargetRuntime(TargetRuntimeExtension1, TargetRuntimeExtension2, AbstractTargetRuntime):" in source


@pytest.mark.parametrize("operation", ["clean_configured", "emit_sources"])
def test_runtime_writes_require_generated_sentinel(tmp_path: Path, settings: Any, operation: str) -> None:
    """Explicit emission cannot authorize a foreign directory by writing its sentinel."""

    runtime = runtime_for(tmp_path)
    settings.COMPOSE_RUNTIME_DIR = runtime.runtime_dir
    runtime.runtime_dir.mkdir()
    (runtime.runtime_dir / "handwritten.py").write_text(
        "# keep\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="lacks the required generated-output marker"):
        getattr(runtime, operation)()

    assert (runtime.runtime_dir / "handwritten.py").read_text(encoding="utf-8") == "# keep\n"
    assert not (runtime.runtime_dir / "__init__.py").exists()


@pytest.mark.parametrize("operation", ["emit_sources"])
def test_runtime_writes_require_configured_directory(tmp_path: Path, settings: Any, operation: str) -> None:
    """A generated sentinel never authorizes writes outside the configured root."""

    runtime = runtime_for(tmp_path)
    runtime.emit_sources()
    stale = runtime.runtime_dir / "stale.py"
    stale.write_text("# keep\n", encoding="utf-8")
    settings.COMPOSE_RUNTIME_DIR = tmp_path / "other_runtime"

    with pytest.raises(RuntimeError, match="not the configured output directory"):
        getattr(runtime, operation)()

    assert stale.read_text(encoding="utf-8") == "# keep\n"
    assert (runtime.runtime_dir / "resources" / "models.py").exists()


def test_clean_then_explicit_emission_is_idempotent(tmp_path: Path, settings: Any) -> None:
    """A cleaned runtime with preserved migrations keeps its cleanup sentinel."""

    runtime = runtime_for(tmp_path)
    settings.COMPOSE_RUNTIME_DIR = runtime.runtime_dir
    runtime.emit_sources()
    migration_paths = (
        runtime.runtime_dir / "resources" / "migrations" / "0001_initial.py",
        runtime.runtime_dir / "resources" / "migrations" / "archive" / "snapshot.txt",
        runtime.runtime_dir / "removed" / "migrations" / "nested" / "data.bin",
    )
    for path in migration_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("migration\n", encoding="utf-8")

    Runtime.clean_configured()
    assert "ANGEE GENERATED RUNTIME" in (runtime.runtime_dir / "__init__.py").read_text(encoding="utf-8")
    runtime.emit_sources()

    assert "ANGEE GENERATED RUNTIME" in (runtime.runtime_dir / "__init__.py").read_text(encoding="utf-8")
    assert all(path.read_text(encoding="utf-8") == "migration\n" for path in migration_paths)
    Runtime.clean_configured()
    assert all(path.read_text(encoding="utf-8") == "migration\n" for path in migration_paths)
    assert "ANGEE GENERATED RUNTIME" in (runtime.runtime_dir / "__init__.py").read_text(encoding="utf-8")
    Runtime.clean_configured()


def test_runtime_clean_refuses_migrations_without_sentinel(tmp_path: Path, settings: Any) -> None:
    """Migrations alone are not enough evidence that a directory is generated."""

    runtime = runtime_for(tmp_path)
    settings.COMPOSE_RUNTIME_DIR = runtime.runtime_dir
    migration_path = runtime.runtime_dir / "resources" / "migrations" / "0001_initial.py"
    migration_path.parent.mkdir(parents=True)
    migration_path.write_text("# migration\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="lacks the required generated-output marker"):
        Runtime.clean_configured()


def _compose_config() -> BuildComposeConfig:
    """Return a ComposeConfig bound enough for a direct import_models call."""

    config = BuildComposeConfig("angee.compose", compose_package)
    config.apps = apps
    return config


def test_explicit_build_config_emits_then_imports_before_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the explicitly selected build lifecycle emits before concrete import."""

    calls: list[str] = []

    class FakeRuntime:
        def emit_sources(self) -> bool:
            calls.append("emit_sources")
            return True

        def import_generated_models(self) -> None:
            calls.append("import")

    monkeypatch.setattr(runtime_module.Runtime, "from_django", classmethod(lambda cls: FakeRuntime()))

    config = _compose_config()
    monkeypatch.setattr(apps, "get_app_configs", lambda: (config,))
    config.import_models()

    assert calls == ["emit_sources", "import"]


def test_build_check_reports_command_error_when_runtime_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``angee check`` converts runtime drift into a clean command error."""

    class FakeRuntime:
        addons = ()

        def check(self) -> None:
            raise RuntimeError("generated runtime is stale: resources/models.py")

    monkeypatch.setattr(runtime_module.Runtime, "from_django", classmethod(lambda cls: FakeRuntime()))

    with pytest.raises(CommandError, match="generated runtime is stale"):
        Command()._handle_check({})


def test_runtime_build_emits_stale_sources_once_before_materializing(tmp_path: Path, monkeypatch, settings) -> None:
    runtime = runtime_for(tmp_path)
    settings.MIGRATION_MODULES = {}
    calls: list[str] = []
    original_render = runtime.render_sources

    def render():
        calls.append("render")
        return original_render()

    class FakeMigrations:
        def materialize(self, *, apps) -> tuple[Path, ...]:
            assert apps is runtime_module.apps
            assert "class Resource" in (runtime.runtime_dir / "resources" / "models.py").read_text()
            calls.append("materialize")
            return ()

    monkeypatch.setattr(runtime, "render_sources", render)
    monkeypatch.setattr(runtime, "runtime_migrations", lambda: FakeMigrations())

    runtime.emit_sources()
    runtime.runtime_migrations().materialize(apps=apps)
    assert calls == ["render", "materialize"]


def test_runtime_build_materializes_without_rewriting_current_sources(tmp_path: Path, monkeypatch, settings) -> None:
    runtime = runtime_for(tmp_path)
    settings.MIGRATION_MODULES = {}
    runtime.emit_sources()
    path = runtime.runtime_dir / "resources" / "models.py"
    modified = path.stat().st_mtime_ns
    caches = (
        runtime.runtime_dir / "__pycache__" / "__init__.cpython-314.pyc",
        runtime.runtime_dir / "resources" / "__pycache__" / "models.cpython-314.pyc",
    )
    for cache in caches:
        cache.parent.mkdir()
        cache.write_bytes(b"\x00bytecode")
    calls: list[str] = []

    class FakeMigrations:
        def materialize(self, *, apps) -> tuple[Path, ...]:
            assert apps is runtime_module.apps
            calls.append("materialize")
            return ()

    monkeypatch.setattr(runtime, "runtime_migrations", lambda: FakeMigrations())

    runtime.emit_sources()
    runtime.runtime_migrations().materialize(apps=apps)
    assert calls == ["materialize"]
    assert path.stat().st_mtime_ns == modified
    assert all(cache.exists() for cache in caches)
    assert runtime.emit_sources() is False


@pytest.mark.parametrize("contents", ["empty", "models", "bytecode"])
def test_runtime_build_prunes_removed_labels_before_materializing(
    tmp_path: Path, monkeypatch, settings, contents, caplog,
) -> None:
    """Removed packages count as drift with sources, bytecode, or empty directories."""

    runtime = runtime_for(tmp_path)
    settings.COMPOSE_RUNTIME_DIR = runtime.runtime_dir
    settings.MIGRATION_MODULES = {}
    runtime.emit_sources()
    removed = runtime.runtime_dir / "removed"
    removed.mkdir()
    if contents == "models":
        (removed / "__init__.py").write_text("", encoding="utf-8")
        (removed / "models.py").write_text("import deleted_addon\n", encoding="utf-8")
    elif contents == "bytecode":
        (removed / "__pycache__").mkdir()
        (removed / "__pycache__" / "models.cpython-314.pyc").write_bytes(b"\x00bytecode")
    else:
        (removed / "nested").mkdir()

    class FakeMigrations:
        def materialize(self, *, apps) -> tuple[Path, ...]:
            assert not removed.exists()
            return ()

    monkeypatch.setattr(runtime, "runtime_migrations", lambda: FakeMigrations())

    runtime.emit_sources()
    runtime.runtime_migrations().materialize(apps=apps)
    assert not removed.exists()
    assert runtime.emit_sources() is False
    assert "Preserved migration directories" not in caplog.text


def test_runtime_check_validates_migrations_after_source_drift_is_clean(tmp_path: Path, monkeypatch) -> None:
    runtime = runtime_for(tmp_path)
    runtime.emit_sources()
    calls: list[str] = []

    class FakeMigrations:
        def check(self) -> None:
            calls.append("migration_check")

    monkeypatch.setattr(runtime, "runtime_migrations", lambda: FakeMigrations())

    runtime.check()

    assert calls == ["migration_check"]


def test_runtime_check_does_not_plan_migrations_while_sources_are_stale(tmp_path: Path, monkeypatch) -> None:
    runtime = runtime_for(tmp_path)
    runtime.emit_sources()
    (runtime.runtime_dir / "resources" / "models.py").write_text("# stale\n", encoding="utf-8")
    calls: list[str] = []
    monkeypatch.setattr(runtime, "runtime_migrations", lambda: calls.append("migration_check"))

    with pytest.raises(RuntimeError, match="generated runtime is stale"):
        runtime.check()

    assert calls == []


def test_emit_sources_never_constructs_runtime_migrations(tmp_path: Path, monkeypatch) -> None:
    runtime = runtime_for(tmp_path)
    runtime.emit_sources()
    monkeypatch.setattr(
        runtime,
        "runtime_migrations",
        lambda: pytest.fail("normal boot must not materialize migrations"),
        raising=False,
    )

    assert runtime.emit_sources() is False


def _provision_options(**overrides: Any) -> dict[str, Any]:
    """Build a provision options dict with every flag defaulted off."""

    options: dict[str, Any] = {
        "after_if_installed": [],
        "force_rebac": False,
        "wait_db": 60,
    }
    options.update(overrides)
    return options


def test_provision_plan_default_flags_covers_the_no_flag_lifecycle() -> None:
    """The prebuilt native plan migrates, synchronizes permissions and checks; addons are explicit."""

    assert provision.plan(_provision_options()) == [
        ["makemigrations", "--check", "--dry-run", "--noinput", "--skip-checks"],
        ["migrate", "--noinput", "--skip-checks"],
        ["rebac", "--skip-checks", "sync", "--prune-stale", "--yes"],
        ["check", "--database", "default"],
    ]


def test_provision_parser_preserves_explicit_addon_command_lines() -> None:
    """The native CLI preserves quoted addon arguments for the provision owner."""
    parser = Command().create_parser("manage.py", "angee")
    options = vars(parser.parse_args([
        "provision", "--after-if-installed", "resources load --include-demo",
        "--after-if-installed", "addon_command --name 'quoted value'",
    ]))
    assert options["after_if_installed"] == [
        "resources load --include-demo", "addon_command --name 'quoted value'",
    ]


def test_provision_plan_force_rebac_force_overwrites_the_sync() -> None:
    """``--force-rebac`` appends ``--force-overwrite`` to the rebac sync step only."""

    plan = provision.plan(_provision_options(force_rebac=True))

    assert ["rebac", "--skip-checks", "sync", "--prune-stale", "--yes", "--force-overwrite"] in plan
    assert ["rebac", "--skip-checks", "sync", "--prune-stale", "--yes"] not in plan


@pytest.mark.parametrize("flag", ["--demo", "--bootstrap-admin", "--post-build"])
def test_provision_parser_rejects_retired_capability_flags(flag: str) -> None:
    """Product actions are explicit addon commands, so retired host flags fail loudly."""
    parser = Command().create_parser("manage.py", "angee")
    with pytest.raises(CommandError, match="unrecognized arguments"):
        parser.parse_args(["provision", flag])


@pytest.mark.parametrize("command_line", ["", "'unterminated"])
def test_provision_rejects_invalid_addon_commands_before_work(command_line, monkeypatch) -> None:
    """A malformed explicit addon request never starts provisioning."""
    command = Command()
    monkeypatch.setattr(
        provision, 'wait_for_database', lambda _command, seconds: pytest.fail('invalid command began provisioning'),
    )
    with pytest.raises(CommandError, match="command line"):
        command._handle_provision(_provision_options(after_if_installed=[command_line]))


def test_provision_plan_combines_every_flag() -> None:
    """Addon requests do not change the mandatory native migration/check plan."""

    plan = provision.plan(_provision_options(
        force_rebac=True, after_if_installed=["resources load --include-demo", "schema", "bootstrap_admin"],
    ))

    assert plan == [
        ["makemigrations", "--check", "--dry-run", "--noinput", "--skip-checks"],
        ["migrate", "--noinput", "--skip-checks"],
        ["rebac", "--skip-checks", "sync", "--prune-stale", "--yes", "--force-overwrite"],
        ["check", "--database", "default"],
    ]


def test_provision_only_orders_native_commands_for_a_prebuilt_runtime() -> None:
    """Build is the preceding operator job; provision never compiles sources."""

    steps = provision.plan(_provision_options())
    assert all(step[0] != "angee" for step in steps)
    assert steps.index(["makemigrations", "--check", "--dry-run", "--noinput", "--skip-checks"]) < steps.index(
        ["migrate", "--noinput", "--skip-checks"],
    )


def test_provision_defers_checks_only_across_the_schema_identity_transition() -> None:
    """Persisted REBAC labels may lag emitted models until migrations finish."""

    plan = provision.plan(_provision_options())

    assert [step for step in plan if "--skip-checks" in step] == [
        ["makemigrations", "--check", "--dry-run", "--noinput", "--skip-checks"],
        ["migrate", "--noinput", "--skip-checks"],
        ["rebac", "--skip-checks", "sync", "--prune-stale", "--yes"],
    ]
    assert (
        plan.index(["migrate", "--noinput", "--skip-checks"])
        < plan.index(["rebac", "--skip-checks", "sync", "--prune-stale", "--yes"])
        < plan.index(["check", "--database", "default"])
    )
    assert plan[-1] == ["check", "--database", "default"]


def test_provision_analyzes_the_existing_tables_of_apps_migrated_forward() -> None:
    """Statistics follow forward schema changes; a backwards step or a missing table names nothing."""

    plan = [
        (migrations.Migration("0003_forward", "contenttypes"), False),
        (migrations.Migration("0004_backward", "sessions"), True),
    ]

    assert provision.analyzed_tables(plan, ["django_content_type", "django_session"]) == ["django_content_type"]
    assert provision.analyzed_tables(plan, ["django_session"]) == []


@pytest.mark.django_db
def test_provision_plan_can_cross_an_old_persisted_rebac_identity() -> None:
    """An old persisted field target fails rebac checks until its identity migrates.

    The bare test host cannot import every addon schema, so this test names the
    rebac check tag rather than running every registered check.
    """

    from rebac.models import SchemaRelation

    call_command("rebac", "sync", "--yes", verbosity=0)
    source = SchemaRelation.objects.get(
        definition__resource_type="agents/skill",
        name="source",
    )
    with pytest.raises(SystemCheckError, match=r"rebac\.E009"), schema_changes():
        source.allowed_subjects = [{"type": "integrate/source", "relation": "", "wildcard": False}]
        source.save(update_fields=["allowed_subjects"])
        call_command("check", "--tag", "rebac", verbosity=0)

    plan = provision.plan(_provision_options())
    assert plan == [
        ["makemigrations", "--check", "--dry-run", "--noinput", "--skip-checks"],
        ["migrate", "--noinput", "--skip-checks"],
        ["rebac", "--skip-checks", "sync", "--prune-stale", "--yes"],
        ["check", "--database", "default"],
    ]


def test_provision_runs_native_steps_and_addon_commands_in_one_process(monkeypatch) -> None:
    calls = []
    waits = []
    command = Command()
    monkeypatch.setattr(provision, "wait_for_database", lambda _command, seconds: waits.append(seconds))
    monkeypatch.setattr(provision, "run_step", lambda _command, step: calls.append(step))
    monkeypatch.setattr(provision, "get_commands", lambda: {"schema": "graphql"})
    options = _provision_options(after_if_installed=["schema", "absent"])

    command._handle_provision(options)

    assert waits == [60]
    assert calls == [*provision.plan(options), ["schema"]]


def test_provision_runs_declared_installed_addon_commands_after_migrations(monkeypatch) -> None:
    calls = []
    command = Command()
    monkeypatch.setattr(provision, "wait_for_database", lambda _command, seconds: None)
    monkeypatch.setattr(provision, 'run_step', lambda _command, argument: (calls.append)(argument))
    monkeypatch.setattr(
        "angee.management.provision.get_commands", lambda: {"addon_post_build": "timeline"},
    )
    command._handle_provision(_provision_options(
        after_if_installed=["addon_post_build", "absent_addon_command"],
    ))
    assert calls[-1] == ["addon_post_build"]
    assert calls.index(["migrate", "--noinput", "--skip-checks"]) < len(calls) - 1


def test_provision_aborts_on_the_first_failed_native_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A native command failure stops every subsequent provision step."""

    calls: list[list[str]] = []

    def fake_step(step: list[str]) -> None:
        calls.append(step)
        if step[0] == "migrate":
            raise CommandError("angee provision: step 'migrate --noinput --skip-checks' failed: boom")

    command = Command()
    monkeypatch.setattr(provision, "wait_for_database", lambda _command, seconds: None)
    monkeypatch.setattr(provision, 'run_step', lambda _command, argument: (fake_step)(argument))

    with pytest.raises(CommandError, match="step 'migrate --noinput --skip-checks' failed"):
        command._handle_provision(_provision_options())

    assert calls == [
        ["makemigrations", "--check", "--dry-run", "--noinput", "--skip-checks"],
        ["migrate", "--noinput", "--skip-checks"],
    ]


def test_provision_run_step_preserves_each_commands_cli_check_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checked commands receive policy; commands opting out receive no unknown option."""

    calls: list[tuple[str, tuple[str, ...], bool | None]] = []

    def fake_call_command(command: BaseCommand, *args: str, **options: Any) -> None:
        calls.append((command.__class__.__module__, args, options.get("skip_checks")))

    monkeypatch.setattr("angee.management.provision.call_command", fake_call_command)
    command = Command()
    for step in provision.plan(_provision_options()):
        provision.run_step(command, step)

    policies = [(module.rsplit(".", 1)[-1], policy) for module, _args, policy in calls]
    assert policies == [
        ("makemigrations", True),
        ("migrate", True),
        ("rebac", True),
        ("check", None),
    ]


def test_provision_run_step_calls_actual_check_free_schema_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A check-free command executes without the parser-rejected ``skip_checks`` option."""

    handled: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "angee.graphql.management.commands.schema.Command.handle",
        lambda self, *args, **options: handled.append(options),
    )

    provision.run_step(Command(), ["schema"])

    assert len(handled) == 1
    assert handled[0]["check"] is False


def test_provision_run_step_names_a_failing_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """Native command exceptions retain the provision step that raised them."""

    def fail(*args: Any, **options: Any) -> None:
        raise RuntimeError("broken schema")

    monkeypatch.setattr("angee.management.provision.call_command", fail)

    with pytest.raises(CommandError, match="step 'schema' failed: broken schema"):
        provision.run_step(Command(), ["schema"])


@pytest.mark.parametrize("fails", [False, True])
def test_provision_run_step_flushes_command_output_before_returning(
    monkeypatch: pytest.MonkeyPatch,
    fails: bool,
) -> None:
    """Delegated output is flushed after success and before propagating failure."""

    events: list[str] = []

    def run(*args: Any, **options: Any) -> None:
        events.append("command-output")
        if fails:
            raise RuntimeError("failed")

    command = Command()
    monkeypatch.setattr(command.stdout, "flush", lambda: events.append("stdout-flush"))
    monkeypatch.setattr(command.stderr, "flush", lambda: events.append("stderr-flush"))
    monkeypatch.setattr("angee.management.provision.call_command", run)

    if fails:
        with pytest.raises(CommandError, match="step 'schema' failed: failed"):
            provision.run_step(command, ["schema"])
    else:
        provision.run_step(command, ["schema"])

    assert events == [
        "stdout-flush",
        "command-output",
        "stdout-flush",
        "stderr-flush",
    ]


@pytest.mark.parametrize("exit_code", [0, None])
def test_provision_run_step_accepts_successful_system_exit(
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int | None,
) -> None:
    """Commands may use either successful SystemExit convention."""

    def exit_successfully(*args: Any, **kwargs: Any) -> None:
        raise SystemExit(exit_code)

    monkeypatch.setattr("angee.management.provision.call_command", exit_successfully)

    provision.run_step(Command(), ["schema"])


def test_provision_run_step_names_a_nonzero_system_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A command's nonzero SystemExit becomes a step-specific provision error."""

    def exit_with_failure(*args: Any, **kwargs: Any) -> None:
        raise SystemExit(4)

    monkeypatch.setattr("angee.management.provision.call_command", exit_with_failure)

    with pytest.raises(CommandError, match=r"step 'schema' failed \(exit 4\)"):
        provision.run_step(Command(), ["schema"])


def test_provision_run_step_explicitly_runs_delegated_prechecks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passing ``skip_checks=False`` defeats ``call_command``'s skip-by-default trap."""

    lifecycle: list[str] = []

    class CheckedCommand(BaseCommand):
        requires_system_checks = ["models"]

        def add_arguments(self, parser: Any) -> None:
            parser.add_argument("operation")
            parser.add_argument("--yes", action="store_true")

        def check(self, **kwargs: Any) -> list[object]:
            lifecycle.append("check")
            return []

        def handle(self, *args: Any, **options: Any) -> None:
            lifecycle.append("handle")

    monkeypatch.setattr("angee.management.provision.get_commands", lambda: {"rebac": "fake"})
    monkeypatch.setattr(
        "angee.management.provision.load_command_class",
        lambda app_name, name: CheckedCommand(),
    )

    provision.run_step(Command(), ["rebac", "sync", "--yes"])

    assert lifecycle == ["check", "handle"]


def test_provision_help_has_no_retired_child_switch(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Provision exposes no subprocess-child lifecycle switch."""

    parser = Command().create_parser("manage.py", "angee")

    with pytest.raises(SystemExit, match="0"):
        parser.parse_args(["provision", "--help"])

    assert "--post-build" not in capsys.readouterr().out


class _FakeConnection:
    """Stand-in default connection that fails ``ensure_connection`` N times."""

    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.attempts = 0
        self.closed = False

    def ensure_connection(self) -> None:
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise OperationalError("connection refused")

    def close(self) -> None:
        self.closed = True


def test_provision_wait_retries_then_closes_the_probe_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wait loop retries until the database answers, then closes the probe."""

    connection = _FakeConnection(fail_times=2)
    monkeypatch.setattr("angee.management.provision.connections", {"default": connection})
    monkeypatch.setattr("angee.management.provision.time.sleep", lambda seconds: None)

    provision.wait_for_database(Command(), 10)

    assert connection.attempts == 3
    assert connection.closed is True


def test_provision_wait_times_out_with_the_last_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A database that never answers raises CommandError with the last error."""

    connection = _FakeConnection(fail_times=99)
    monkeypatch.setattr("angee.management.provision.connections", {"default": connection})
    monkeypatch.setattr("angee.management.provision.time.sleep", lambda seconds: None)

    with pytest.raises(CommandError, match="within 3s: connection refused"):
        provision.wait_for_database(Command(), 3)

    assert connection.attempts == 3


def test_appgraph_annotates_roots_and_dependencies() -> None:
    """resolve() tags declared roots and normalizes each app's dependencies.

    The platform console reads these annotations instead of re-deriving the
    composed graph (``addons/angee/platform/schema.py``).
    """

    graph = AppGraph(ManifestDeclarations())
    configs = {config.name: config for config in graph.resolve(["angee.iam"])}

    iam = configs["angee.iam"]
    assert iam.compose_root is True
    assert iam.compose_root_declaration == "angee.iam"
    assert "angee.resources" in addon_manifest(iam).depends_on
    assert not hasattr(iam, "angee_depends_on")

    # `resources` is pulled in through iam's closure, not declared — a dependency.
    assert configs["angee.resources"].compose_root is False
    assert configs["angee.resources"].compose_root_declaration is None

    # `forced` = another resolved app depends on me (cannot be uninstalled). `resources`
    # is in iam's closure → forced; the sole declared root nothing depends on is not.
    assert configs["angee.resources"].compose_required is True
    assert iam.compose_required is False


def test_appgraph_rejects_duplicate_roots() -> None:
    """A repeated explicit root app is a settings error, not hidden dedupe."""

    with pytest.raises(ImproperlyConfigured, match="Duplicate Django app 'angee.resources'"):
        AppGraph(ManifestDeclarations()).resolve(["angee.resources", "angee.resources"])


def test_appgraph_root_wins_when_also_a_dependency() -> None:
    """An app declared as a root remains a root even if another root depends on it."""

    configs = {
        config.name: config
        for config in AppGraph(ManifestDeclarations()).resolve(["angee.iam", "angee.resources"])
    }

    assert configs["angee.iam"].compose_root is True
    assert configs["angee.resources"].compose_root is True


def test_appgraph_preserves_authored_app_config_root_declaration() -> None:
    """Runtime drift compares the exact root spelling authored in settings YAML."""

    declaration = "angee.iam.apps.IAMConfig"
    configs = {config.name: config for config in AppGraph(ManifestDeclarations()).resolve([declaration])}

    assert configs["angee.iam"].compose_root_declaration == declaration


def test_appgraph_distinguishes_project_roots_from_injected_runtime_roots() -> None:
    """Framework defaults run but do not become project-authored drift facts."""

    declaration = "angee.iam.apps.IAMConfig"
    configs = {
        config.name: config
        for config in AppGraph(ManifestDeclarations()).resolve(
            ["django.contrib.contenttypes", declaration],
            declared_roots=[declaration],
        )
    }

    assert configs["angee.iam"].compose_root_declaration == declaration
    assert configs["angee.iam"].compose_root is True
    assert configs["django.contrib.contenttypes"].compose_root_declaration is None
    assert configs["django.contrib.contenttypes"].compose_root is False


def test_appgraph_rejects_duplicate_dependencies() -> None:
    """Repeated dependencies are rejected at their declaring owner."""

    config = make_addon(name="tests.duplicate_dependency", depends_on=("angee", "angee"))

    with pytest.raises(ImproperlyConfigured, match="duplicate dependency"):
        AppGraph(ManifestDeclarations()).resolve([config])


def test_project_env_file_loads_without_overriding_process_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The project-root .env seeds env vars for host runs; real process env wins.

    The stack's gitignored `.env` (secrets plus derived DATABASE_URL) is what lets
    a bare `uv run manage.py …` talk to the stack database; operator-managed
    services set their env explicitly, so read_env must never overwrite it.
    """

    (tmp_path / ".env").write_text(
        'DATABASE_URL="postgres://angee:pw@127.0.0.1:5433/angee"\nYAMLCONF_SECRET_KEY="from-env-file"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("YAMLCONF_SECRET_KEY", "from-process-env")

    # read_env writes straight into os.environ (not via monkeypatch), so clean up
    # directly — a trailing monkeypatch.delenv would record the leaked value as
    # prior state and RESTORE it at teardown, poisoning later tests.
    try:
        ProjectContract({})._read_project_env(tmp_path)

        assert os.environ["DATABASE_URL"] == "postgres://angee:pw@127.0.0.1:5433/angee"
        assert os.environ["YAMLCONF_SECRET_KEY"] == "from-process-env"
    finally:
        os.environ.pop("DATABASE_URL", None)


def test_project_env_file_is_optional(tmp_path: Path) -> None:
    """A project without .env composes exactly as before — silent no-op."""

    ProjectContract({})._read_project_env(tmp_path)


def test_database_pool_follows_project_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgres://user:password@localhost:5432/db")
    project_settings = ModuleType("test_project_settings")
    project_settings.BASE_DIR = tmp_path
    development: dict[str, Any] = {}
    ProjectContract(development)._apply_defaults(
        project_settings, tmp_path, predefined_settings=frozenset(),
    )
    database = development["DATABASES"]["default"]
    assert "pool" not in database["OPTIONS"]
    assert "CONN_MAX_AGE" not in database

    monkeypatch.setenv("ANGEE_DB_POOL", "true")
    monkeypatch.setenv("ANGEE_DB_POOL_MAX_SIZE", "3")
    monkeypatch.setenv("ANGEE_DB_POOL_TIMEOUT", "7")
    production: dict[str, Any] = {}
    ProjectContract(production)._apply_defaults(
        project_settings, tmp_path, predefined_settings=frozenset(),
    )
    database = production["DATABASES"]["default"]
    assert database["OPTIONS"]["pool"] == {"min_size": 0, "max_size": 3, "timeout": 7.0}
    assert "CONN_MAX_AGE" not in database
    assert "CONN_HEALTH_CHECKS" not in database
    monkeypatch.setenv("DATABASE_URL", "postgres://user:password@localhost:5432/db?conn_max_age=600")
    with pytest.raises(ImproperlyConfigured, match="ANGEE_DB_POOL.*DATABASE_URL CONN_MAX_AGE"):
        ProjectContract({})._apply_defaults(
            project_settings, tmp_path, predefined_settings=frozenset(),
        )

    monkeypatch.setenv("DATABASE_URL", "postgres://user:password@localhost:5432/db?conn_max_age=0")
    zero_age: dict[str, Any] = {}
    ProjectContract(zero_age)._apply_defaults(
        project_settings, tmp_path, predefined_settings=frozenset(),
    )
    assert zero_age["DATABASES"]["default"]["CONN_MAX_AGE"] == 0
    assert zero_age["DATABASES"]["default"]["OPTIONS"]["pool"]["max_size"] == 3

    monkeypatch.setenv("ANGEE_DB_POOL", "false")
    disabled: dict[str, Any] = {}
    ProjectContract(disabled)._apply_defaults(
        project_settings, tmp_path, predefined_settings=frozenset(),
    )
    assert "pool" not in disabled["DATABASES"]["default"]["OPTIONS"]

    monkeypatch.setenv("DATABASE_URL", "postgres://user:password@localhost:5432/db")
    monkeypatch.delenv("ANGEE_DB_POOL")
    project_settings.ANGEE_DB_POOL = "false"
    disabled_from_settings: dict[str, Any] = {}
    ProjectContract(disabled_from_settings)._apply_defaults(
        project_settings, tmp_path, predefined_settings=frozenset(),
    )
    assert "pool" not in disabled_from_settings["DATABASES"]["default"]["OPTIONS"]
    project_settings.ANGEE_DB_POOL = True
    project_settings.ANGEE_DB_POOL_MAX_SIZE = 4
    from_settings: dict[str, Any] = {}
    ProjectContract(from_settings)._apply_defaults(
        project_settings, tmp_path, predefined_settings=frozenset(),
    )
    assert from_settings["DATABASES"]["default"]["OPTIONS"]["pool"]["max_size"] == 3
    monkeypatch.delenv("ANGEE_DB_POOL_MAX_SIZE")
    ProjectContract(from_settings)._apply_defaults(
        project_settings, tmp_path, predefined_settings=frozenset(),
    )
    assert from_settings["DATABASES"]["default"]["OPTIONS"]["pool"]["max_size"] == 4

    explicit_database = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": tmp_path / "app.sqlite3"}}
    project_settings.DATABASES = explicit_database
    namespace = {}
    ProjectContract(namespace)._apply_defaults(
        project_settings, tmp_path, predefined_settings=frozenset(),
    )
    assert namespace["DATABASES"] == explicit_database


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


def test_explicit_source_emission_renders_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = runtime_for(tmp_path)
    original = runtime.render_sources
    calls = []

    def render():
        calls.append("render")
        return original()

    monkeypatch.setattr(runtime, "render_sources", render)
    runtime.emit_sources()
    assert calls == ["render"]
    assert (runtime.runtime_dir / "resources" / "models.py").is_file()



def test_source_emission_preserves_an_authored_history_package_initializer(tmp_path):
    runtime = runtime_for(tmp_path)
    initializer = runtime.runtime_dir / "resources" / "migrations" / "__init__.py"
    initializer.parent.mkdir(parents=True)
    initializer.write_text("# Host-owned historical imports remain intact.\n")

    runtime.emit_sources()

    assert initializer.read_text() == "# Host-owned historical imports remain intact.\n"
    assert Path("resources/migrations/__init__.py") not in runtime.render_sources()


def test_runtime_from_django_does_not_bind_migrations_or_write_sources(
    tmp_path: Path,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.COMPOSE_RUNTIME_DIR = tmp_path / "runtime"
    settings.MIGRATION_MODULES = {"resources": "authored.migrations"}
    original = settings.MIGRATION_MODULES
    configs = (apps.get_app_config("resources"),)
    monkeypatch.setattr(runtime_module.apps, "get_app_configs", lambda: configs)
    runtime = Runtime.from_django()
    assert runtime.labels == ("resources",)
    assert settings.MIGRATION_MODULES is original
    assert not runtime.runtime_dir.exists()


def test_configured_cleanup_removes_all_generated_packages(tmp_path: Path, settings: Any) -> None:
    """An empty source map still cleans every package under the guarded runtime."""

    runtime_dir = tmp_path / "runtime"
    settings.COMPOSE_RUNTIME_DIR = runtime_dir
    runtime_dir.mkdir()
    (runtime_dir / "__init__.py").write_text(f"{GENERATED_SENTINEL}\n", encoding="utf-8")
    for label in ("integrate_example", "records_integrate_example", "workflows_legacy", "example"):
        package = runtime_dir / label
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "models.py").write_text("# obsolete generated models\n", encoding="utf-8")
    for relative in ("gql/client.ts", "schemas/default.graphql", "web/deleted-artifact.js"):
        artifact = runtime_dir / relative
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("obsolete", encoding="utf-8")
    (runtime_dir / "empty" / "nested").mkdir(parents=True)
    outside = tmp_path / "keep.py"
    outside.write_text("# outside runtime\n", encoding="utf-8")

    Runtime.clean_configured()

    assert not any(runtime_dir.iterdir())
    assert outside.read_text(encoding="utf-8") == "# outside runtime\n"


@pytest.mark.parametrize("runtime_module_name", ["runtime", "generated_runtime"])
def test_configured_cleanup_requires_no_discovery_or_rendering(
    tmp_path: Path,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    runtime_module_name: str,
) -> None:
    settings.COMPOSE_RUNTIME_DIR = tmp_path / "runtime"
    runtime = runtime_for(tmp_path)
    runtime.runtime_module = runtime_module_name
    settings.COMPOSE_RUNTIME_MODULE = runtime_module_name
    settings.MIGRATION_MODULES = {"removed": f"{runtime.runtime_module}.removed.migrations"}
    runtime.emit_sources()
    module = ModuleType(runtime_module_name)
    exec(compile(runtime.render_sources()[Path("__init__.py")], module.__name__, "exec"), vars(module))
    monkeypatch.setitem(sys.modules, runtime_module_name, module)
    migration = runtime.runtime_dir / "resources" / "migrations" / "0001_saved.py"
    migration.parent.mkdir(parents=True)
    migration.write_text("# preserved migration\n")
    removed_migration = runtime.runtime_dir / "removed" / "migrations" / "0001_stale.py"
    removed_migration.parent.mkdir(parents=True)
    removed_migration.write_text("import deleted_addon\n", encoding="utf-8")
    (removed_migration.parent.parent / "models.py").write_text("# old generated models\n", encoding="utf-8")

    def forbidden(*args, **kwargs):
        pytest.fail("cleanup must not discover or render source models")

    monkeypatch.setattr(ModelComposition, "discover", forbidden)
    monkeypatch.setattr(Runtime, "render_sources", forbidden)
    monkeypatch.setattr(runtime_module.apps, "get_models", forbidden)
    with caplog.at_level(logging.WARNING, logger="angee.compose.runtime"):
        Runtime.clean_configured()
    assert migration.read_text() == "# preserved migration\n"
    assert removed_migration.read_text(encoding="utf-8") == "import deleted_addon\n"
    assert str(removed_migration.parent) in caplog.text
    assert str(migration.parent) not in caplog.text
    assert not (runtime.runtime_dir / "resources" / "models.py").exists()
    assert not (removed_migration.parent.parent / "models.py").exists()
    Runtime.clean_configured()
    assert migration.exists()


def test_settings_and_bootstrap_import_without_loading_model_runtime() -> None:
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from django.conf import settings
import angee.conf
import angee.management.provision
import angee.compose.composer
assert not settings.configured
for module in ('angee.compose.runtime', 'angee.base.models', 'rebac.models'):
    assert module not in sys.modules, module
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

def test_tailwind_sources_resolve_workspace_package_links(tmp_path: Path) -> None:
    """The watcher sees real package source directories, not dependency symlinks."""
    runtime = tmp_path / "runtime"
    for package in ("app", "ui"):
        source = tmp_path / "slot" / "packages" / package
        (source / "src").mkdir(parents=True)
        link = tmp_path / "web" / "node_modules" / "@angee" / package
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(source, target_is_directory=True)
    css = WebRuntime((), runtime_dir=runtime).tailwind_sources_css()
    assert '@source "../../slot/packages/ui/src";' in css
    assert '@source "../../slot/packages/app/src";' in css
    assert "node_modules" not in css
    assert css == WebRuntime((), runtime_dir=runtime).tailwind_sources_css()


@pytest.mark.parametrize("changed_app", ["example.plain", "example.donor"])
@pytest.mark.parametrize("operation", ["add", "remove"])
def test_prebuilt_runtime_refuses_a_changed_native_app_closure_before_model_import(
    monkeypatch: pytest.MonkeyPatch, settings: Any, changed_app: str, operation: str,
) -> None:
    """Plain apps and donor-only apps are structural inputs even without model output."""

    config = AngeeConfig("angee", compose_package)
    config.apps = apps
    changed = SimpleNamespace(name=changed_app, label=changed_app.rsplit(".", 1)[1])
    baseline = (config,)
    expanded = (changed, config)
    built, current = (baseline, expanded) if operation == "add" else (expanded, baseline)
    artifact = SimpleNamespace(
        RUNTIME_APPS=(),
        RUNTIME_APP_IDENTITIES=tuple((app.name, app.label) for app in built),
        RUNTIME_MODEL_MODULES=("sealed_runtime.example.models",),
        RUNTIME_INTEGRATION_MODULES=(),
        RUNTIME_MIGRATION_MODULES={},
    )
    settings.COMPOSE_RUNTIME_MODULE = "sealed_runtime"
    monkeypatch.setattr(apps, "get_app_configs", lambda: current)
    imported: list[str] = []
    original_import = runtime_module.importlib.import_module

    def import_module(name: str, package: str | None = None):
        if name == "sealed_runtime":
            return artifact
        if name.startswith("sealed_runtime."):
            imported.append(name)
            raise AssertionError("Generated model imported before sealed-closure validation")
        return original_import(name, package)

    monkeypatch.setattr(runtime_module.importlib, "import_module", import_module)
    with pytest.raises(ImproperlyConfigured, match="Installed Django apps differ.*rebuild"):
        config.import_models()
    assert imported == []


def test_composition_retains_an_installed_empty_cross_addon_migration_target() -> None:
    """A contributing app need not own the installed history label it upgrades."""

    class UpgradeDeclarations(AppDeclarationSource):
        def migrations(self, app):
            if app.label == "contributor":
                return ({"app_label": "retired", "name": "retire_rows", "module": "contributor.upgrade"},)
            return ()

    target = SimpleNamespace(name="example.retired", label="retired", models_module=None)
    contributor = SimpleNamespace(name="example.contributor", label="contributor", models_module=None)
    composition = ModelComposition.discover((target, contributor), declarations=UpgradeDeclarations())
    assert composition.labels == ("retired",)
