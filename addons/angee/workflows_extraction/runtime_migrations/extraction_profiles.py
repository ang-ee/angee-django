"""Rename retained extraction evidence and separate domain profiles from transport.

Consumer domain keys keep their spelling and must be registered as profiles.
The built-in ``inference`` engine only selected transport, so it becomes ``none``
(no domain profile). The old disabled ``none`` engine carries forward unchanged.
This mapping is frozen; consumers that need to retain an ``inference`` domain
key must declare their own migration. Configuration and page metadata are renamed
in place without reinterpreting their retained payloads. Rollback restores the
column names but keeps ``none``: the old ``inference``/``none`` distinction is
lost, and enabling previously disabled extraction would be unsafe.
"""

from django.db import migrations, models
from django.db.migrations.state import ProjectState

from angee.base.impl import ImplClassField


def applies(project_state: ProjectState) -> bool:
    model = project_state.models.get(("workflows_extraction", "extraction"))
    return model is not None and "engine" in model.fields


def forwards(apps, schema_editor):
    rows = apps.get_model("workflows_extraction", "Extraction")._base_manager.using(schema_editor.connection.alias)
    # Literal expressions preserve historical keys even after their registry retires.
    old = models.Value("inference", output_field=models.CharField())
    new = models.Value("none", output_field=models.CharField())
    rows.filter(profile=old).update(profile=new)


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.RenameField("extraction", "engine", "profile"),
        migrations.RenameField("extraction", "engine_config", "profile_config"),
        migrations.RenameField("extractionpage", "engine_metadata", "provider_metadata"),
        migrations.AlterField(
            "extraction", "profile",
            ImplClassField(registry_setting="ANGEE_EXTRACTION_PROFILE_CLASSES", editable=False),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
