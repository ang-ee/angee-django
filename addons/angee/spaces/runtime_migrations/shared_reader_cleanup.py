"""Delete retired user-reader tuples during ordinary migration execution.

From spaces schema revision 8, reader is const-backed and stored user readers
have no effect. Delete every auth/user subject on that relation in both stores,
logging each store's deleted count. Leave other resource types, relations,
subject types and resource-registry rows intact. Use historical models and the
registry model's real FK paths rather than live queryset translation. Clearing
retired grants is fail-closed and needs no prior schema synchronization. Cleanup
is idempotent, respects database routers, and has a no-op reverse.
"""

import logging

from django.db import migrations, router
from django.db.migrations.state import ProjectState

RESOURCE_TYPES = (
    "spaces/group",
)
RETIRED_RELATION = "reader"
logger = logging.getLogger(__name__)


def applies(project_state: ProjectState) -> bool:
    return all(
        ("rebac", model_name) in project_state.models
        for model_name in ("relationship", "relationshipregistry")
    )


def forwards(apps, schema_editor):
    alias = schema_editor.connection.alias
    for model_name, filters in (
        ("Relationship", {
            "resource_type__in": RESOURCE_TYPES,
            "subject_type": "auth/user",
        }),
        ("RelationshipRegistry", {
            "resource_fk__resource_type__in": RESOURCE_TYPES,
            "subject_fk__resource_type": "auth/user",
        }),
    ):
        model = apps.get_model("rebac", model_name)
        if router.allow_migrate_model(alias, model):
            deleted, _ = model._base_manager.using(alias).order_by().filter(
                **filters, relation=RETIRED_RELATION,
            ).delete()
            logger.info("Deleted %d retired spaces/group reader tuples from %s.", deleted, model._meta.label)


class Migration(migrations.Migration):
    dependencies = [("rebac", "0002_rebac_resource")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
