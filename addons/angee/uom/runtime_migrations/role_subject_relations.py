"""Rewrite UOM manager tuples to reference a plain fixed role subject."""

from __future__ import annotations

import json
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.db import migrations
from django.db.migrations.state import ProjectState

_APP_LABEL = "uom"
_APP_MODELS = ("uomcategory", "uom")
_RESOURCE_TYPES = ("uom/category", "uom/uom")
_ROLE_TYPE = "uom/role"
_ROLE_ID = "uom_admin"
_RELATIONSHIP_FIELDS = {
    "resource_type",
    "resource_id",
    "relation",
    "subject_type",
    "subject_id",
    "optional_subject_relation",
    "caveat_name",
    "caveat_context",
    "expires_at",
    "written_at_xid",
}
_REGISTRY_FIELDS = {
    "resource_fk",
    "relation",
    "subject_fk",
    "optional_subject_relation",
    "caveat_name",
    "caveat_context",
    "expires_at",
    "written_at_xid",
}


def _condition(row: Any) -> tuple[str, Any]:
    return json.dumps(row.caveat_context, sort_keys=True), row.expires_at


def applies(project_state: ProjectState) -> bool:
    """Apply only when the complete UOM and REBAC storage state exists."""

    present = [project_state.models.get((_APP_LABEL, name)) for name in _APP_MODELS]
    if not any(present):
        return False
    if not all(present):
        raise ImproperlyConfigured("angee.uom:role_subject_relations found partial UOM state")
    relationship = project_state.models.get(("rebac", "relationship"))
    registry = project_state.models.get(("rebac", "relationshipregistry"))
    if relationship is None or registry is None:
        raise ImproperlyConfigured("angee.uom:role_subject_relations requires both REBAC stores")
    if not _RELATIONSHIP_FIELDS.issubset(relationship.fields) or not _REGISTRY_FIELDS.issubset(
        registry.fields
    ):
        raise ImproperlyConfigured("angee.uom:role_subject_relations found unexpected REBAC state")
    return True


def rewrite_manager_subjects(apps: Any, schema_editor: Any) -> None:
    """Remove only the computed permission suffix from owned manager tuples."""

    database = schema_editor.connection.alias
    stores = (
        (apps.get_model("rebac", "Relationship"), False),
        (apps.get_model("rebac", "RelationshipRegistry"), True),
    )
    rewrites: list[tuple[Any, Any | None]] = []
    for model, registry in stores:
        rows = model._base_manager.using(database)
        identity: dict[str, Any] = {"relation": "manager", "optional_subject_relation": "effective_member"}
        if registry:
            identity.update(
                resource_fk__resource_type__in=_RESOURCE_TYPES,
                subject_fk__resource_type=_ROLE_TYPE,
                subject_fk__resource_id=_ROLE_ID,
            )
        else:
            identity.update(resource_type__in=_RESOURCE_TYPES, subject_type=_ROLE_TYPE, subject_id=_ROLE_ID)
        for row in rows.filter(**identity).order_by("pk"):
            destination_identity = {
                "relation": "manager",
                "optional_subject_relation": "",
                "caveat_name": str(row.caveat_name),
            }
            if registry:
                destination_identity.update(resource_fk_id=row.resource_fk_id, subject_fk_id=row.subject_fk_id)
            else:
                destination_identity.update(
                    resource_type=str(row.resource_type),
                    resource_id=str(row.resource_id),
                    subject_type=str(row.subject_type),
                    subject_id=str(row.subject_id),
                )
            destination = rows.filter(**destination_identity).first()
            if destination is not None and _condition(destination) != _condition(row):
                raise ImproperlyConfigured("angee.uom:role_subject_relations found conflicting manager tuples")
            rewrites.append((row, destination))
    for row, destination in rewrites:
        if destination is not None:
            if row.written_at_xid > destination.written_at_xid:
                destination.written_at_xid = row.written_at_xid
                destination.save(update_fields=("written_at_xid",))
            row.delete()
        else:
            row.optional_subject_relation = ""
            row.save(update_fields=("optional_subject_relation",))


class Migration(migrations.Migration):
    """Adopt plain fixed-role subjects for UOM manager relationships."""

    atomic = True
    dependencies = [("uom", "__latest__"), ("rebac", "__latest__")]
    operations = [migrations.RunPython(rewrite_manager_subjects, migrations.RunPython.noop)]
