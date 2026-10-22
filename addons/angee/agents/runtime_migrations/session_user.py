"""A session's ``owner`` column becomes ``user``: a session is its user's own conversation.

The rename keeps every session's user in place, then the field takes its current
definition (nullable, not editable, no reverse accessor). Django sees a removed
``owner`` and an added ``user`` because the definition changed with the name, so
without this cutover ``makemigrations`` would drop the populated column.
"""

from django.conf import settings
from django.db import migrations, models
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    session = project_state.models.get(("agents", "agentsession"))
    return session is not None and "owner" in session.fields and "user" not in session.fields


class Migration(migrations.Migration):
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.RenameField("agentsession", "owner", "user"),
        migrations.AlterField(
            "agentsession",
            "user",
            models.ForeignKey(
                settings.AUTH_USER_MODEL,
                null=True,
                blank=True,
                editable=False,
                on_delete=models.CASCADE,
                related_name="+",
            ),
        ),
    ]
