"""Remove obsolete container and link mirrors from both local relationship stores.

Historical shares on other relations remain intact; the live binding and target
rows are never modified. Cleanup needs no prior schema synchronization. Until the
new backed schema is synchronized, access through the retired tuples fails closed.
Reversing this migration does not restore tuples; old code needs its resync command.
"""

from itertools import islice

from django.db import migrations, models, router
from django.db.migrations.state import ProjectState


CONTAINERS = ("storage/drive", "storage/folder", "integrate/integration", "messaging/thread")


def applies(project_state: ProjectState) -> bool:
    return all(
        model in project_state.models
        for model in (
            ("projects", "projectbinding"), ("projects", "link"),
            ("rebac", "relationship"), ("rebac", "relationshipregistry"),
        )
    )


def forwards(apps, schema_editor):
    alias = schema_editor.connection.alias
    for name, resource, subject in (
        ("Relationship", "resource_type", "subject_type"),
        ("RelationshipRegistry", "resource_fk__resource_type", "subject_fk__resource_type"),
    ):
        model = apps.get_model("rebac", name)
        if not router.allow_migrate_model(alias, model):
            continue
        rows = model._base_manager.using(alias).order_by()
        obsolete = rows.filter(
            models.Q(**{f"{resource}__in": CONTAINERS, "relation": "project", subject: "projects/project"})
            | models.Q(**{resource: "projects/link", "relation": "project", subject: "projects/project"})
            | models.Q(**{resource: "projects/link", "relation": "task", subject: "projects/task"})
        )
        ids = obsolete.values_list("pk", flat=True).iterator(chunk_size=1000)
        while batch := list(islice(ids, 1000)):
            rows.filter(pk__in=batch).delete()


class Migration(migrations.Migration):
    dependencies = [("rebac", "__latest__")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
