"""Rename retained extraction evidence and separate domain profiles from transport.

Consumer domain keys keep their spelling and must be registered as profiles.
The built-in ``inference`` engine only selected transport, so it becomes ``none``
(no domain profile), unless a consumer explicitly registers an ``inference``
domain profile. Rollback restores that built-in key. Configuration and page
metadata are renamed in place without reinterpreting their retained payloads.
The new built-in key ``none`` is reserved: an old consumer using that key must
declare its own key migration first, otherwise rollback would conflate it with
the old transport-only engine. Reject that ambiguity before changing any column.
"""

from django.db import migrations, models
from django.db.migrations.state import ProjectState

from angee.base.impl import ImplClassField


def applies(project_state: ProjectState) -> bool:
    model = project_state.models.get(("workflows_extraction", "extraction"))
    return model is not None and "engine" in model.fields


def validate_legacy_keys(apps, schema_editor):
    rows = apps.get_model("workflows_extraction", "Extraction")._base_manager.using(schema_editor.connection.alias)
    if rows.filter(engine=models.Value("none", output_field=models.CharField())).exists():
        raise ValueError(
            "Extraction engine key 'none' conflicts with the new built-in profile key. "
            "Declare a consumer key migration before the extraction profile transition."
        )


def forwards(apps, schema_editor):
    model = apps.get_model("workflows_extraction", "Extraction")
    if "inference" in model._meta.get_field("profile").registered_keys():
        return  # An explicitly carried-forward domain profile retains its key.
    rows = model._base_manager.using(schema_editor.connection.alias)
    # Literal expressions preserve historical keys even after their registry retires.
    old = models.Value("inference", output_field=models.CharField())
    new = models.Value("none", output_field=models.CharField())
    rows.filter(profile=old).update(profile=new)


def backwards(apps, schema_editor):
    rows = apps.get_model("workflows_extraction", "Extraction")._base_manager.using(schema_editor.connection.alias)
    rows.filter(profile=models.Value("none", output_field=models.CharField())).update(
        profile=models.Value("inference", output_field=models.CharField()),
    )


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.RunPython(validate_legacy_keys, migrations.RunPython.noop),
        migrations.RenameField("extraction", "engine", "profile"),
        migrations.RenameField("extraction", "engine_config", "profile_config"),
        migrations.RenameField("extractionpage", "engine_metadata", "provider_metadata"),
        migrations.AlterField(
            "extraction", "profile",
            ImplClassField(registry_setting="ANGEE_EXTRACTION_PROFILE_CLASSES", editable=False),
        ),
        migrations.RunPython(forwards, backwards),
    ]
