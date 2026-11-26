"""An initial messaging migration authored while channels held their own form still loads.

``Channel.form_schema`` named its default by dotted path, so every initial
messaging migration written while the column existed imports
``angee.messaging.webforms`` and names ``default_webform_schema``, at every
``migrate``. The column is gone; that name stays for those files.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from django.db import models
from django.db.migrations.writer import MigrationWriter

OLD_INITIAL_MIGRATION = """\
import angee.messaging.webforms
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    operations = [
        migrations.CreateModel(
            name='Channel',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('form_schema', models.JSONField(blank=True, default=angee.messaging.webforms.default_webform_schema)),
            ],
        ),
    ]
"""


def test_an_initial_migration_naming_the_old_form_schema_default_still_imports_and_builds_it(tmp_path: Path) -> None:
    """Loaded as Django loads a migration module, the old field builds, and its default still serializes the same."""

    path = tmp_path / "0001_initial.py"
    path.write_text(OLD_INITIAL_MIGRATION)
    spec = importlib.util.spec_from_file_location("retained_messaging_0001_initial", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    (create,) = module.Migration.operations
    field = dict(create.fields)["form_schema"]

    assert isinstance(field, models.JSONField)
    assert field.get_default() == {"type": "object", "properties": {}}
    assert MigrationWriter.serialize(field) == (
        "models.JSONField(blank=True, default=angee.messaging.webforms.default_webform_schema)",
        {"import angee.messaging.webforms", "from django.db import models"},
    )
