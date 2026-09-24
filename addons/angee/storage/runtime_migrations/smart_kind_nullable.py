"""Replace the empty smart-folder kind with NULL, retaining folder identities."""

from django.db import migrations, models
from django.db.migrations.state import ProjectState

from angee.base.fields import StateField


def applies(project_state: ProjectState) -> bool:
    model = project_state.models.get(("storage", "folder"))
    return model is not None and "smart_kind" in model.fields and (
        not model.fields["smart_kind"].null
        or any(
            operation.constraint not in model.options.get("constraints", [])
            for operation in Migration.operations if isinstance(operation, migrations.AddConstraint)
        )
    )


def forwards(apps, schema_editor):
    rows = apps.get_model("storage", "Folder")._base_manager.using(schema_editor.connection.alias)
    rows.filter(smart_kind=models.Value("", output_field=models.CharField())).update(smart_kind=None)


def backwards(apps, schema_editor):
    rows = apps.get_model("storage", "Folder")._base_manager.using(schema_editor.connection.alias)
    rows.filter(smart_kind__isnull=True).update(smart_kind=models.Value("", output_field=models.CharField()))


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.RemoveConstraint("folder", "uniq_storage_folder_owner_smart_kind"),
        migrations.AlterField(
            "folder", "smart_kind",
            StateField(choices=[("trash", "Trash")], null=True, blank=True, editable=False),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.AddConstraint(
            "folder",
            models.UniqueConstraint(
                fields=("owner", "smart_kind"),
                condition=models.Q(is_virtual=True, smart_kind__isnull=False),
                name="uniq_storage_folder_owner_smart_kind",
            ),
        ),
    ]
