"""A handle's ``owner`` column becomes ``controller``: who sends or syncs as the address.

The rename keeps every handle's controlling user in place, then the field takes its
current definition (reverse accessor ``controlled_handles``). Django sees a removed
``owner`` and an added ``controller`` because the definition changed with the name,
so without this cutover ``makemigrations`` would drop the populated column.
"""

from django.conf import settings
from django.db import migrations, models
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    handle = project_state.models.get(("parties", "handle"))
    return handle is not None and "owner" in handle.fields and "controller" not in handle.fields


class Migration(migrations.Migration):
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.RenameField("handle", "owner", "controller"),
        migrations.AlterField(
            "handle",
            "controller",
            models.ForeignKey(
                settings.AUTH_USER_MODEL,
                null=True,
                blank=True,
                on_delete=models.SET_NULL,
                related_name="controlled_handles",
            ),
        ),
    ]
