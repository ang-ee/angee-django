"""Adopt plain ToolRole subjects for role inheritance and tool grants."""

from __future__ import annotations

import json
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.db import migrations
from django.db.migrations.state import ProjectState

_TOOL_ROLE = "agents/toolrole"
_TOOL_GRANT = "agents/tool_grant"
_EFFECTIVE_MEMBER = "effective_member"


def applies(project_state: ProjectState) -> bool:
    """Apply only when the agents catalogue exists in the historical state."""

    present = {
        name
        for name in ("agent", "mcpserver", "mcptool")
        if ("agents", name) in project_state.models
    }
    if not present:
        return False
    if present != {"agent", "mcpserver", "mcptool"}:
        raise ImproperlyConfigured(
            "angee.agents:role_subject_relations found partial agents catalogue state"
        )
    return True


def _wire_identity(row: Any, *, registry: bool) -> dict[str, Any]:
    if registry:
        return {
            "resource_fk_id": row.resource_fk_id,
            "subject_fk_id": row.subject_fk_id,
        }
    return {
        "resource_type": str(row.resource_type),
        "resource_id": str(row.resource_id),
        "subject_type": str(row.subject_type),
        "subject_id": str(row.subject_id),
    }


def _condition(row: Any) -> tuple[str, Any]:
    return json.dumps(row.caveat_context, sort_keys=True), row.expires_at


def _plan_store(
    model: Any,
    database: str,
    *,
    registry: bool,
) -> tuple[list[tuple[Any, str]], list[tuple[Any, Any]]]:
    manager = model._base_manager.using(database)
    resource_type = "resource_fk__resource_type" if registry else "resource_type"
    subject_type = "subject_fk__resource_type" if registry else "subject_type"
    sources = manager.filter(
        **{
            subject_type: _TOOL_ROLE,
            "optional_subject_relation": _EFFECTIVE_MEMBER,
            f"{resource_type}__in": (_TOOL_ROLE, _TOOL_GRANT),
        }
    ).order_by("pk")
    if registry:
        sources = sources.select_related("resource_fk")
    rewrites: list[tuple[Any, str]] = []
    duplicates: list[tuple[Any, Any]] = []
    for source in sources:
        source_resource_type = (
            str(source.resource_fk.resource_type) if registry else str(source.resource_type)
        )
        if source_resource_type == _TOOL_ROLE and str(source.relation) != "includes":
            continue
        if source_resource_type == _TOOL_GRANT and str(source.relation) != "grantee":
            continue
        target_relation = "includes" if source_resource_type == _TOOL_ROLE else "role"
        collision = manager.filter(
            **_wire_identity(source, registry=registry),
            relation=target_relation,
            optional_subject_relation="",
            caveat_name=str(source.caveat_name),
        ).first()
        if collision is None:
            rewrites.append((source, target_relation))
            continue
        if _condition(collision) != _condition(source):
            raise ImproperlyConfigured(
                "angee.agents:role_subject_relations found role tuple collisions "
                "with different caveat context or expiry"
            )
        duplicates.append((source, collision))
    return rewrites, duplicates


def rewrite_role_subject_relations(apps: Any, schema_editor: Any) -> None:
    """Rewrite the two obsolete ToolRole subject-set tuple shapes."""

    database = schema_editor.connection.alias
    stores = (
        (apps.get_model("rebac", "Relationship"), False),
        (apps.get_model("rebac", "RelationshipRegistry"), True),
    )
    plans = [
        (model._base_manager.using(database), *_plan_store(model, database, registry=registry))
        for model, registry in stores
    ]
    for manager, rewrites, duplicates in plans:
        for source, target_relation in rewrites:
            source.relation = target_relation
            source.optional_subject_relation = ""
            source.save(update_fields=("relation", "optional_subject_relation"))
        for source, destination in duplicates:
            if destination.written_at_xid < source.written_at_xid:
                destination.written_at_xid = source.written_at_xid
                destination.save(update_fields=("written_at_xid",))
        if duplicates:
            manager.filter(pk__in=[source.pk for source, _ in duplicates]).delete()


class Migration(migrations.Migration):
    """Store role objects directly and traverse effective membership in schema."""

    atomic = True
    dependencies = [
        ("agents", "__latest__"),
        ("rebac", "__latest__"),
    ]
    operations = [
        migrations.RunPython(
            rewrite_role_subject_relations,
            migrations.RunPython.noop,
        )
    ]
