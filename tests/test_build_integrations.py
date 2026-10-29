"""Composition boundaries: native models, feature collisions and emitted path binding."""

import sys
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.test.utils import isolate_apps

from angee.compose.declarations import BuildIntegration, ModelDeclaration
from angee.compose.model_composition import ModelComposition
from angee.compose.runtime import Runtime


class Contribution(BuildIntegration):
    """Supply one explicit declaration and source map to exercise the boundary."""

    def __init__(self, *, declaration=None, sources=None, imports=()):
        self.declaration = declaration or ModelDeclaration()
        self.sources = sources or {}
        self.imports = imports

    def model_declaration(self, composition, source):
        return self.declaration

    def render_sources(self, runtime):
        return self.sources

    def runtime_imports(self, runtime):
        return tuple(f"{runtime.runtime_module}.{name}" for name in self.imports)


@pytest.fixture
def native_source(monkeypatch, settings):
    settings.MIGRATION_MODULES = {**settings.MIGRATION_MODULES, "build_contract": "build_contract.migrations"}
    with isolate_apps():
        class Record(models.Model):
            runtime = True
            title = models.CharField(max_length=32)
            objects = models.Manager()

            class Meta:
                abstract = True
                app_label = "build_contract"

        monkeypatch.setattr(sys.modules[__name__], "Record", Record, raising=False)
        yield Record


def runtime_for(tmp_path, native_source, *integrations):
    composition = ModelComposition({"build_contract": (native_source,)}, {})
    return Runtime((), composition, runtime_dir=tmp_path / "runtime", integrations=integrations)


def test_plain_composition_adds_no_feature_models_managers_or_outputs(tmp_path, native_source):
    sources = runtime_for(tmp_path, native_source).render_sources()
    assert set(sources) == {
        Path("__init__.py"), Path("build_contract/__init__.py"),
        Path("build_contract/models.py"),
    }
    model_source = sources[Path("build_contract/models.py")]
    assert "AngeeUnscopedManager" not in model_source
    assert "base_manager_name" not in model_source
    assert "rebac_grantable" not in model_source
    assert "catalogue" not in model_source


@pytest.mark.parametrize("module,existing", [
    ("runtime.build_contract.migrations", False),
    ("runtime.build_contract.migrations", True),
    ("authored.history", False),
    (None, False),
])
def test_build_initializes_only_missing_default_migration_packages(tmp_path, native_source, settings, module, existing):
    settings.MIGRATION_MODULES = {"build_contract": module}
    runtime = runtime_for(tmp_path, native_source)
    settings.COMPOSE_RUNTIME_DIR = runtime.runtime_dir
    initializer = runtime.runtime_dir / "build_contract/migrations/__init__.py"
    authored = b"# Native package initializer.\r\nKEEP = True\r\n"
    if existing:
        initializer.parent.mkdir(parents=True)
        initializer.write_bytes(authored)

    sources = runtime.render_sources()
    assert initializer.exists() is existing  # Rendering remains read-only.
    assert (Path("build_contract/migrations/__init__.py") in sources) is (
        module == "runtime.build_contract.migrations" and not existing
    )
    runtime.emit_sources()
    assert initializer.exists() is (existing or module == "runtime.build_contract.migrations")
    if initializer.exists():
        assert initializer.read_bytes() == (authored if existing else b"")
        assert list(initializer.parent.iterdir()) == [initializer]
    assert runtime.emit_sources() is False


@pytest.mark.parametrize("name", ["_meta", "title", "objects", "save", "Meta", "extends"])
def test_build_declarations_cannot_replace_native_behavior(tmp_path, native_source, name):
    contribution = Contribution(declaration=ModelDeclaration(attributes={name: "replacement"}))
    with pytest.raises(ImproperlyConfigured, match="cannot declare"):
        runtime_for(tmp_path, native_source, contribution)


def test_build_declarations_refuse_competing_feature_owners(tmp_path, native_source):
    contribution = ModelDeclaration(attributes={"feature_mode": "enabled"})
    with pytest.raises(ImproperlyConfigured, match="competing build declarations"):
        runtime_for(
            tmp_path, native_source, Contribution(declaration=contribution), Contribution(declaration=contribution),
        )


@pytest.mark.parametrize("path", ["../outside.py", "/outside.py", "build_contract", "build_contract/models.py"])
def test_artifact_collisions_and_escaping_paths_fail_before_writing(tmp_path, native_source, path):
    runtime = runtime_for(tmp_path, native_source, Contribution(sources={Path(path): "content"}))
    with pytest.raises(ImproperlyConfigured):
        runtime.emit_sources()
    assert not runtime.runtime_dir.exists()


def test_emitted_integration_module_is_imported_without_a_build_callback(tmp_path, settings, monkeypatch):
    contribution = Contribution(sources={Path("feature.py"): "READY = True\n"}, imports=("feature",))
    directory = tmp_path / "integration_runtime"
    settings.COMPOSE_RUNTIME_DIR = directory
    monkeypatch.syspath_prepend(str(tmp_path))
    runtime = Runtime(
        (), ModelComposition({}, {}), runtime_dir=directory,
        runtime_module="integration_runtime", integrations=(contribution,),
    )
    assert runtime.emit_sources() is True
    assert runtime.emit_sources() is False
    runtime.import_generated_models()
    assert sys.modules["integration_runtime.feature"].READY is True
    assert runtime.integration_modules() == ("integration_runtime.feature",)


@pytest.mark.parametrize("operation", ["render_sources", "emit_sources"])
def test_native_model_artifacts_cannot_escape_through_directory_symlinks(
    tmp_path, native_source, operation,
):
    """Native model outputs obey the same containment contract as contributions."""

    runtime = runtime_for(tmp_path, native_source)
    runtime.runtime_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    foreign = outside / "models.py"
    foreign.write_text("foreign", encoding="utf-8")
    (runtime.runtime_dir / "build_contract").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ImproperlyConfigured, match="escapes the root through a symlink"):
        getattr(runtime, operation)()
    assert foreign.read_text(encoding="utf-8") == "foreign"
    assert not (runtime.runtime_dir / "__init__.py").exists()
