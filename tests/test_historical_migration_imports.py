"""Retained migration modules must load before an upgrade can run."""

from importlib import import_module

from django.db.migrations.loader import MigrationLoader
from django.db.models import GeneratedField

from angee.fields import SqidField
from angee.impl import ImplClassField


def test_loader_imports_retained_field_and_relationship_contracts(tmp_path, monkeypatch, settings):
    package = tmp_path / "retained_migrations"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "0001_initial.py").write_text(
        "from django.db import migrations\n"
        "from angee.base.fields import SqidField, ImplClassField\n"
        "from angee.integrate.fields import DiscrepancyOpenField\n"
        "from angee.base.historical_relationships import (\n"
        "    ensure_historical_relationships, delete_historical_relationships,\n"
        "    retarget_historical_resource,\n"
        ")\n"
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
    assert module.SqidField is SqidField
    assert module.ImplClassField is ImplClassField
    assert module.DiscrepancyOpenField is GeneratedField
