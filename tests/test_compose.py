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
from django.db import OperationalError, models

import angee.compose as compose_package
import angee.compose.runtime as runtime_module
from angee.base.emission import ModelClassAttribute, ModelDecorator
from angee.base.fields import StateField
from angee.base.mixins import HistoryMixin, RevisionMixin
from angee.base.models import AngeeManager, AngeeModel, role_anchor
from angee.base.transitions import StateTransitions, save_state, transition
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

    source = Runtime((), runtime_dir=tmp_path / "runtime")._models_source("tests", (probe,))

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
            Runtime((), runtime_dir=tmp_path / "runtime")._models_source("tests", (stray,))
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


def test_runtime_walks_declared_emission_seams_for_history_and_revision(tmp_path: Path) -> None:
    """History and revision mixins both ride declared emission seams."""

    module = ModuleType("tests.declared_emission.models")
    DeclaredEmissionThing = type(
        "DeclaredEmissionThing",
        (HistoryMixin, RevisionMixin, AngeeModel),
        {
            "__module__": module.__name__,
            "runtime": True,
            "revisioned_fields": ("body",),
            "body": models.TextField(),
            "Meta": type("Meta", (), {"abstract": True, "app_label": "declared"}),
        },
    )
    module.DeclaredEmissionThing = DeclaredEmissionThing
    app_config = SimpleNamespace(
        label="declared",
        name="tests.declared_emission",
        module=ModuleType("tests.declared_emission"),
        models_module=module,
    )

    source = Runtime((app_config,), runtime_dir=tmp_path / "runtime").render_sources()[Path("declared/models.py")]

    assert (
        source.index("@reversion.register")
        < source.index("class DeclaredEmissionThing")
        < source.index("history = simple_history")
        < source.index("class Meta(_DeclaredEmissionThingMeta)")
    )


def test_runtime_walks_synthetic_declared_emission_seams(tmp_path: Path) -> None:
    """Unknown mixins emit through the same declared attribute/decorator seams."""

    class SyntheticEmissionMixin(models.Model):
        angee_model_decorators = (
            ModelDecorator(
                import_path="tests.synthetic_emission_decorators.marker_decorator",
                args=("decorated",),
            ),
        )

        @classmethod
        def angee_model_attributes(
            cls,
            *,
            app_label: str,
            model_class: type[models.Model],
            extension_bases: tuple[type[models.Model], ...],
        ) -> tuple[ModelClassAttribute, ...]:
            return (
                ModelClassAttribute(
                    name="synthetic_marker",
                    import_path="tests.synthetic_emission_attributes.MarkerAttribute",
                    args=(model_class.__name__,),
                    kwargs=(("app_label", app_label), ("extension_count", len(extension_bases))),
                ),
            )

        class Meta:
            abstract = True
            app_label = "tests"

    module = ModuleType("tests.synthetic_emission.models")
    SyntheticEmissionThing = type(
        "SyntheticEmissionThing",
        (SyntheticEmissionMixin, AngeeModel),
        {
            "__module__": module.__name__,
            "runtime": True,
            "Meta": type("Meta", (), {"abstract": True, "app_label": "synthetic"}),
        },
    )
    module.SyntheticEmissionThing = SyntheticEmissionThing
    app_config = SimpleNamespace(
        label="synthetic",
        name="tests.synthetic_emission",
        module=ModuleType("tests.synthetic_emission"),
        models_module=module,
    )

    source = Runtime((app_config,), runtime_dir=tmp_path / "runtime").render_sources()[Path("synthetic/models.py")]

    attribute_import = "import tests.synthetic_emission_attributes"
    decorator_import = "import tests.synthetic_emission_decorators"
    decorator = "@tests.synthetic_emission_decorators.marker_decorator('decorated')"
    class_header = "class SyntheticEmissionThing(AbstractSyntheticEmissionThing):"
    attribute = (
        "synthetic_marker = tests.synthetic_emission_attributes.MarkerAttribute("
        "'SyntheticEmissionThing', app_label='synthetic', extension_count=0)"
    )

    assert attribute_import in source
    assert decorator_import in source
    assert source.index(decorator_import) < source.index(decorator) < source.index(class_header)
    assert (
        source.index(attribute_import)
        < source.index(attribute)
        < source.index("class Meta(_SyntheticEmissionThingMeta)")
    )


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
        (AngeeModel,),
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

    source = Runtime((app_config,), runtime_dir=tmp_path / "runtime").render_sources()[Path("catalogue/models.py")]
    parent_body = source[source.index("class CatalogueThing") : source.index("class CatalogueChild")]
    child_body = source[source.index("class CatalogueChild") :]

    assert "catalogue = True" in parent_body
    assert 'catalogue_tier = "install"' in parent_body
    assert "catalogue = True" not in child_body
    assert "catalogue_tier" not in child_body


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


class _ProbeParentManager(AngeeManager):  # type: ignore[misc]
    """Distinct manager standing in for a materialized parent's default manager."""


class _ProbeMixinManager(AngeeManager):  # type: ignore[misc]
    """Distinct manager a source-side mixin would inject under a child-first flip."""


def test_runtime_child_override_flips_base_order(tmp_path: Path) -> None:
    """``child_overrides_parent`` emits the abstract source before the concrete parent (F-e)."""

    class OverrideChild(AngeeModel):
        runtime = True
        extends = "tests.DecoratedRevisionThing"
        child_overrides_parent = True
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
            OverrideChild=OverrideChild,
        ),
    )

    source = Runtime((app_config,), runtime_dir=tmp_path / "runtime").render_sources()[Path("tests/models.py")]

    assert "class DecoratedRevisionThing(AbstractDecoratedRevisionThing):" in source
    # Flipped: source before parent (vs the parent-first status quo).
    assert "class OverrideChild(AbstractOverrideChild, DecoratedRevisionThing):" in source
    assert "class OverrideChild(DecoratedRevisionThing, AbstractOverrideChild):" not in source
    # The flip re-declares the parent-shared framework fields as None so the child
    # inherits the parent's columns instead of duplicating them.
    child_body = source[source.index("class OverrideChild") :]
    assert "created_at = None" in child_body
    assert "updated_at = None" in child_body


def test_runtime_parties_children_stay_parent_first(tmp_path: Path) -> None:
    """Guard (a): parties children never opt in — parent-first order is byte-preserved (F-e)."""

    runtime = Runtime(
        tuple(apps.get_app_config(label) for label in ("resources", "iam", "integrate", "storage", "parties")),
        runtime_dir=tmp_path / "runtime",
    )

    source = runtime.render_sources()[Path("parties/models.py")]

    assert "class Person(Party, AbstractPerson):" in source
    assert "class Organization(Party, AbstractOrganization):" in source
    assert "AbstractPerson, Party" not in source
    assert "AbstractOrganization, Party" not in source
    # No opt-in → no field-removal shadows anywhere in the parties runtime.
    assert "created_at = None" not in source


def test_runtime_child_override_rejects_silent_manager_swap(tmp_path: Path) -> None:
    """Guard (b): a flip that would swap the default manager without an explicit one fails (F-e)."""

    class ParentWithManager(AngeeModel):
        runtime = True
        objects = _ProbeParentManager()

        class Meta:
            abstract = True
            app_label = "tests"

    class ManagerMixin(AngeeModel):
        objects = _ProbeMixinManager()

        class Meta:
            abstract = True
            app_label = "tests"

    class SwapChild(ManagerMixin):
        runtime = True
        extends = "tests.ParentWithManager"
        child_overrides_parent = True

        class Meta:
            abstract = True
            app_label = "tests"

    app_config = SimpleNamespace(
        label="tests",
        name=__name__,
        module=sys.modules[__name__],
        models_module=SimpleNamespace(ParentWithManager=ParentWithManager, SwapChild=SwapChild),
    )

    with pytest.raises(ImproperlyConfigured, match="default manager"):
        Runtime((app_config,), runtime_dir=tmp_path / "runtime")


def test_runtime_child_override_allows_explicit_own_manager(tmp_path: Path) -> None:
    """Guard (b): the same swap is allowed when the child declares its own manager (F-e)."""

    class ParentWithManager(AngeeModel):
        runtime = True
        objects = _ProbeParentManager()

        class Meta:
            abstract = True
            app_label = "tests"

    class ExplicitChild(AngeeModel):
        runtime = True
        extends = "tests.ParentWithManager"
        child_overrides_parent = True
        objects = _ProbeMixinManager()

        class Meta:
            abstract = True
            app_label = "tests"

    app_config = SimpleNamespace(
        label="tests",
        name=__name__,
        module=sys.modules[__name__],
        models_module=SimpleNamespace(ParentWithManager=ParentWithManager, ExplicitChild=ExplicitChild),
    )

    source = Runtime((app_config,), runtime_dir=tmp_path / "runtime").render_sources()[Path("tests/models.py")]

    assert "class ExplicitChild(AbstractExplicitChild, ParentWithManager):" in source


def test_runtime_child_override_rejects_non_child_optin(tmp_path: Path) -> None:
    """The opt-in is meaningless off a materialized child, so the composer rejects it (F-e)."""

    class NotAChild(AngeeModel):
        runtime = True
        child_overrides_parent = True

        class Meta:
            abstract = True
            app_label = "tests"

    app_config = SimpleNamespace(
        label="tests",
        name=__name__,
        module=sys.modules[__name__],
        models_module=SimpleNamespace(NotAChild=NotAChild),
    )

    with pytest.raises(ImproperlyConfigured, match="not a materialized child"):
        Runtime((app_config,), runtime_dir=tmp_path / "runtime")


def test_runtime_child_override_revalidates_transition_metadata(tmp_path: Path) -> None:
    """Guard (c): an opting child's inherited transition metadata re-validates on the flip (F-e)."""

    class Lifecycle(models.TextChoices):
        DRAFT = "draft", "Draft"
        DONE = "done", "Done"

    class TxnParent(AngeeModel):
        runtime = True
        status = StateField(choices_enum=Lifecycle, default=Lifecycle.DRAFT)
        status_transitions = StateTransitions(status, {Lifecycle.DRAFT: [Lifecycle.DONE]})

        class Meta:
            abstract = True
            app_label = "tests"

        @transition(status, source=Lifecycle.DRAFT, target=Lifecycle.DONE, on_success=save_state)
        def finish(self) -> None:
            """Move draft to done."""

    class TxnChild(AngeeModel):
        runtime = True
        extends = "tests.TxnParent"
        child_overrides_parent = True
        note = models.CharField(max_length=16, blank=True, default="")

        class Meta:
            abstract = True
            app_label = "tests"

    app_config = SimpleNamespace(
        label="tests",
        name=__name__,
        module=sys.modules[__name__],
        models_module=SimpleNamespace(TxnParent=TxnParent, TxnChild=TxnChild),
    )

    source = Runtime((app_config,), runtime_dir=tmp_path / "runtime").render_sources()[Path("tests/models.py")]

    assert "class TxnChild(AbstractTxnChild, TxnParent):" in source


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


def test_runtime_child_aggregates_parent_donor_and_own_hook(tmp_path: Path) -> None:
    """A materialized child runs a parent-side donor's hook and its own, each once.

    Regression for the parent modelled via its abstract source alone: the parent's
    hook lives on a donor, not the source, so the old code dropped it and the child
    silently lost either the parent's hook or its own. The fix models the parent as
    its whole composed set (the concrete parent's own ``after_resource_load``), so
    the child aggregates the parent (which runs the donor's hook) then its own.
    """

    def parent_donor_hook(cls: type, instances: object, **kwargs: object) -> None:
        del cls, instances, kwargs

    def child_hook(cls: type, instances: object, **kwargs: object) -> None:
        del cls, instances, kwargs

    parent_module = ModuleType("tests.hookparent.models")
    donor_module = ModuleType("tests.hookdonor.models")
    child_module = ModuleType("tests.hookchild.models")
    _source_model(parent_module, "HookParent", "hookparent", runtime=True)
    _source_model(
        donor_module,
        "HookParentDonor",
        "hookdonor",
        extends="hookparent.HookParent",
        after_resource_load=classmethod(parent_donor_hook),
    )
    _source_model(
        child_module,
        "HookChild",
        "hookchild",
        runtime=True,
        extends="hookparent.HookParent",
        after_resource_load=classmethod(child_hook),
    )

    runtime = Runtime(
        (
            _addon_config("hookparent", parent_module),
            _addon_config("hookdonor", donor_module),
            _addon_config("hookchild", child_module),
        ),
        runtime_dir=tmp_path / "runtime",
    )
    sources = runtime.render_sources()

    # The parent runs its single donor natively — no parent aggregator emitted.
    assert "def after_resource_load(" not in sources[Path("hookparent/models.py")]
    # The child aggregates the whole parent (its donor's hook) then its own, in order.
    child_source = sources[Path("hookchild/models.py")]
    assert "def after_resource_load(cls, *args: object, **kwargs: object) -> None:" in child_source
    parent_call = "HookParent.after_resource_load.__func__(cls, *args, **kwargs)"
    child_call = "AbstractHookChild.after_resource_load.__func__(cls, *args, **kwargs)"
    assert child_source.index(parent_call) < child_source.index(child_call)


def test_runtime_child_dedupes_hook_shared_with_a_parent_donor(tmp_path: Path) -> None:
    """A hook shared by a parent donor and a child donor runs once — via the parent.

    The child dedups its own contributors against the parent's *whole* composed set,
    so a function the parent already runs (through its donor) is not called again by
    the child's donor. The child aggregator calls the parent and the child's own
    hook, but never the child donor's copy of the shared function.
    """

    def shared_hook(cls: type, instances: object, **kwargs: object) -> None:
        del cls, instances, kwargs

    def child_hook(cls: type, instances: object, **kwargs: object) -> None:
        del cls, instances, kwargs

    parent_module = ModuleType("tests.sharedparent.models")
    parent_donor_module = ModuleType("tests.sharedpdonor.models")
    child_module = ModuleType("tests.sharedchild.models")
    child_donor_module = ModuleType("tests.sharedcdonor.models")
    _source_model(parent_module, "SharedParent", "sharedparent", runtime=True)
    _source_model(
        parent_donor_module,
        "SharedParentDonor",
        "sharedpdonor",
        extends="sharedparent.SharedParent",
        after_resource_load=classmethod(shared_hook),
    )
    _source_model(
        child_module,
        "SharedChild",
        "sharedchild",
        runtime=True,
        extends="sharedparent.SharedParent",
        after_resource_load=classmethod(child_hook),
    )
    _source_model(
        child_donor_module,
        "SharedChildDonor",
        "sharedcdonor",
        extends="sharedchild.SharedChild",
        after_resource_load=classmethod(shared_hook),  # the SAME function the parent donor runs
    )

    runtime = Runtime(
        (
            _addon_config("sharedparent", parent_module),
            _addon_config("sharedpdonor", parent_donor_module),
            _addon_config("sharedchild", child_module),
            _addon_config("sharedcdonor", child_donor_module),
        ),
        runtime_dir=tmp_path / "runtime",
    )
    child_source = runtime.render_sources()[Path("sharedchild/models.py")]

    assert "def after_resource_load(cls, *args: object, **kwargs: object) -> None:" in child_source
    assert "SharedParent.after_resource_load.__func__(cls, *args, **kwargs)" in child_source
    assert "AbstractSharedChild.after_resource_load.__func__(cls, *args, **kwargs)" in child_source
    # The child donor's own copy of the shared hook is NOT called — it deduped
    # against the parent's set, so the shared function runs exactly once.
    assert "SharedChildExtension1.after_resource_load" not in child_source


def test_child_override_removed_fields_rejects_divergent_same_name_field(tmp_path: Path) -> None:
    """A child that redefines an inherited parent-shared field fails the build (finding #2).

    ``_child_override_removed_fields`` shadows a parent-shared inherited field with
    ``None`` so the child inherits the parent's column. That is only sound when the
    two are the *same* field; a deliberate same-name override with a different
    definition would vanish behind the shadow, so the composer rejects it instead of
    silently dropping the override.
    """

    narrow = type(
        "NarrowLabel",
        (AngeeModel,),
        {
            "__module__": "tests.divparent.models",
            "label": models.CharField(max_length=10),
            "Meta": type("Meta", (), {"abstract": True, "app_label": "divparent"}),
        },
    )
    wide = type(
        "WideLabel",
        (AngeeModel,),
        {
            "__module__": "tests.divchild.models",
            "label": models.CharField(max_length=99),
            "Meta": type("Meta", (), {"abstract": True, "app_label": "divchild"}),
        },
    )
    parent_module = ModuleType("tests.divparent.models")
    child_module = ModuleType("tests.divchild.models")
    DivParent = type(
        "DivParent",
        (narrow, AngeeModel),
        {
            "__module__": parent_module.__name__,
            "runtime": True,
            "Meta": type("Meta", (), {"abstract": True, "app_label": "divparent"}),
        },
    )
    # A materialized child that inherits a *different* ``label`` (max_length 99) than
    # the parent's (max_length 10) — the deliberate same-name override.
    DivChild = type(
        "DivChild",
        (wide, AngeeModel),
        {
            "__module__": child_module.__name__,
            "runtime": True,
            "extends": "divparent.DivParent",
            "Meta": type("Meta", (), {"abstract": True, "app_label": "divchild"}),
        },
    )
    parent_module.DivParent = DivParent
    child_module.DivChild = DivChild

    runtime = Runtime(
        (
            _addon_config("divparent", parent_module),
            _addon_config("divchild", child_module),
        ),
        runtime_dir=tmp_path / "runtime",
    )

    with pytest.raises(ImproperlyConfigured, match="different column of the same name"):
        runtime._child_override_removed_fields(cast(type[AngeeModel], DivChild))


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
