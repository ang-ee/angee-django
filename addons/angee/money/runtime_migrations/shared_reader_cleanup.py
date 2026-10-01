"""Delete retired shared readers and role inclusions during ordinary migration.

For RESOURCE_TYPES and RETIRED_RELATION below, delete only auth/user:* subjects
with an empty subject relation, plus every money/role includes tuple, in both
relationship storage modes. Leave other shares and resource-registry rows intact.
Use historical models and the registry model's real FK paths rather than live
queryset translation. Clearing retired grants is fail-closed and needs no prior
schema synchronization. Cleanup is idempotent, respects database routers, and
has a no-op reverse.
"""

from django.db import migrations, router
from django.db.migrations.state import ProjectState
from django.db.models import Q

RESOURCE_TYPES = (
    "money/rate",
)
RETIRED_RELATION = "shared"


def applies(project_state: ProjectState) -> bool:
    return all(
        ("rebac", model_name) in project_state.models
        for model_name in ("relationship", "relationshipregistry")
    )


def forwards(apps, schema_editor):
    alias = schema_editor.connection.alias
    for model_name, filters, role_filters in (
        ("Relationship", {
            "resource_type__in": RESOURCE_TYPES,
            "subject_type": "auth/user",
            "subject_id": "*",
        }, {"resource_type": "money/role", "relation": "includes"}),
        ("RelationshipRegistry", {
            "resource_fk__resource_type__in": RESOURCE_TYPES,
            "subject_fk__resource_type": "auth/user",
            "subject_fk__resource_id": "*",
        }, {"resource_fk__resource_type": "money/role", "relation": "includes"}),
    ):
        model = apps.get_model("rebac", model_name)
        if router.allow_migrate_model(alias, model):
            model._base_manager.using(alias).order_by().filter(
                Q(**filters, relation=RETIRED_RELATION, optional_subject_relation="")
                | Q(**role_filters),
            ).delete()


class Migration(migrations.Migration):
    dependencies = [("rebac", "0002_rebac_resource")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
