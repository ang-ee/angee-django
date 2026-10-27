"""Retained migration modules must load before an upgrade can run."""

from importlib import import_module

from django.db.migrations.loader import MigrationLoader
from django.db.models import GeneratedField


def test_loader_imports_retained_integration_field_contract(tmp_path, monkeypatch, settings):
    package = tmp_path / "retained_migrations"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "0001_initial.py").write_text(
        "from django.db import migrations\n"
        "from angee.integrate.fields import DiscrepancyOpenField\n"
        "class Migration(migrations.Migration):\n"
        "    initial = True\n"
        "    dependencies = []\n"
        "    operations = []\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    settings.MIGRATION_MODULES = {**settings.MIGRATION_MODULES, "resources": "retained_migrations"}

    # Django imports even applied migration modules before it can plan new work.
    loader = MigrationLoader(None, load=False)
    loader.load_disk()

    assert ("resources", "0001_initial") in loader.disk_migrations
    module = import_module("retained_migrations.0001_initial")
    assert module.DiscrepancyOpenField is GeneratedField
