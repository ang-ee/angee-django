"""A workflow gains a transferable ``owner``, starting from its creator.

Access that followed ``created_by`` follows ``owner`` now, so each existing workflow
takes its creator as owner and one installed without a creator stays ownerless. The
backfill only fills an empty owner; reverse leaves the column to its removal.
"""

from django.conf import settings
from django.db import migrations, models
from django.db.migrations.state import ProjectState

from angee.models.ownership import retained_set_null


def applies(project_state: ProjectState) -> bool:
    workflow = project_state.models.get(("workflows", "workflow"))
    return workflow is not None and "owner" not in workflow.fields


def forwards(apps, schema_editor):
    rows = apps.get_model("workflows", "Workflow")._base_manager.using(schema_editor.connection.alias).order_by()
    rows.filter(owner__isnull=True).update(owner_id=models.F("created_by_id"))


class Migration(migrations.Migration):
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField(
            "workflow",
            "owner",
            models.ForeignKey(
                settings.AUTH_USER_MODEL,
                null=True,
                blank=True,
                editable=False,
                on_delete=retained_set_null,
                related_name="+",
            ),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
