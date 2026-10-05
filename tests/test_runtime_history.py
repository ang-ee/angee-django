"""Retained migrations remain loadable without reviving retired capabilities."""

from django.apps.registry import Apps
from django.db.migrations.loader import MigrationLoader

from angee.compose.composer import Composer
from angee.compose.history import HistoricalRuntimeConfig, historical_labels
from angee.compose.model_composition import ModelComposition


def test_retired_dependency_loads_without_stale_models_or_autoconfig(tmp_path, monkeypatch, settings):
    runtime = tmp_path / "cutover_runtime"
    runtime.mkdir()
    (runtime / "__init__.py").write_text("")
    for label in ("retired", "consumer"):
        folder = runtime / label
        (folder / "migrations").mkdir(parents=True)
        (folder / "__init__.py").write_text("")
        (folder / "migrations" / "__init__.py").write_text("")
        (folder / "models.py").write_text('raise AssertionError("retired models imported")\n')
        (folder / "autoconfig.py").write_text('raise AssertionError("retired autoconfig imported")\n')
    (runtime / "retired" / "migrations" / "0001_initial.py").write_text(
        'from django.db import migrations\nclass Migration(migrations.Migration):\n'
        '    dependencies = []\n    operations = []\n'
    )
    (runtime / "consumer" / "migrations" / "0001_initial.py").write_text(
        'from django.db import migrations\nclass Migration(migrations.Migration):\n'
        '    dependencies = [("retired", "0001_initial")]\n    operations = []\n'
    )
    namespace = {"INSTALLED_APPS": [], "ANGEE_RUNTIME_DIR": runtime, "ANGEE_RUNTIME_MODULE": "cutover_runtime"}
    Composer(namespace).compose_settings()
    configs = namespace["INSTALLED_APPS"]
    assert historical_labels(runtime) == ("consumer", "retired")
    assert all(isinstance(config, HistoricalRuntimeConfig) for config in configs)
    registry = Apps(configs)
    assert list(registry.get_models()) == []
    assert ModelComposition.discover(configs).labels == ()

    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr("django.db.migrations.loader.apps", registry)
    settings.MIGRATION_MODULES = {
        label: f"cutover_runtime.{label}.migrations" for label in historical_labels(runtime)
    }
    loader = MigrationLoader(None)
    assert loader.graph.forwards_plan(("consumer", "0001_initial")) == [
        ("retired", "0001_initial"), ("consumer", "0001_initial"),
    ]
