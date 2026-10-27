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

    def __init__(self, *, declaration=None, sources=None):
        self.declaration = declaration or ModelDeclaration()
        self.sources = sources or {}
        self.bound = []

    def model_declaration(self, composition, source):
        return self.declaration

    def render_sources(self, runtime):
        return self.sources

    def bind(self, runtime, sources):
        self.bound.append(sources)


@pytest.fixture
def native_source(monkeypatch):
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
        Path("build_contract/migrations/__init__.py"), Path("build_contract/models.py"),
    }
    model_source = sources[Path("build_contract/models.py")]
    assert "AngeeUnscopedManager" not in model_source
    assert "base_manager_name" not in model_source
    assert "rebac_grantable" not in model_source
    assert "catalogue" not in model_source


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
        runtime.emit_if_stale()
    assert not runtime.runtime_dir.exists()


def test_binding_receives_the_emitted_map_even_when_sources_are_unchanged(tmp_path, settings):
    contribution = Contribution(sources={Path("feature.txt"): "value"})
    directory = tmp_path / "runtime"
    settings.ANGEE_RUNTIME_DIR = directory
    runtime = Runtime((), ModelComposition({}, {}), runtime_dir=directory, integrations=(contribution,))
    assert runtime.emit_if_stale() is True
    assert runtime.emit_if_stale() is False
    assert len(contribution.bound) == 2
    for sources in contribution.bound:
        assert sources[Path("feature.txt")] == (directory / "feature.txt").read_text()
