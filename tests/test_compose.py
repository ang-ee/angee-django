"""Tests for build-time runtime composition."""

from __future__ import annotations

import sys
from dataclasses import is_dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
from django.apps import AppConfig, apps
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import CommandError
from django.db import models

import angee.compose as compose_package
import angee.compose.runtime as runtime_module
from angee.base.mixins import HistoryMixin, RevisionMixin
from angee.base.models import AngeeModel
from angee.compose.appgraph import AppGraph
from angee.compose.apps import ComposeConfig
from angee.compose.management.commands.angee import Command
from angee.compose.runtime import Runtime
from angee.compose.web import WebRuntime
from tests.conftest import make_contract


@pytest.fixture
def stub_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve a test ``_addon_contract`` through the compose readers.

    The web and appgraph projectors read ``addon_contract`` at module scope. The
    stubs in these tests are bare app configs with no ``addon.toml`` on disk, so a
    test attaches an in-memory contract as ``_addon_contract`` and this points the
    readers at it — keeping the injection on the test side, out of the production
    reader (which has the manifest as its sole source).
    """

    def fake(app_config: Any) -> Any:
        return getattr(app_config, "_addon_contract", None)

    monkeypatch.setattr("angee.compose.web.addon_contract", fake)
    monkeypatch.setattr("angee.compose.appgraph.addon_contract", fake)


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

    return Runtime(
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


def test_runtime_model_render_plan_is_named() -> None:
    """The runtime model render plan is a named owner, not an anonymous tuple."""

    assert is_dataclass(runtime_module.RuntimeModelRenderPlan)


def test_runtime_model_render_plan_keeps_model_owned_meta(tmp_path: Path) -> None:
    """Each named render plan carries the Meta facts for its own model."""

    source = Runtime((), runtime_dir=tmp_path / "runtime")._models_source(
        "tests",
        (FirstRenderPlanMetaThing, SecondRenderPlanMetaThing),
    )

    first_source = source[
        source.index("class FirstRenderPlanMetaThing") : source.index("class SecondRenderPlanMetaThing")
    ]
    assert "rebac_resource_type = 'tests/first-render-plan'" in first_source
    assert "rebac_resource_type = 'tests/second-render-plan'" not in first_source
    assert "rebac_resource_type = 'tests/second-render-plan'" in source


def test_web_runtime_projects_addon_web_packages_in_composed_order(stub_contracts: None) -> None:
    """Addon web package declarations feed one generated web manifest."""

    first = SimpleNamespace(name="tests.first", label="first", _addon_contract=make_contract(web="@demo/first"))
    backend_only = SimpleNamespace(name="tests.backend", label="backend")
    second = SimpleNamespace(name="tests.second", label="second", _addon_contract=make_contract(web="@demo/second"))

    manifest = WebRuntime((first, backend_only, second)).manifest_json()

    assert manifest.index('"package": "@demo/first"') < manifest.index('"package": "@demo/second"')
    assert "tests.backend" not in manifest
    # The composer holds no schema-name knowledge — the CLI discovers schemas
    # from the SDL on disk — so the manifest carries no schema list.
    assert '"schemas"' not in manifest


def test_web_runtime_projects_external_codegen_entries(stub_contracts: None) -> None:
    """An addon's web_codegen declaration projects into the manifest."""

    daemon = SimpleNamespace(
        name="tests.daemon",
        label="daemon",
        _addon_contract=make_contract(
            web="@demo/daemon",
            web_codegen={
                "schema": "operator",
                "sdl": "schema/operator.graphql",
                "documents": "documents.daemon.ts",
                "types": True,
            },
        ),
    )

    manifest = WebRuntime((daemon,)).manifest_json()

    assert '"schema": "operator"' in manifest
    assert '"package": "@demo/daemon"' in manifest
    assert '"sdl": "schema/operator.graphql"' in manifest
    assert '"documents": "documents.daemon.ts"' in manifest
    assert '"app": "tests.daemon"' in manifest


def test_web_runtime_rejects_codegen_without_web_package(stub_contracts: None) -> None:
    """An external codegen entry requires its addon to ship a web package."""

    daemon = SimpleNamespace(
        name="tests.daemon",
        label="daemon",
        _addon_contract=make_contract(web_codegen={"schema": "operator", "sdl": "s.graphql", "documents": "d.ts"}),
    )

    with pytest.raises(ImproperlyConfigured, match=r"requires \[web\]\.package"):
        WebRuntime((daemon,))


def test_web_runtime_rejects_duplicate_addon_web_packages(stub_contracts: None) -> None:
    """Two addons cannot claim the same web package identity."""

    first = SimpleNamespace(name="tests.first", label="first", _addon_contract=make_contract(web="@demo/shared"))
    second = SimpleNamespace(name="tests.second", label="second", _addon_contract=make_contract(web="@demo/shared"))

    with pytest.raises(ImproperlyConfigured, match=r"Duplicate \[web\]\.package"):
        WebRuntime((first, second))


def test_web_runtime_rejects_invalid_package_names(stub_contracts: None) -> None:
    """The web package contract fails before a broken manifest is emitted."""

    broken = SimpleNamespace(name="tests.broken", label="broken", _addon_contract=make_contract(web="../broken"))

    with pytest.raises(ImproperlyConfigured, match="valid npm package name"):
        WebRuntime((broken,))


def test_runtime_configures_migrations_for_runtime_labels(tmp_path: Path, settings: Any) -> None:
    """Runtime owns migration redirects for labels it materializes."""

    runtime = runtime_for(tmp_path)
    settings.MIGRATION_MODULES = {"custom": "custom.migrations"}

    returned = runtime.configure_migration_modules()

    assert returned is runtime
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
    runtime = Runtime(
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


def test_runtime_renders_model_decorators_from_mixins(tmp_path: Path) -> None:
    """Mixin-declared decorators are emitted on concrete runtime models."""

    app_config = SimpleNamespace(
        label="tests",
        name=__name__,
        module=sys.modules[__name__],
        models_module=sys.modules[__name__],
    )
    runtime = Runtime((app_config,), runtime_dir=tmp_path / "runtime")

    source = runtime.render_sources()[Path("tests/models.py")]

    assert "import reversion" in source
    assert "@reversion.register(fields=('body',))" in source
    assert source.index("@reversion.register") < source.index("class DecoratedRevisionThing")


def test_runtime_renders_model_attributes_from_mixins(tmp_path: Path) -> None:
    """Mixin-declared class attributes are emitted on concrete runtime models."""

    app_config = SimpleNamespace(
        label="tests",
        name=__name__,
        module=sys.modules[__name__],
        models_module=sys.modules[__name__],
    )

    source = Runtime((app_config,), runtime_dir=tmp_path / "runtime").render_sources()[Path("tests/models.py")]

    assert "import simple_history.models" in source
    assert "history = simple_history.models.HistoricalRecords(app='tests')" in source
    assert source.index("history = simple_history") < source.index("class Meta(_DecoratedHistoryThingMeta)")


def test_runtime_emits_only_models_marked_runtime(tmp_path: Path) -> None:
    """Only abstract source models declaring ``runtime = True`` are emitted."""

    app_config = SimpleNamespace(
        label="tests",
        name=__name__,
        module=sys.modules[__name__],
        models_module=sys.modules[__name__],
    )

    source = Runtime((app_config,), runtime_dir=tmp_path / "runtime").render_sources()[Path("tests/models.py")]

    assert "class DecoratedRevisionThing" in source
    assert "class SkippedRuntimeThing" not in source


def test_runtime_renders_materialized_child_extension(tmp_path: Path) -> None:
    """``extends`` + ``runtime = True`` emits a concrete MTI child model."""

    class RuntimeChild(AngeeModel):
        runtime = True
        extends = "tests.DecoratedRevisionThing"
        child_value = models.CharField(max_length=16)

        class Meta:
            abstract = True
            app_label = "tests"

    app_config = SimpleNamespace(
        label="tests",
        name=__name__,
        module=sys.modules[__name__],
        models_module=SimpleNamespace(
            DecoratedRevisionThing=DecoratedRevisionThing,
            RuntimeChild=RuntimeChild,
        ),
    )

    source = Runtime((app_config,), runtime_dir=tmp_path / "runtime").render_sources()[Path("tests/models.py")]

    assert "from runtime.tests.models import DecoratedRevisionThing" not in source
    assert "class DecoratedRevisionThing(AbstractDecoratedRevisionThing):" in source
    assert "class RuntimeChild(DecoratedRevisionThing, AbstractRuntimeChild):" in source


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
        (AngeeModel,),
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

    runtime = Runtime(
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

    assert "from runtime.target.models import TargetRuntime as TargetRuntime" in child_source
    assert "from tests.child.models import RuntimeChild as AbstractRuntimeChild" in child_source
    assert "class RuntimeChild(TargetRuntime, AbstractRuntimeChild):" in child_source
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
        Runtime((app_config,), runtime_dir=tmp_path / "runtime")


def test_runtime_rejects_mismatched_extension_model_label(tmp_path: Path) -> None:
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
        Runtime((target_config, extension_config), runtime_dir=tmp_path / "runtime")


def test_runtime_emit_and_check_detect_drift(tmp_path: Path) -> None:
    """Emit writes deterministic files and check reports later drift."""

    runtime = runtime_for(tmp_path)
    runtime.emit()
    runtime.check()

    (tmp_path / "runtime" / "resources" / "models.py").write_text(
        "# stale\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="stale"):
        runtime.check()
    assert (tmp_path / "runtime" / "resources" / "models.py").read_text(encoding="utf-8") == "# stale\n"


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

    runtime = Runtime(
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


def test_runtime_aggregates_multiple_after_resource_load_donors(tmp_path: Path) -> None:
    """Two donors defining ``after_resource_load`` emit one ordered aggregator (F-c)."""

    def first_hook(cls: type, instances: object, **kwargs: object) -> None:
        del cls, instances, kwargs

    def second_hook(cls: type, instances: object, **kwargs: object) -> None:
        del cls, instances, kwargs

    target_module = ModuleType("tests.mergetarget.models")
    first_module = ModuleType("tests.mergefirst.models")
    second_module = ModuleType("tests.mergesecond.models")
    _source_model(target_module, "MergeTarget", "mergetarget", runtime=True)
    _source_model(
        first_module,
        "MergeFirstDonor",
        "mergefirst",
        extends="mergetarget.MergeTarget",
        after_resource_load=classmethod(first_hook),
    )
    _source_model(
        second_module,
        "MergeSecondDonor",
        "mergesecond",
        extends="mergetarget.MergeTarget",
        after_resource_load=classmethod(second_hook),
    )

    runtime = Runtime(
        (
            _addon_config("mergetarget", target_module),
            _addon_config("mergefirst", first_module),
            _addon_config("mergesecond", second_module),
        ),
        runtime_dir=tmp_path / "runtime",
    )

    source = runtime.render_sources()[Path("mergetarget/models.py")]

    assert "def after_resource_load(cls, *args: object, **kwargs: object) -> None:" in source
    first_call = "MergeTargetExtension1.after_resource_load.__func__(cls, *args, **kwargs)"
    second_call = "MergeTargetExtension2.after_resource_load.__func__(cls, *args, **kwargs)"
    # Dependency order = the emitted base-tuple order (app-graph order): donor one before donor two.
    assert source.index(first_call) < source.index(second_call)
    # The aggregator is a class member, not free-floating.
    assert source.index("class MergeTarget(") < source.index(first_call) < source.index("class Meta(")


def test_runtime_keeps_single_after_resource_load_donor_native(tmp_path: Path) -> None:
    """A single donor resolves natively — the composer emits no aggregator (F-c)."""

    def only_hook(cls: type, instances: object, **kwargs: object) -> None:
        del cls, instances, kwargs

    target_module = ModuleType("tests.solotarget.models")
    donor_module = ModuleType("tests.solodonor.models")
    _source_model(target_module, "SoloTarget", "solotarget", runtime=True)
    _source_model(
        donor_module,
        "SoloDonor",
        "solodonor",
        extends="solotarget.SoloTarget",
        after_resource_load=classmethod(only_hook),
    )

    runtime = Runtime(
        (
            _addon_config("solotarget", target_module),
            _addon_config("solodonor", donor_module),
        ),
        runtime_dir=tmp_path / "runtime",
    )

    source = runtime.render_sources()[Path("solotarget/models.py")]

    assert "def after_resource_load(" not in source


def test_runtime_dedupes_shared_after_resource_load_function(tmp_path: Path) -> None:
    """Two donors inheriting the same hook function collapse to native dispatch (F-c)."""

    def shared_hook(cls: type, instances: object, **kwargs: object) -> None:
        del cls, instances, kwargs

    target_module = ModuleType("tests.dedupetarget.models")
    first_module = ModuleType("tests.dedupefirst.models")
    second_module = ModuleType("tests.dedupesecond.models")
    _source_model(target_module, "DedupeTarget", "dedupetarget", runtime=True)
    _source_model(
        first_module,
        "DedupeFirstDonor",
        "dedupefirst",
        extends="dedupetarget.DedupeTarget",
        after_resource_load=classmethod(shared_hook),
    )
    _source_model(
        second_module,
        "DedupeSecondDonor",
        "dedupesecond",
        extends="dedupetarget.DedupeTarget",
        after_resource_load=classmethod(shared_hook),
    )

    runtime = Runtime(
        (
            _addon_config("dedupetarget", target_module),
            _addon_config("dedupefirst", first_module),
            _addon_config("dedupesecond", second_module),
        ),
        runtime_dir=tmp_path / "runtime",
    )

    source = runtime.render_sources()[Path("dedupetarget/models.py")]

    assert "def after_resource_load(" not in source


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
    migration_path = runtime.runtime_dir / "resources" / "migrations" / "0001_initial.py"
    migration_path.write_text("# migration\n", encoding="utf-8")

    runtime.clean()
    assert "ANGEE GENERATED RUNTIME" in (runtime.runtime_dir / "__init__.py").read_text(encoding="utf-8")
    runtime.emit()

    assert "ANGEE GENERATED RUNTIME" in (runtime.runtime_dir / "__init__.py").read_text(encoding="utf-8")
    assert migration_path.read_text(encoding="utf-8") == "# migration\n"
    runtime.clean()
    assert migration_path.read_text(encoding="utf-8") == "# migration\n"
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
        def emit_if_stale(self) -> bool:
            calls.append("emit_if_stale")
            return True

        def import_generated_models(self) -> None:
            calls.append("import")

    monkeypatch.setattr(runtime_module.Runtime, "from_django", classmethod(lambda cls: FakeRuntime()))

    _compose_config().import_models()

    assert calls == ["emit_if_stale", "import"]


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


def test_build_emit_does_not_recheck_after_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The build command leaves write integrity to the emit path."""

    calls: list[str] = []

    class FakeRuntime:
        def is_current(self) -> bool:
            calls.append("current")
            return False

        def emit(self) -> None:
            calls.append("emit")

        def check(self) -> None:
            calls.append("check")

    monkeypatch.setattr(runtime_module.Runtime, "from_django", classmethod(lambda cls: FakeRuntime()))

    Command()._handle_build({"check": False})

    assert calls == ["current", "emit"]


def test_appgraph_annotates_roots_and_dependencies() -> None:
    """resolve() tags declared roots and normalizes each app's dependencies.

    The platform console reads these annotations instead of re-deriving the
    composed graph (``addons/angee/platform/schema.py``).
    """

    graph = AppGraph()
    configs = {config.name: config for config in graph.resolve(["angee.iam"])}

    iam = configs["angee.iam"]
    assert iam.angee_addon_root is True
    assert iam.angee_depends_on == graph.app_dependencies(iam)
    assert "angee.resources" in iam.angee_depends_on

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


def test_appgraph_rejects_duplicate_dependencies(stub_contracts: None) -> None:
    """Repeated dependencies are rejected at their declaring owner."""

    config = AppConfig("tests.duplicate_dependency", sys.modules[__name__])
    config._addon_contract = make_contract(depends_on=("angee.base", "angee.base"))

    with pytest.raises(ImproperlyConfigured, match="duplicate dependency 'angee.base'"):
        AppGraph().resolve([config])
