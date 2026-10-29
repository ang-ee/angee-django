"""Retire old file-index caches only when canonical Files retain their content."""

from django.db import migrations, models, router


def applies(state):
    model = state.models.get(("storage_integrate", "mount"))
    if model is None or "cursor" not in model.fields:
        return False
    if not isinstance(model.fields["cursor"], models.JSONField):
        raise ValueError("Unexpected legacy storage cursor field.")
    return True


def forwards(apps, schema_editor):
    mount = apps.get_model("storage_integrate", "Mount")
    file = apps.get_model("storage", "File")
    alias = schema_editor.connection.alias
    if not router.allow_migrate_model(alias, mount):
        return
    for row in mount._base_manager.using(alias).exclude(cursor={}).iterator():
        cursor = row.cursor
        if not isinstance(cursor, dict) or set(cursor) != {"index"} or not isinstance(cursor["index"], dict):
            raise ValueError(f"Mount {row.pk} has unsupported retained cursor state.")
        for path, entry in cursor["index"].items():
            if (
                not isinstance(path, str) or not path
                or not isinstance(entry, dict)
                or not {"size", "sha256"}.issubset(entry)
                or set(entry) - {"size", "mtime", "sha256"}
                or type(entry["size"]) is not int or entry["size"] < 0
                or not isinstance(entry["sha256"], str) or len(entry["sha256"]) != 64
            ):
                raise ValueError(f"Mount {row.pk} has an unsupported file-index entry.")
            if not file._base_manager.using(alias).filter(
                drive_id=row.drive_id, content_hash=entry["sha256"], size_bytes=entry["size"],
            ).exists():
                raise ValueError(f"Mount {row.pk} has cached content not retained by Files in its drive.")


class Migration(migrations.Migration):
    dependencies = [("storage_integrate", "__latest__"), ("storage", "__latest__")]
    operations = [
        migrations.RunPython(forwards),
        migrations.RemoveField(model_name="mount", name="cursor"),
    ]
