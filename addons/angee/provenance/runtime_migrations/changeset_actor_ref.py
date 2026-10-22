"""Name stored attribution without shadowing REBAC's native ``actor()`` method.

The explicit column is established before renaming the Python field so Django
keeps the populated ``actor`` column throughout the state transition.
"""

from django.db import migrations, models
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    changeset = project_state.models.get(("provenance", "changeset"))
    return changeset is not None and "actor" in changeset.fields and "actor_ref" not in changeset.fields


class Migration(migrations.Migration):
    operations = [
        migrations.AlterField(
            "changeset", "actor", models.TextField(db_column="actor", blank=True, editable=False),
        ),
        migrations.RenameField("changeset", "actor", "actor_ref"),
    ]
