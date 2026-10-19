"""Parties, folders and circles gain a transferable ``owner``, starting from their creator.

Access that followed ``created_by`` follows ``owner`` now, so each existing row takes
its creator as owner and a row without a creator stays ownerless. The backfill only
fills an empty owner; reverse leaves the column to its removal.
"""

from django.conf import settings
from django.db import migrations, models
from django.db.migrations.state import ProjectState

from angee.models.ownership import retained_set_null

MODELS = ("Party", "Folder", "Circle")


def applies(project_state: ProjectState) -> bool:
    roots = [project_state.models.get(("parties", name.lower())) for name in MODELS]
    return all(root is not None and "owner" not in root.fields for root in roots)


def forwards(apps, schema_editor):
    for name in MODELS:
        rows = apps.get_model("parties", name)._base_manager.using(schema_editor.connection.alias).order_by()
        rows.filter(owner__isnull=True).update(owner_id=models.F("created_by_id"))


class Migration(migrations.Migration):
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        *(
            migrations.AddField(
                name.lower(),
                "owner",
                models.ForeignKey(
                    settings.AUTH_USER_MODEL,
                    null=True,
                    blank=True,
                    editable=False,
                    on_delete=retained_set_null,
                    related_name="+",
                ),
            )
            for name in MODELS
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
