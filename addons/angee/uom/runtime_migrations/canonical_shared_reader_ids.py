"""Retarget pre-PK unit catalogue readers without granting new visibility."""

from __future__ import annotations

from typing import Any, ClassVar

from django.db import migrations
from django.db.migrations.state import ProjectState
from rebac import ObjectRef

from angee.base.fields import SqidField
from angee.base.historical_relationships import retarget_historical_resource


def applies(project_state: ProjectState) -> bool:
    """Apply once both unit catalogue models are present."""

    return all(
        ("uom", model_name) in project_state.models
        for model_name in ("uomcategory", "uom")
    )


def retarget_unit_readers(apps: Any, schema_editor: Any) -> None:
    """Move existing SQID relationships to canonical PK resource IDs."""

    alias = schema_editor.connection.alias
    for model_name, resource_type, prefix in (
        ("UomCategory", "uom/category", "uoc_"),
        ("Uom", "uom/uom", "uom_"),
    ):
        model = apps.get_model("uom", model_name)
        codec = SqidField(prefix=prefix)
        for pk in model._base_manager.using(alias).order_by("pk").values_list(
            "pk", flat=True
        ):
            retarget_historical_resource(
                apps,
                using=alias,
                old=ObjectRef(resource_type, codec.public_id_from_value(pk)),
                new=ObjectRef(resource_type, str(pk)),
            )


class Migration(migrations.Migration):
    dependencies: ClassVar[list[tuple[str, str]]] = [
        ("uom", "__latest__"),
        ("rebac", "__latest__"),
    ]
    operations: ClassVar[list[migrations.operations.base.Operation]] = [
        migrations.RunPython(retarget_unit_readers),
    ]
