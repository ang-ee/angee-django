"""Transfer package ownership of the renamed VCS permission schema rows."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.db import migrations
from django.db.migrations.state import ProjectState

MOVED_TYPES = (
    "integrate/vcs_bridge",
    "integrate/repository",
    "integrate/source",
    "integrate/template",
)
OLD_PACKAGE = "angee.integrate"
NEW_PACKAGE = "angee.integrate_vcs"
SCHEMA_KINDS = ("definition", "relation", "permission")


def applies(project_state: ProjectState) -> bool:
    """Materialize once the complete extracted VCS model state is present."""

    moved = {("integrate_vcs", name) for name in ("vcsbridge", "repository", "source", "template")}
    present = moved & set(project_state.models)
    if not present:
        return False
    if present != moved:
        raise ImproperlyConfigured(
            "angee.integrate_vcs:adopt_vcs_permission_schema found a partial VCS model adoption"
        )
    return True


def _new_external_id(external_id: str) -> str | None:
    kind, separator, identity = external_id.partition(":")
    if not separator or kind not in SCHEMA_KINDS:
        return None
    resource_type, relation_separator, suffix = identity.partition("#")
    if resource_type not in MOVED_TYPES:
        return None
    renamed = resource_type.replace("integrate/", "integrate_vcs/", 1)
    return f"{kind}:{renamed}{relation_separator}{suffix}"


def adopt_vcs_permission_schema(apps, schema_editor) -> None:
    """Move exact VCS schema provenance keys without changing their target rows."""

    try:
        managed_record = apps.get_model("rebac", "PackageManagedRecord")
    except LookupError:
        return

    alias = schema_editor.connection.alias
    records = managed_record._base_manager.using(alias).filter(package=OLD_PACKAGE).order_by("pk")
    moves = [(record, new_id) for record in records if (new_id := _new_external_id(record.external_id))]
    if not moves:
        return

    destination_ids = [new_id for _, new_id in moves]
    collisions = list(
        managed_record._base_manager.using(alias)
        .filter(package=NEW_PACKAGE, external_id__in=destination_ids)
        .order_by("external_id")
        .values_list("external_id", flat=True)
    )
    if collisions:
        raise ImproperlyConfigured(
            "angee.integrate_vcs:adopt_vcs_permission_schema found destination package records; "
            f"repair collisions before extraction: {collisions}"
        )

    for record, new_id in moves:
        record.package = NEW_PACKAGE
        record.external_id = new_id
        record.save(update_fields=["package", "external_id"])


class Migration(migrations.Migration):
    """Adopt provenance for the renamed VCS permission schema."""

    dependencies = [("integrate_vcs", "__latest__")]
    operations = [migrations.RunPython(adopt_vcs_permission_schema)]
