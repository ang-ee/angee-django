"""Tests for build-time runtime composition."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import CommandError
from django.db import models

import angee.compose as compose_package
import angee.compose.runtime as runtime_module
from angee.base.mixins import RevisionMixin
from angee.base.models import AngeeModel
from angee.compose.appgraph import AppGraph
from angee.compose.apps import ComposeConfig
from angee.compose.management.commands.angee import Command
from angee.compose.runtime import Runtime


class DecoratedRevisionThing(RevisionMixin, AngeeModel):
    """Abstract model used to test composer-emitted model decorators."""

    runtime = True

    revisioned_fields = ("body",)

    body = models.TextField()

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


def runtime_for(tmp_path: Path) -> Runtime:
    """Return a runtime that emits the installed resource addon."""

    return Runtime(
        (apps.get_app_config("resources"),),
        runtime_dir=tmp_path / "runtime",
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
    """The IAM addon emits a concrete swappable user model."""

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
    assert "swappable = 'AUTH_USER_MODEL'" in user_source


def test_runtime_renders_model_decorators_from_mixins(tmp_path: Path) -> None:
    """Mixin-declared decorators are emitted on concrete runtime models."""

    app_config = SimpleNamespace(
        label="decorated",
        name=__name__,
        module=sys.modules[__name__],
        models_module=sys.modules[__name__],
    )
    runtime = Runtime((app_config,), runtime_dir=tmp_path / "runtime")

    source = runtime.render_sources()[Path("decorated/models.py")]

    assert "import reversion" in source
    assert "@reversion.register(fields=('body',))" in source
    assert source.index("@reversion.register") < source.index("class DecoratedRevisionThing")


def test_runtime_emits_only_models_marked_runtime(tmp_path: Path) -> None:
    """Only abstract source models declaring ``runtime = True`` are emitted."""

    app_config = SimpleNamespace(
        label="selected",
        name=__name__,
        module=sys.modules[__name__],
        models_module=sys.modules[__name__],
    )

    source = Runtime((app_config,), runtime_dir=tmp_path / "runtime").render_sources()[Path("selected/models.py")]

    assert "class DecoratedRevisionThing" in source
    assert "class SkippedRuntimeThing" not in source


def test_runtime_rejects_materialized_extensions(tmp_path: Path) -> None:
    """Extensions use ``extends`` and must not also declare ``runtime = True``."""

    class BadExtension(AngeeModel):
        runtime = True
        extends = "tests.DecoratedRevisionThing"

        class Meta:
            abstract = True
            app_label = "tests"

    app_config = SimpleNamespace(
        label="selected",
        name=__name__,
        module=sys.modules[__name__],
        models_module=SimpleNamespace(BadExtension=BadExtension),
    )

    with pytest.raises(ImproperlyConfigured, match="runtime = True and extends"):
        Runtime((app_config,), runtime_dir=tmp_path / "runtime")


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
