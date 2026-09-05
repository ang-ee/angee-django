"""Tests for build-time runtime composition."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
from django.apps import AppConfig, apps
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import CommandError
from django.db import OperationalError, models

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
    assert '@source "../../web/node_modules/@angee/resources/src";' in sources[Path("web/tailwind.sources.css")]


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
    settings.MIGRATION_MODULES = {"custom": "custom.migrations"}

    runtime.configure_migration_modules()
    assert settings.MIGRATION_MODULES["custom"] == "custom.migrations"
    assert settings.MIGRATION_MODULES["resources"] == "runtime.resources.migrations"


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
    assert "catalogue = True" not in child_body
    assert "catalogue_tier" not in child_body


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


def test_runtime_boot_repairs_drift_without_pruning_but_emit_prunes(tmp_path: Path) -> None:
    """Checks see all drift, boot repairs files only, and explicit emit prunes."""

    runtime = runtime_for(tmp_path)
    runtime.emit()
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
    runtime.emit()
    assert not orphan.exists()
    runtime.check()


def test_runtime_check_ignores_schema_command_output(tmp_path: Path) -> None:
    """GraphQL SDL files are checked by the schema command, not build."""

    runtime = runtime_for(tmp_path)
    runtime.emit()
    schema_path = tmp_path / "runtime" / "schemas" / "public.graphql"
    schema_path.parent.mkdir()
    schema_path.write_text("type Query { ok: Boolean! }\n", encoding="utf-8")

    runtime.check()


def test_runtime_check_ignores_graphql_codegen_output(tmp_path: Path) -> None:
    """Generated GraphQL client code is checked by its frontend owner, not build."""

    runtime = runtime_for(tmp_path)
    runtime.emit()
    gql_path = tmp_path / "runtime" / "gql" / "public" / "graphql.ts"
    gql_path.parent.mkdir(parents=True)
    gql_path.write_text("export const ok = true;\n", encoding="utf-8")

    runtime.check()


def test_runtime_check_ignores_web_codegen_output(tmp_path: Path) -> None:
    """Generated web entry code is checked by the frontend CLI, not build."""

    runtime = runtime_for(tmp_path)
    runtime.emit()
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


def test_runtime_clean_requires_generated_sentinel(tmp_path: Path) -> None:
    """Clean refuses to delete a non-generated configured runtime dir."""

    runtime = runtime_for(tmp_path)
    runtime.runtime_dir.mkdir()
    (runtime.runtime_dir / "handwritten.py").write_text(
        "# keep\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="not an Angee runtime directory"):
        runtime.clean()


def test_clean_then_emit_is_idempotent(tmp_path: Path, settings: Any) -> None:
    """A cleaned runtime with preserved migrations keeps its cleanup sentinel."""

    runtime = runtime_for(tmp_path)
    settings.ANGEE_RUNTIME_DIR = runtime.runtime_dir
    runtime.emit()
    migration_paths = (
        runtime.runtime_dir / "resources" / "migrations" / "0001_initial.py",
        runtime.runtime_dir / "resources" / "migrations" / "archive" / "snapshot.txt",
        runtime.runtime_dir / "removed" / "migrations" / "nested" / "data.bin",
    )
    for path in migration_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("migration\n", encoding="utf-8")

    runtime.clean()
    assert "ANGEE GENERATED RUNTIME" in (runtime.runtime_dir / "__init__.py").read_text(encoding="utf-8")
    runtime.emit()

    assert "ANGEE GENERATED RUNTIME" in (runtime.runtime_dir / "__init__.py").read_text(encoding="utf-8")
    assert all(path.read_text(encoding="utf-8") == "migration\n" for path in migration_paths)
    runtime.clean()
    assert all(path.read_text(encoding="utf-8") == "migration\n" for path in migration_paths)
    assert "ANGEE GENERATED RUNTIME" in (runtime.runtime_dir / "__init__.py").read_text(encoding="utf-8")
    runtime.clean()


def test_runtime_clean_refuses_migrations_without_sentinel(tmp_path: Path) -> None:
    """Migrations alone are not enough evidence that a directory is generated."""

    runtime = runtime_for(tmp_path)
    migration_path = runtime.runtime_dir / "resources" / "migrations" / "0001_initial.py"
    migration_path.parent.mkdir(parents=True)
    migration_path.write_text("# migration\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="not an Angee runtime directory"):
        runtime.clean()


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


def test_runtime_build_emits_stale_sources_once_before_materializing(tmp_path: Path, monkeypatch) -> None:
    runtime = runtime_for(tmp_path)
    calls: list[str] = []
    original_render = runtime.render_sources

    def render():
        calls.append("render")
        return original_render()

    class FakeMigrations:
        def materialize(self) -> tuple[Path, ...]:
            assert "class Resource" in (runtime.runtime_dir / "resources" / "models.py").read_text()
            calls.append("materialize")
            return ()

    monkeypatch.setattr(runtime, "render_sources", render)
    monkeypatch.setattr(runtime, "runtime_migrations", lambda: FakeMigrations())

    assert runtime.build() is AddonDependencyGroupResult.SKIPPED_NO_PROJECT_DIR
    assert calls == ["render", "materialize"]


def test_runtime_build_materializes_without_rewriting_current_sources(tmp_path: Path, monkeypatch) -> None:
    runtime = runtime_for(tmp_path)
    runtime.emit()
    path = runtime.runtime_dir / "resources" / "models.py"
    modified = path.stat().st_mtime_ns
    calls: list[str] = []

    class FakeMigrations:
        def materialize(self) -> tuple[Path, ...]:
            calls.append("materialize")
            return ()

    monkeypatch.setattr(runtime, "runtime_migrations", lambda: FakeMigrations())

    assert runtime.build() is AddonDependencyGroupResult.SKIPPED_NO_PROJECT_DIR
    assert calls == ["materialize"]
    assert path.stat().st_mtime_ns == modified


def test_runtime_check_validates_migrations_after_source_drift_is_clean(tmp_path: Path, monkeypatch) -> None:
    runtime = runtime_for(tmp_path)
    runtime.emit()
    calls: list[str] = []

    class FakeMigrations:
        def check(self) -> None:
            calls.append("migration_check")

    monkeypatch.setattr(runtime, "runtime_migrations", lambda: FakeMigrations())

    runtime.check()

    assert calls == ["migration_check"]


def test_runtime_check_does_not_plan_migrations_while_sources_are_stale(tmp_path: Path, monkeypatch) -> None:
    runtime = runtime_for(tmp_path)
    runtime.emit()
    (runtime.runtime_dir / "resources" / "models.py").write_text("# stale\n", encoding="utf-8")
    calls: list[str] = []
    monkeypatch.setattr(runtime, "runtime_migrations", lambda: calls.append("migration_check"))

    with pytest.raises(RuntimeError, match="generated runtime is stale"):
        runtime.check()

    assert calls == []


def test_emit_if_stale_never_constructs_runtime_migrations(tmp_path: Path, monkeypatch) -> None:
    runtime = runtime_for(tmp_path)
    runtime.emit()
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
    }
    options.update(overrides)
    return options


def test_provision_plan_default_flags_covers_the_no_flag_lifecycle() -> None:
    """The bare plan runs build→migrate→sync→load→schema with no optional steps."""

    assert Command._provision_plan(_provision_options()) == [
        ["angee", "build"],
        ["reconcile_permissions"],
        ["makemigrations"],
        ["migrate", "--noinput"],
        ["rebac", "sync", "--yes"],
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

    assert ["rebac", "sync", "--yes", "--force-overwrite"] in plan
    assert ["rebac", "sync", "--yes"] not in plan


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
        ["reconcile_permissions"],
        ["makemigrations"],
        ["migrate", "--noinput"],
        ["rebac", "sync", "--yes", "--force-overwrite"],
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
        build = plan.index(["angee", "build"])
        makemigrations = plan.index(["makemigrations"])
        migrate = plan.index(["migrate", "--noinput"])
        assert build < makemigrations < migrate


def test_provision_runs_every_step_as_a_fresh_child_interpreter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each step spawns ``python <manage.py> <step>`` in order, streaming output."""

    calls: list[list[str]] = []

    def fake_run(argv: list[str], check: bool = False) -> SimpleNamespace:
        calls.append(argv)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("angee.compose.management.commands.angee.subprocess.run", fake_run)

    command = Command()
    monkeypatch.setattr(command, "_wait_for_database", lambda seconds: None)
    command._handle_provision(_provision_options())

    manage_py = Command._manage_py_path()
    assert calls == [[sys.executable, manage_py, *step] for step in Command._provision_plan(_provision_options())]


def test_provision_aborts_on_the_first_failed_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-zero child exit stops provision and names the failing step."""

    calls: list[list[str]] = []

    def fake_run(argv: list[str], check: bool = False) -> SimpleNamespace:
        calls.append(argv)
        returncode = 1 if argv[2:] == ["makemigrations"] else 0
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr("angee.compose.management.commands.angee.subprocess.run", fake_run)

    command = Command()
    monkeypatch.setattr(command, "_wait_for_database", lambda seconds: None)

    with pytest.raises(CommandError, match="step 'makemigrations' failed"):
        command._handle_provision(_provision_options())

    # Stops at the failing step: build, reconcile_permissions, makemigrations.
    assert [argv[2:] for argv in calls] == [
        ["angee", "build"],
        ["reconcile_permissions"],
        ["makemigrations"],
    ]


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
    assert "angee.resources" in addon_manifest(iam).depends_on
    assert not hasattr(iam, "angee_depends_on")

    # `resources` is pulled in through iam's closure, not declared — a dependency.
    assert configs["angee.resources"].angee_addon_root is False

    # `forced` = another resolved app depends on me (cannot be uninstalled). `resources`
    # is in iam's closure → forced; the sole declared root nothing depends on is not.
    assert configs["angee.resources"].angee_forced is True
    assert iam.angee_forced is False


def test_appgraph_rejects_duplicate_roots() -> None:
    """A repeated explicit root app is a settings error, not hidden dedupe."""

    with pytest.raises(ImproperlyConfigured, match="Duplicate root app 'angee.resources'"):
        AppGraph().resolve(["angee.resources", "angee.resources"])


def test_appgraph_root_wins_when_also_a_dependency() -> None:
    """An app declared as a root remains a root even if another root depends on it."""

    configs = {config.name: config for config in AppGraph().resolve(["angee.iam", "angee.resources"])}

    assert configs["angee.iam"].angee_addon_root is True
    assert configs["angee.resources"].angee_addon_root is True


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


def test_runtime_emit_renders_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = runtime_for(tmp_path)
    original = runtime.render_sources
    calls = []

    def render():
        calls.append("render")
        return original()

    monkeypatch.setattr(runtime, "render_sources", render)
    runtime.emit()
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


def test_configured_cleanup_requires_no_discovery_or_rendering(
    tmp_path: Path,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.ANGEE_RUNTIME_DIR = tmp_path / "runtime"
    runtime = runtime_for(tmp_path)
    runtime.emit()
    migration = runtime.runtime_dir / "resources" / "migrations" / "0001_saved.py"
    migration.write_text("# preserved migration\n")

    def forbidden(*args, **kwargs):
        pytest.fail("cleanup must not discover or render source models")

    monkeypatch.setattr(ModelComposition, "discover", forbidden)
    monkeypatch.setattr(Runtime, "render_sources", forbidden)
    Runtime.clean_configured()
    assert migration.read_text() == "# preserved migration\n"
    assert not (runtime.runtime_dir / "resources" / "models.py").exists()
    runtime.reset()
    runtime.clean()
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
