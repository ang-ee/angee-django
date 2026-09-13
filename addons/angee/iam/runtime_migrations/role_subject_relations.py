"""Rewrite role hierarchy tuples after ``includes`` becomes a plain role edge."""

from __future__ import annotations

import json
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.db import migrations
from django.db.migrations.state import ProjectState

_ROLE_SUFFIX = "/role"
_INCLUDES_RELATION = "includes"
_EFFECTIVE_MEMBER = "effective_member"
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


def applies(project_state: ProjectState) -> bool:
    """Materialize when IAM and both historical REBAC stores are coherent."""

    if project_state.models.get(("iam", "user")) is None:
        return False
    relationship = project_state.models.get(("rebac", "relationship"))
    registry = project_state.models.get(("rebac", "relationshipregistry"))
    if relationship is None and registry is None:
        return False
    if relationship is None or registry is None:
        raise ImproperlyConfigured(
            "angee.iam:role_subject_relations requires both REBAC relationship stores"
        )
    if not _RELATIONSHIP_FIELDS.issubset(relationship.fields) or not _REGISTRY_FIELDS.issubset(
        registry.fields
    ):
        raise ImproperlyConfigured(
            "angee.iam:role_subject_relations found an unexpected REBAC relationship state"
        )
    return True


def _role_rows(model: Any, database: str, *, registry: bool) -> Any:
    identity = {
        "relation": _INCLUDES_RELATION,
        "optional_subject_relation": _EFFECTIVE_MEMBER,
    }
    if registry:
        identity["resource_fk__resource_type__endswith"] = _ROLE_SUFFIX
        return (
            model._base_manager.using(database)
            .filter(**identity)
            .select_related("resource_fk", "subject_fk")
            .order_by("pk")
        )
    identity["resource_type__endswith"] = _ROLE_SUFFIX
    return model._base_manager.using(database).filter(**identity).order_by("pk")


def _subject_type(row: Any, *, registry: bool) -> str:
    return str(row.subject_fk.resource_type) if registry else str(row.subject_type)


def _resource_type(row: Any, *, registry: bool) -> str:
    return str(row.resource_fk.resource_type) if registry else str(row.resource_type)


def _condition(row: Any) -> tuple[str, Any]:
    return json.dumps(row.caveat_context, sort_keys=True), row.expires_at


def _destination(rows: Any, row: Any, *, registry: bool) -> Any:
    identity = {
        "relation": _INCLUDES_RELATION,
        "optional_subject_relation": "",
        "caveat_name": str(row.caveat_name),
    }
    if registry:
        identity.update(resource_fk_id=row.resource_fk_id, subject_fk_id=row.subject_fk_id)
    else:
        identity.update(
            resource_type=str(row.resource_type),
            resource_id=str(row.resource_id),
            subject_type=str(row.subject_type),
            subject_id=str(row.subject_id),
        )
    return rows.filter(**identity).first()


def rewrite_role_subject_relations(apps: Any, schema_editor: Any) -> None:
    """Rewrite exact same-role hierarchy edges without weakening conditions."""

    database = schema_editor.connection.alias
    stores = (
        (apps.get_model("rebac", "Relationship"), False),
        (apps.get_model("rebac", "RelationshipRegistry"), True),
    )
    rewrites: list[tuple[Any, Any | None]] = []
    for model, registry in stores:
        manager = model._base_manager.using(database)
        for row in _role_rows(model, database, registry=registry):
            if _subject_type(row, registry=registry) != _resource_type(row, registry=registry):
                continue
            destination = _destination(manager, row, registry=registry)
            if destination is not None and _condition(destination) != _condition(row):
                raise ImproperlyConfigured(
                    "angee.iam:role_subject_relations found colliding role hierarchy "
                    "tuples with different caveat context or expiry"
                )
            rewrites.append((row, destination))

    for row, destination in rewrites:
        if destination is not None:
            if row.written_at_xid > destination.written_at_xid:
                destination.written_at_xid = row.written_at_xid
                destination.save(update_fields=("written_at_xid",))
            row.delete()
            continue
        row.optional_subject_relation = ""
        row.save(update_fields=("optional_subject_relation",))


class Migration(migrations.Migration):
    """Adopt relation-to-relation traversal for every IAM role namespace."""

    atomic = True
    dependencies = [
        ("iam", "__latest__"),
        ("rebac", "__latest__"),
    ]
    operations = [
        migrations.RunPython(
            rewrite_role_subject_relations,
            migrations.RunPython.noop,
        )
    ]
