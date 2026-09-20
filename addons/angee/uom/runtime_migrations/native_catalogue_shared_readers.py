"""Grant wildcard readers only to exact unextended native catalogue rows.

An extended/composed UoM model may add a narrower visibility policy.  This
framework migration therefore refuses to classify such rows: the extension
owner must append one composed migration that evaluates its persisted fields.
That keeps the permission transition default-deny and avoids a grant-all window.
"""

from __future__ import annotations

from typing import Any, ClassVar

from angee.base.fields import SqidField
from angee.base.historical_relationships import (
    delete_historical_relationships,
    ensure_historical_relationships,
)
from django.db import migrations
from django.db.migrations.state import ProjectState
from rebac import ObjectRef, RelationshipTuple, SubjectRef

_NATIVE_FIELDS = {
    "uomcategory": {"id", "created_at", "updated_at", "name"},
    "uom": {
        "id",
        "created_at",
        "updated_at",
        "is_archived",
        "name",
        "category",
        "ratio",
        "offset",
        "rounding",
        "is_reference",
    },
}


def applies(project_state: ProjectState) -> bool:
    """Materialize only for the exact framework-owned, unextended schema."""

    for model_name, expected in _NATIVE_FIELDS.items():
        model = project_state.models.get(("uom", model_name))
        if model is None or set(model.fields) != expected:
            return False
    return True


def _relationships(apps: Any, alias: str) -> tuple[RelationshipTuple, ...]:
    everyone = SubjectRef.of("auth/user", "*")
    relationships: list[RelationshipTuple] = []
    for model_name, resource_type, prefix in (
        ("UomCategory", "uom/category", "uoc_"),
        ("Uom", "uom/uom", "uom_"),
    ):
        model = apps.get_model("uom", model_name)
        codec = SqidField(prefix=prefix)
        relationships.extend(
            RelationshipTuple(
                resource=ObjectRef(resource_type, codec.public_id_from_value(pk)),
                relation="shared",
                subject=everyone,
            )
            for pk in model._base_manager.using(alias).order_by("pk").values_list(
                "pk", flat=True
            )
        )
    return tuple(relationships)


def add_native_catalogue_readers(apps: Any, schema_editor: Any) -> None:
    """Grant existing exact-native catalogue rows on the migration alias."""

    alias = schema_editor.connection.alias
    ensure_historical_relationships(
        apps,
        using=alias,
        relationships=_relationships(apps, alias),
    )


def remove_native_catalogue_readers(apps: Any, schema_editor: Any) -> None:
    """Remove only the wildcard tuples contributed by this migration."""

    alias = schema_editor.connection.alias
    delete_historical_relationships(
        apps,
        using=alias,
        relationships=_relationships(apps, alias),
    )


class Migration(migrations.Migration):
    """Backfill readers for existing unextended native UoM catalogue rows."""

    dependencies: ClassVar[list[tuple[str, str]]] = [("rebac", "__latest__")]
    operations: ClassVar[list[migrations.operations.base.Operation]] = [
        migrations.RunPython(
            add_native_catalogue_readers,
            remove_native_catalogue_readers,
        )
    ]
