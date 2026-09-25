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
from django.db import OperationalError, models
from django.test.utils import isolate_apps

import angee.compose as compose_package
import angee.compose.runtime as runtime_module
from angee.addons import addon_manifest
from angee.base.mixins import HistoryMixin, RevisionMixin
from angee.base.models import AngeeModel, role_anchor
from angee.compose.appgraph import AppGraph
from angee.compose.apps import ComposeConfig
from angee.compose.dependencies import AddonDependencyGroupResult
from angee.compose.management.commands.angee import Command
from angee.compose.model_composition import ModelComposition
from angee.compose.rendering import render_models
from angee.compose.runtime import Runtime
from angee.compose.web import WebRuntime
from angee.fs import GENERATED_SENTINEL
from tests.conftest import make_addon


class DecoratedRevisionThing(RevisionMixin, AngeeModel):
    """Abstract model used to test composer-emitted model decorators."""

    runtime = True

    revisioned_fields = ("body",)

    body = models.TextField()

    class Meta:
        """Django model options for the test source model."""

        abstract = True
        app_label = "tests"


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
    assert '"package": "@angee/resources"' in sources[Path("web/manifest.json")]
    resources_web = Path(apps.get_app_config("resources").path).resolve() / "web"
    addon_source = Path(os.path.relpath(resources_web, tmp_path / "runtime" / "web")).as_posix()
    assert f'@source "{addon_source}/src";' in sources[Path("web/tailwind.sources.css")]


def test_runtime_model_render_plan_keeps_model_owned_meta(tmp_path: Path) -> None:
    """Each named render plan carries the Meta facts for its own model."""

    source = render_models(
        ModelComposition({"tests": (FirstRenderPlanMetaThing, SecondRenderPlanMetaThing)}, {}), "tests"
    )

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

    assert composition.grantable(parent) == {"viewer": "share", "reviewer": "write"}
    assert composition.grantable(child) == {}
    rendered = render_models(composition, "tests")
    assert "rebac_grantable = {'viewer': 'share', 'reviewer': 'write'}" in rendered
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
    with pytest.raises(ImproperlyConfigured, match="conflicting rebac_grantable"):
        ModelComposition({"tests": (source,)}, {"tests.grantsource": (donor,)}, model_owners={donor: module.__name__})


@pytest.mark.parametrize("invalid", [None, [], {"": "write"}, {"reviewer": 42}])
def test_runtime_validates_donor_grants_with_model_owner(invalid: Any) -> None:
    """Malformed addon declarations retain the model's configuration error contract."""

    module = ModuleType("tests.grant_invalid")
    source = _source_model(module, "GrantValid", "tests", runtime=True)
    donor = _source_model(module, "GrantInvalid", "tests", extends="tests.GrantValid", rebac_grantable=invalid)
    with pytest.raises(ImproperlyConfigured, match="rebac_grantable"):
        ModelComposition({"tests": (source,)}, {"tests.grantvalid": (donor,)}, model_owners={donor: module.__name__})


def test_runtime_honors_explicit_label_when_module_terminal_differs(
    tmp_path: Path,
) -> None:
    """A source module ending in ``base`` may emit under its explicit app label."""

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

    ordered = AppGraph().resolve((alpha, beta))
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
            "package": "@demo/addon",
            "root": "../../addon/web",
            "sourceRoot": "src",
        }
    ]


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


def test_runtime_configures_migrations_for_runtime_labels(tmp_path: Path, settings: Any) -> None:
    """Runtime owns migration redirects for labels it materializes."""

    runtime = runtime_for(tmp_path)
    settings.MIGRATION_MODULES = {
        "custom": "custom.migrations",
        "disabled": None,
        "foreign": "other_runtime.foreign.migrations",
    }

    runtime.configure_migration_modules()
    assert settings.MIGRATION_MODULES == {
        "custom": "custom.migrations",
        "disabled": None,
        "foreign": "other_runtime.foreign.migrations",
        "resources": "runtime.resources.migrations",
    }


def test_runtime_migration_module_conflicts_fail_fast(tmp_path: Path, settings: Any) -> None:
    """Projects cannot silently move migrations for emitted runtime apps."""

    runtime = runtime_for(tmp_path)
    settings.MIGRATION_MODULES = {"resources": "custom.resources.migrations"}

    with pytest.raises(ImproperlyConfigured, match=r"MIGRATION_MODULES\['resources'\]"):
        runtime.configure_migration_modules()


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
    assert "_UserMeta = getattr(AbstractUser, 'Meta', object)" in user_source
    assert "class Meta(_UserMeta):" in user_source
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

    source = render_models(ModelComposition({"tests": (probe,)}, {}), "tests")

    assert "from tests.role_anchor_probe import ProbeRole as AbstractProbeRole" in source
    assert "_ProbeRoleMeta = getattr(AbstractProbeRole, 'Meta', object)" in source
    assert "class ProbeRole(AbstractProbeRole):" in source
    assert "class Meta(_ProbeRoleMeta):" in source
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

    assert "class DecoratedRevisionThing" in source
    assert "class SkippedRuntimeThing" not in source


def test_runtime_carries_catalogue_markers_on_emitted_concrete_model(tmp_path: Path) -> None:
    """Catalogue declarations survive the abstract-source to concrete-runtime hop."""

    module = ModuleType("tests.catalogue_emission.models")
    CatalogueThing = type(
        "CatalogueThing",
        (AngeeModel,),
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
        extends = "tests.DecoratedRevisionThing"
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
            DecoratedRevisionThing=DecoratedRevisionThing,
            RuntimeChild=RuntimeChild,
        ),
    )

    source = Runtime.discover((app_config,), runtime_dir=tmp_path / "runtime").render_sources()[Path("tests/models.py")]

    assert "from runtime.tests.models import DecoratedRevisionThing" not in source
    assert "class DecoratedRevisionThing(AbstractDecoratedRevisionThing):" in source
    assert "class RuntimeChild(AbstractRuntimeChild, DecoratedRevisionThing):" in source


def test_runtime_renders_materialized_child_extension_across_apps(tmp_path: Path) -> None:
    """Materialized children import the generated parent from the target runtime app."""

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


def test_runtime_boot_repairs_drift_without_pruning_but_build_prunes(tmp_path: Path) -> None:
    """Checks see all drift, boot repairs files only, and explicit build cleans."""

    runtime = runtime_for(tmp_path)
    runtime.emit_if_stale()
    runtime.check()
    models_path = runtime.runtime_dir / "resources" / "models.py"
    models_path.write_text("# stale\n", encoding="utf-8")
    orphan = runtime.runtime_dir / "removed" / "models.py"
    orphan.parent.mkdir()
    orphan.write_text("# orphan\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="stale"):
        runtime.check()
    assert runtime.emit_if_stale() is True
    assert models_path.read_text(encoding="utf-8") == runtime.render_sources()[Path("resources/models.py")]
    assert orphan.exists()
    runtime.build()
    assert not orphan.exists()
    runtime.check()


def test_runtime_check_ignores_schema_command_output(tmp_path: Path) -> None:
    """GraphQL SDL files are checked by the schema command, not build."""

    runtime = runtime_for(tmp_path)
    runtime.emit_if_stale()
    schema_path = tmp_path / "runtime" / "schemas" / "public.graphql"
    schema_path.parent.mkdir()
    schema_path.write_text("type Query { ok: Boolean! }\n", encoding="utf-8")

    runtime.check()


def test_runtime_check_ignores_graphql_codegen_output(tmp_path: Path) -> None:
    """Generated GraphQL client code is checked by its frontend owner, not build."""

    runtime = runtime_for(tmp_path)
    runtime.emit_if_stale()
    gql_path = tmp_path / "runtime" / "gql" / "public" / "graphql.ts"
    gql_path.parent.mkdir(parents=True)
    gql_path.write_text("export const ok = true;\n", encoding="utf-8")

    runtime.check()


def test_runtime_check_ignores_web_codegen_output(tmp_path: Path) -> None:
    """Generated web entry code is checked by the frontend CLI, not build."""

    runtime = runtime_for(tmp_path)
    runtime.emit_if_stale()
    app_path = tmp_path / "runtime" / "web" / "app.ts"
    app_path.write_text("export const ok = true;\n", encoding="utf-8")
    routes_path = tmp_path / "runtime" / "web" / "routes.gen.ts"
    routes_path.write_text("export const routes = [];\n", encoding="utf-8")

    runtime.check()


def test_runtime_extensions_follow_app_graph_order_not_class_names(tmp_path: Path) -> None:
    """Renaming extension classes must not change extension base precedence."""

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


@pytest.mark.parametrize("operation", ["clean_configured", "build", "emit_if_stale"])
def test_runtime_writes_require_generated_sentinel(tmp_path: Path, settings: Any, operation: str) -> None:
    """Boot repair cannot authorize a foreign directory by writing its sentinel."""

    runtime = runtime_for(tmp_path)
    settings.ANGEE_RUNTIME_DIR = runtime.runtime_dir
    runtime.runtime_dir.mkdir()
    (runtime.runtime_dir / "handwritten.py").write_text(
        "# keep\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="not an Angee runtime directory"):
        getattr(runtime, operation)()

    assert (runtime.runtime_dir / "handwritten.py").read_text(encoding="utf-8") == "# keep\n"
    assert not (runtime.runtime_dir / "__init__.py").exists()


@pytest.mark.parametrize("operation", ["build", "emit_if_stale"])
def test_runtime_writes_require_configured_directory(tmp_path: Path, settings: Any, operation: str) -> None:
    """A generated sentinel never authorizes writes outside the configured root."""

    runtime = runtime_for(tmp_path)
    runtime.emit_if_stale()
    stale = runtime.runtime_dir / "stale.py"
    stale.write_text("# keep\n", encoding="utf-8")
    settings.ANGEE_RUNTIME_DIR = tmp_path / "other_runtime"

    with pytest.raises(RuntimeError, match="not the configured runtime dir"):
        getattr(runtime, operation)()

    assert stale.read_text(encoding="utf-8") == "# keep\n"
    assert (runtime.runtime_dir / "resources" / "models.py").exists()


def test_clean_then_boot_repair_is_idempotent(tmp_path: Path, settings: Any) -> None:
    """A cleaned runtime with preserved migrations keeps its cleanup sentinel."""

    runtime = runtime_for(tmp_path)
    settings.ANGEE_RUNTIME_DIR = runtime.runtime_dir
    runtime.emit_if_stale()
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
    runtime.emit_if_stale()

    assert "ANGEE GENERATED RUNTIME" in (runtime.runtime_dir / "__init__.py").read_text(encoding="utf-8")
    assert all(path.read_text(encoding="utf-8") == "migration\n" for path in migration_paths)
    Runtime.clean_configured()
    assert all(path.read_text(encoding="utf-8") == "migration\n" for path in migration_paths)
    assert "ANGEE GENERATED RUNTIME" in (runtime.runtime_dir / "__init__.py").read_text(encoding="utf-8")
    Runtime.clean_configured()


def test_runtime_clean_refuses_migrations_without_sentinel(tmp_path: Path, settings: Any) -> None:
    """Migrations alone are not enough evidence that a directory is generated."""

    runtime = runtime_for(tmp_path)
    settings.ANGEE_RUNTIME_DIR = runtime.runtime_dir
    migration_path = runtime.runtime_dir / "resources" / "migrations" / "0001_initial.py"
    migration_path.parent.mkdir(parents=True)
    migration_path.write_text("# migration\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="not an Angee runtime directory"):
        Runtime.clean_configured()


def _compose_config() -> ComposeConfig:
    """Return a ComposeConfig bound enough for a direct import_models call."""

    config = ComposeConfig("angee.compose", compose_package)
    config.apps = apps
    return config


def test_compose_config_heals_stale_runtime_then_imports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """App population emits a stale runtime, then imports generated models.

    The hook is write-only and unconditional: it always heals drift before
    importing, so a fresh or partially-deleted runtime is repaired in-process
    rather than surfacing as a cryptic swappable-model resolution error.
    """

    calls: list[str] = []

    class FakeRuntime:
        def configure_migration_modules(self) -> None:
            calls.append("migration_modules")

        def emit_if_stale(self) -> bool:
            calls.append("emit_if_stale")
            return True

        def import_generated_models(self) -> None:
            calls.append("import")

    monkeypatch.setattr(runtime_module.Runtime, "from_django", classmethod(lambda cls: FakeRuntime()))

    _compose_config().import_models()

    assert calls == ["migration_modules", "emit_if_stale", "import"]


def test_build_check_reports_command_error_when_runtime_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``angee build --check`` converts runtime drift into a clean command error."""

    class FakeRuntime:
        def check(self) -> None:
            raise RuntimeError("generated runtime is stale: resources/models.py")

    monkeypatch.setattr(runtime_module.Runtime, "from_django", classmethod(lambda cls: FakeRuntime()))

    with pytest.raises(CommandError, match="generated runtime is stale"):
        Command()._handle_build({"check": True})


def test_build_command_delegates_the_complete_write_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The build command delegates source emission and migration writes to Runtime."""

    calls: list[str] = []

    class FakeRuntime:
        def build(self) -> AddonDependencyGroupResult:
            calls.append("build")
            return AddonDependencyGroupResult.UNCHANGED

    monkeypatch.setattr(runtime_module.Runtime, "from_django", classmethod(lambda cls: FakeRuntime()))

    Command()._handle_build({"check": False})

    assert calls == ["build"]


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

    assert runtime.build() is AddonDependencyGroupResult.SKIPPED_NO_PROJECT_DIR
    assert calls == ["render", "materialize"]


def test_runtime_build_materializes_without_rewriting_current_sources(tmp_path: Path, monkeypatch, settings) -> None:
    runtime = runtime_for(tmp_path)
    settings.MIGRATION_MODULES = {}
    runtime.emit_if_stale()
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

    assert runtime.build() is AddonDependencyGroupResult.SKIPPED_NO_PROJECT_DIR
    assert calls == ["materialize"]
    assert path.stat().st_mtime_ns == modified
    assert all(cache.exists() for cache in caches)
    assert runtime.emit_if_stale() is False


@pytest.mark.parametrize("contents", ["empty", "models", "bytecode"])
def test_runtime_build_prunes_removed_labels_before_materializing(
    tmp_path: Path, monkeypatch, settings, contents, caplog,
) -> None:
    """Removed packages count as drift with sources, bytecode, or empty directories."""

    runtime = runtime_for(tmp_path)
    settings.ANGEE_RUNTIME_DIR = runtime.runtime_dir
    settings.MIGRATION_MODULES = {}
    runtime.emit_if_stale()
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

    assert runtime.build() is AddonDependencyGroupResult.SKIPPED_NO_PROJECT_DIR
    assert not removed.exists()
    assert runtime.emit_if_stale() is False
    assert "Preserved migration directories" not in caplog.text


def test_runtime_check_validates_migrations_after_source_drift_is_clean(tmp_path: Path, monkeypatch) -> None:
    runtime = runtime_for(tmp_path)
    runtime.emit_if_stale()
    calls: list[str] = []

    class FakeMigrations:
        def check(self) -> None:
            calls.append("migration_check")

    monkeypatch.setattr(runtime, "runtime_migrations", lambda: FakeMigrations())

    runtime.check()

    assert calls == ["migration_check"]


def test_runtime_check_does_not_plan_migrations_while_sources_are_stale(tmp_path: Path, monkeypatch) -> None:
    runtime = runtime_for(tmp_path)
    runtime.emit_if_stale()
    (runtime.runtime_dir / "resources" / "models.py").write_text("# stale\n", encoding="utf-8")
    calls: list[str] = []
    monkeypatch.setattr(runtime, "runtime_migrations", lambda: calls.append("migration_check"))

    with pytest.raises(RuntimeError, match="generated runtime is stale"):
        runtime.check()

    assert calls == []


def test_emit_if_stale_never_constructs_runtime_migrations(tmp_path: Path, monkeypatch) -> None:
    runtime = runtime_for(tmp_path)
    runtime.emit_if_stale()
    monkeypatch.setattr(
        runtime,
        "runtime_migrations",
        lambda: pytest.fail("normal boot must not materialize migrations"),
        raising=False,
    )

    assert runtime.emit_if_stale() is False


def _provision_options(**overrides: Any) -> dict[str, Any]:
    """Build a provision options dict with every flag defaulted off."""

    options: dict[str, Any] = {
        "demo": False,
        "bootstrap_admin": False,
        "force_rebac": False,
        "wait_db": 60,
        "post_build": False,
    }
    options.update(overrides)
    return options


def test_provision_plan_default_flags_covers_the_no_flag_lifecycle() -> None:
    """The bare plan runs build→migrate→sync→load→schema with no optional steps."""

    assert Command._provision_plan(_provision_options()) == [
        ["angee", "build"],
        ["makemigrations", "--noinput", "--skip-checks"],
        ["migrate", "--noinput", "--skip-checks"],
        ["reconcile_permissions"],
        ["rebac", "--skip-checks", "sync", "--yes"],
        ["check"],
        ["resources", "load"],
        ["schema"],
    ]


def test_provision_plan_demo_loads_demo_resources() -> None:
    """``--demo`` appends ``--include-demo`` to the resources load step only."""

    plan = Command._provision_plan(_provision_options(demo=True))

    assert ["resources", "load", "--include-demo"] in plan
    assert ["resources", "load"] not in plan


def test_provision_plan_force_rebac_force_overwrites_the_sync() -> None:
    """``--force-rebac`` appends ``--force-overwrite`` to the rebac sync step only."""

    plan = Command._provision_plan(_provision_options(force_rebac=True))

    assert ["rebac", "--skip-checks", "sync", "--yes", "--force-overwrite"] in plan
    assert ["rebac", "--skip-checks", "sync", "--yes"] not in plan


def test_provision_plan_bootstrap_admin_appends_a_final_step() -> None:
    """``--bootstrap-admin`` appends ``bootstrap_admin`` as the last step."""

    plan = Command._provision_plan(_provision_options(bootstrap_admin=True))

    assert plan[-1] == ["bootstrap_admin"]
    assert Command._provision_plan(_provision_options())[-1] != ["bootstrap_admin"]


def test_provision_plan_combines_every_flag() -> None:
    """All flags together yield the full demo + force + bootstrap plan."""

    plan = Command._provision_plan(_provision_options(demo=True, force_rebac=True, bootstrap_admin=True))

    assert plan == [
        ["angee", "build"],
        ["makemigrations", "--noinput", "--skip-checks"],
        ["migrate", "--noinput", "--skip-checks"],
        ["reconcile_permissions"],
        ["rebac", "--skip-checks", "sync", "--yes", "--force-overwrite"],
        ["check"],
        ["resources", "load", "--include-demo"],
        ["schema"],
        ["bootstrap_admin"],
    ]


def test_provision_plan_builds_before_it_migrates() -> None:
    """The composer must emit concrete models before migrations run against them."""

    for options in (
        _provision_options(),
        _provision_options(demo=True, force_rebac=True, bootstrap_admin=True),
    ):
        plan = Command._provision_plan(options)
        makemigrations = plan.index(["makemigrations", "--noinput", "--skip-checks"])
        migrate = plan.index(["migrate", "--noinput", "--skip-checks"])
        assert plan.index(["angee", "build"]) < makemigrations < migrate


def test_provision_defers_checks_only_across_the_schema_identity_transition() -> None:
    """Persisted REBAC labels may lag emitted models until migrations finish."""

    plan = Command._provision_plan(_provision_options())

    assert [step for step in plan if "--skip-checks" in step] == [
        ["makemigrations", "--noinput", "--skip-checks"],
        ["migrate", "--noinput", "--skip-checks"],
        ["rebac", "--skip-checks", "sync", "--yes"],
    ]
    assert (
        plan.index(["migrate", "--noinput", "--skip-checks"])
        < plan.index(["rebac", "--skip-checks", "sync", "--yes"])
        < plan.index(["check"])
    )
    assert plan.index(["check"]) < plan.index(["resources", "load"])


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
    source.allowed_subjects = [{"type": "integrate/source", "relation": "", "wildcard": False}]
    source.save(update_fields=["allowed_subjects"])

    with pytest.raises(SystemCheckError, match=r"rebac\.E009"):
        call_command("check", "--tag", "rebac", verbosity=0)

    plan = Command._provision_plan(_provision_options())
    assert plan[1:6] == [
        ["makemigrations", "--noinput", "--skip-checks"],
        ["migrate", "--noinput", "--skip-checks"],
        ["reconcile_permissions"],
        ["rebac", "--skip-checks", "sync", "--yes"],
        ["check"],
    ]


def test_provision_builds_then_starts_one_post_build_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The parent builds once, then crosses one fresh-registry boundary."""

    events: list[object] = []

    def fake_run(argv: list[str], check: bool = False) -> SimpleNamespace:
        events.append(("child", argv, check))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("angee.compose.management.commands.angee.subprocess.run", fake_run)

    command = Command()
    messages: list[str] = []
    monkeypatch.setattr(command.stdout, "write", lambda message, *args, **kwargs: messages.append(message))
    monkeypatch.setattr(command, "_wait_for_database", lambda seconds: events.append(("wait", seconds)))
    monkeypatch.setattr(command, "_run_step", lambda step: events.append(("step", step)))
    options = _provision_options(
        demo=True,
        force_rebac=True,
        bootstrap_admin=True,
        wait_db=12,
    )

    command._handle_provision(options)

    manage_py = Command._manage_py_path()
    assert events == [
        ("wait", 12),
        ("step", ["angee", "build"]),
        (
            "child",
            [
                sys.executable,
                manage_py,
                "angee",
                "provision",
                "--post-build",
                "--demo",
                "--force-rebac",
                "--bootstrap-admin",
            ],
            False,
        ),
    ]
    assert messages[-1] == "angee provision: ok"


def test_provision_reports_a_failed_post_build_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-zero child result stops the parent and reports the process boundary."""

    monkeypatch.setattr(
        "angee.compose.management.commands.angee.subprocess.run",
        lambda argv, check=False: SimpleNamespace(returncode=7),
    )
    command = Command()
    monkeypatch.setattr(command, "_wait_for_database", lambda seconds: None)
    monkeypatch.setattr(command, "_run_step", lambda step: None)

    with pytest.raises(CommandError, match=r"post-build commands failed \(exit 7\)"):
        command._handle_provision(_provision_options())


def test_provision_build_failure_never_starts_the_post_build_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fresh process boundary is crossed only after a successful build."""

    def fail_build(step: list[str]) -> None:
        raise CommandError("build failed")

    command = Command()
    monkeypatch.setattr(command, "_wait_for_database", lambda seconds: None)
    monkeypatch.setattr(command, "_run_step", fail_build)
    monkeypatch.setattr(
        "angee.compose.management.commands.angee.subprocess.run",
        lambda *args, **kwargs: pytest.fail("a failed build must not spawn the post-build child"),
    )

    with pytest.raises(CommandError, match="build failed"):
        command._handle_provision(_provision_options())


def test_provision_post_build_runs_remaining_steps_in_order_without_respawning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The child reuses one populated registry and never repeats parent work."""

    calls: list[list[str]] = []
    options = _provision_options(post_build=True, demo=True, bootstrap_admin=True)
    command = Command()
    messages: list[str] = []
    monkeypatch.setattr(command.stdout, "write", lambda message, *args, **kwargs: messages.append(message))
    monkeypatch.setattr(command, "_run_step", lambda step: calls.append(step))
    monkeypatch.setattr(
        command,
        "_wait_for_database",
        lambda seconds: pytest.fail("the post-build child must not wait for the database"),
    )
    monkeypatch.setattr(
        "angee.compose.management.commands.angee.subprocess.run",
        lambda *args, **kwargs: pytest.fail("the post-build child must not spawn another child"),
    )

    command._handle_provision(options)

    assert calls == Command._provision_plan(options)[1:]
    assert "angee provision: ok" not in messages


def test_provision_post_build_aborts_on_the_first_failed_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An in-process command failure stops the remaining post-build sequence."""

    calls: list[list[str]] = []

    def fake_step(step: list[str]) -> None:
        calls.append(step)
        if step[0] == "migrate":
            raise CommandError("angee provision: step 'migrate --noinput --skip-checks' failed: boom")

    command = Command()
    monkeypatch.setattr(command, "_run_step", fake_step)

    with pytest.raises(CommandError, match="step 'migrate --noinput --skip-checks' failed"):
        command._handle_provision(_provision_options(post_build=True))

    assert calls == [
        ["makemigrations", "--noinput", "--skip-checks"],
        ["migrate", "--noinput", "--skip-checks"],
    ]


def test_provision_run_step_preserves_each_commands_cli_check_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checked commands receive policy; commands opting out receive no unknown option."""

    calls: list[tuple[str, tuple[str, ...], bool | None]] = []

    def fake_call_command(command: BaseCommand, *args: str, **options: Any) -> None:
        calls.append((command.__class__.__module__, args, options.get("skip_checks")))

    monkeypatch.setattr("angee.compose.management.commands.angee.call_command", fake_call_command)
    command = Command()
    for step in Command._provision_plan(_provision_options()):
        command._run_step(step)

    policies = [(module.rsplit(".", 1)[-1], policy) for module, _args, policy in calls]
    assert policies == [
        ("angee", None),
        ("makemigrations", True),
        ("migrate", True),
        ("reconcile_permissions", None),
        ("rebac", True),
        ("check", None),
        ("resources", None),
        ("schema", None),
    ]


def test_provision_run_step_calls_actual_check_free_angee_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A check-free command executes without the parser-rejected ``skip_checks`` option."""

    handled: list[dict[str, Any]] = []
    monkeypatch.setattr(Command, "_handle_build", lambda self, options: handled.append(options))

    Command()._run_step(["angee", "build"])

    assert len(handled) == 1
    assert handled[0]["check"] is False


def test_provision_run_step_names_a_failing_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """Native command exceptions retain the provision step that raised them."""

    def fail(*args: Any, **options: Any) -> None:
        raise RuntimeError("broken schema")

    monkeypatch.setattr("angee.compose.management.commands.angee.call_command", fail)

    with pytest.raises(CommandError, match="step 'schema' failed: broken schema"):
        Command()._run_step(["schema"])


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
    monkeypatch.setattr("angee.compose.management.commands.angee.call_command", run)

    if fails:
        with pytest.raises(CommandError, match="step 'schema' failed: failed"):
            command._run_step(["schema"])
    else:
        command._run_step(["schema"])

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

    monkeypatch.setattr("angee.compose.management.commands.angee.call_command", exit_successfully)

    Command()._run_step(["schema"])


def test_provision_run_step_names_a_nonzero_system_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A command's nonzero SystemExit becomes a step-specific provision error."""

    def exit_with_failure(*args: Any, **kwargs: Any) -> None:
        raise SystemExit(4)

    monkeypatch.setattr("angee.compose.management.commands.angee.call_command", exit_with_failure)

    with pytest.raises(CommandError, match=r"step 'schema' failed \(exit 4\)"):
        Command()._run_step(["schema"])


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

    monkeypatch.setattr("angee.compose.management.commands.angee.get_commands", lambda: {"rebac": "fake"})
    monkeypatch.setattr(
        "angee.compose.management.commands.angee.load_command_class",
        lambda app_name, name: CheckedCommand(),
    )

    Command()._run_step(["rebac", "sync", "--yes"])

    assert lifecycle == ["check", "handle"]


def test_provision_parser_accepts_the_internal_post_build_entrypoint() -> None:
    """Django's native parser carries flags into the internal child invocation."""

    parser = Command().create_parser("manage.py", "angee")

    options = vars(parser.parse_args(["provision", "--post-build", "--demo", "--force-rebac", "--bootstrap-admin"]))

    assert options["post_build"] is True
    assert options["demo"] is True
    assert options["force_rebac"] is True
    assert options["bootstrap_admin"] is True


def test_provision_help_hides_the_internal_post_build_entrypoint(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The implementation-only child switch stays out of the public CLI help."""

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
    monkeypatch.setattr("angee.compose.management.commands.angee.connections", {"default": connection})
    monkeypatch.setattr("angee.compose.management.commands.angee.time.sleep", lambda seconds: None)

    Command()._wait_for_database(10)

    assert connection.attempts == 3
    assert connection.closed is True


def test_provision_wait_times_out_with_the_last_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A database that never answers raises CommandError with the last error."""

    connection = _FakeConnection(fail_times=99)
    monkeypatch.setattr("angee.compose.management.commands.angee.connections", {"default": connection})
    monkeypatch.setattr("angee.compose.management.commands.angee.time.sleep", lambda seconds: None)

    with pytest.raises(CommandError, match="within 3s: connection refused"):
        Command()._wait_for_database(3)

    assert connection.attempts == 3


def test_provision_manage_py_path_is_absolute() -> None:
    """The child entrypoint is the resolved absolute ``manage.py`` path."""

    manage_py = Command._manage_py_path()

    assert Path(manage_py).is_absolute()
    assert Path(manage_py) == Path(sys.argv[0]).resolve()


def test_appgraph_annotates_roots_and_dependencies() -> None:
    """resolve() tags declared roots and normalizes each app's dependencies.

    The platform console reads these annotations instead of re-deriving the
    composed graph (``addons/angee/platform/schema.py``).
    """

    graph = AppGraph()
    configs = {config.name: config for config in graph.resolve(["angee.iam"])}

    iam = configs["angee.iam"]
    assert iam.angee_addon_root is True
    assert iam.angee_root_declaration == "angee.iam"
    assert "angee.resources" in addon_manifest(iam).depends_on
    assert not hasattr(iam, "angee_depends_on")

    # `resources` is pulled in through iam's closure, not declared — a dependency.
    assert configs["angee.resources"].angee_addon_root is False
    assert configs["angee.resources"].angee_root_declaration is None

    # `forced` = another resolved app depends on me (cannot be uninstalled). `resources`
    # is in iam's closure → forced; the sole declared root nothing depends on is not.
    assert configs["angee.resources"].angee_forced is True
    assert iam.angee_forced is False


def test_appgraph_rejects_duplicate_roots() -> None:
    """A repeated explicit root app is a settings error, not hidden dedupe."""

    with pytest.raises(ImproperlyConfigured, match="Duplicate Django app 'angee.resources'"):
        AppGraph().resolve(["angee.resources", "angee.resources"])


def test_appgraph_root_wins_when_also_a_dependency() -> None:
    """An app declared as a root remains a root even if another root depends on it."""

    configs = {config.name: config for config in AppGraph().resolve(["angee.iam", "angee.resources"])}

    assert configs["angee.iam"].angee_addon_root is True
    assert configs["angee.resources"].angee_addon_root is True


def test_appgraph_preserves_authored_app_config_root_declaration() -> None:
    """Runtime drift compares the exact root spelling authored in settings YAML."""

    declaration = "angee.iam.apps.IAMConfig"
    configs = {config.name: config for config in AppGraph().resolve([declaration])}

    assert configs["angee.iam"].angee_root_declaration == declaration


def test_appgraph_distinguishes_project_roots_from_injected_runtime_roots() -> None:
    """Framework defaults run but do not become project-authored drift facts."""

    declaration = "angee.iam.apps.IAMConfig"
    configs = {
        config.name: config
        for config in AppGraph().resolve(
            ["django.contrib.contenttypes", declaration],
            declared_roots=[declaration],
        )
    }

    assert configs["angee.iam"].angee_root_declaration == declaration
    assert configs["angee.iam"].angee_addon_root is True
    assert configs["django.contrib.contenttypes"].angee_root_declaration is None
    assert configs["django.contrib.contenttypes"].angee_addon_root is False


def test_appgraph_rejects_duplicate_dependencies() -> None:
    """Repeated dependencies are rejected at their declaring owner."""

    config = make_addon(name="tests.duplicate_dependency", depends_on=("angee.base", "angee.base"))

    with pytest.raises(ImproperlyConfigured, match="duplicate dependency"):
        AppGraph().resolve([config])


def test_project_env_file_loads_without_overriding_process_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The project-root .env seeds env vars for host runs; real process env wins.

    The stack's gitignored `.env` (secrets plus derived DATABASE_URL) is what lets
    a bare `uv run manage.py …` talk to the stack database; operator-managed
    services set their env explicitly, so read_env must never overwrite it.
    """

    from angee.compose.project import ProjectContract

    (tmp_path / ".env").write_text(
        'DATABASE_URL="postgres://angee:pw@127.0.0.1:5433/angee"\nYAMLCONF_SECRET_KEY="from-env-file"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("YAMLCONF_SECRET_KEY", "from-process-env")

    import os

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

    from angee.compose.project import ProjectContract

    ProjectContract({})._read_project_env(tmp_path)


def test_runtime_boot_repair_renders_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = runtime_for(tmp_path)
    original = runtime.render_sources
    calls = []

    def render():
        calls.append("render")
        return original()

    monkeypatch.setattr(runtime, "render_sources", render)
    runtime.emit_if_stale()
    assert calls == ["render"]
    assert (runtime.runtime_dir / "resources" / "models.py").is_file()


def test_runtime_from_django_does_not_bind_migrations_or_write_sources(
    tmp_path: Path,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.ANGEE_RUNTIME_DIR = tmp_path / "runtime"
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
    settings.ANGEE_RUNTIME_DIR = runtime_dir
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


@pytest.mark.parametrize("swapped", [False, True])
@isolate_apps("angee.resources")
def test_configured_cleanup_requires_no_discovery_or_rendering(
    tmp_path: Path,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    swapped: bool,
) -> None:
    settings.ANGEE_RUNTIME_DIR = tmp_path / "runtime"
    runtime = runtime_for(tmp_path)
    settings.ANGEE_RUNTIME_MODULE = runtime.runtime_module
    settings.MIGRATION_MODULES = {"removed": f"{runtime.runtime_module}.removed.migrations"}
    if swapped:
        source = runtime.composition.ordered_models[0]
        monkeypatch.setattr(source.Meta, "swappable", "CLEANUP_RESOURCE_MODEL", raising=False)
        settings.CLEANUP_RESOURCE_MODEL = "resources.Replacement"
    runtime.emit_if_stale()
    module = ModuleType(f"{runtime.runtime_module}.resources.models")
    exec(compile(runtime.render_sources()[Path("resources/models.py")], module.__name__, "exec"), vars(module))
    monkeypatch.setattr(runtime_module, "apps", module.Resource._meta.apps)
    assert bool(module.Resource._meta.swapped) is swapped
    migration = runtime.runtime_dir / "resources" / "migrations" / "0001_saved.py"
    migration.write_text("# preserved migration\n")
    removed_migration = runtime.runtime_dir / "removed" / "migrations" / "0001_stale.py"
    removed_migration.parent.mkdir(parents=True)
    removed_migration.write_text("import deleted_addon\n", encoding="utf-8")
    (removed_migration.parent.parent / "models.py").write_text("# old generated models\n", encoding="utf-8")

    def forbidden(*args, **kwargs):
        pytest.fail("cleanup must not discover or render source models")

    monkeypatch.setattr(ModelComposition, "discover", forbidden)
    monkeypatch.setattr(Runtime, "render_sources", forbidden)
    with caplog.at_level(logging.WARNING, logger="angee.fs"):
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
import angee.compose.project
import angee.compose.bootstrap
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
