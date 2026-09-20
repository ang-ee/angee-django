"""Retarget pre-PK rate reader identities without changing their grants."""

from __future__ import annotations

from typing import Any, ClassVar

from django.db import migrations
from django.db.migrations.state import ProjectState
from rebac import ObjectRef

from angee.base.fields import SqidField
from angee.base.historical_relationships import retarget_historical_resource


def applies(project_state: ProjectState) -> bool:
    """Apply when the rate catalogue exists, including composed extensions."""

    return ("money", "currencyrate") in project_state.models


def retarget_rate_readers(apps: Any, schema_editor: Any) -> None:
    """Preserve only existing SQID-attached grants under canonical PK IDs."""

    alias = schema_editor.connection.alias
    rate = apps.get_model("money", "CurrencyRate")
    codec = SqidField(prefix="crt_")
    for pk in rate._base_manager.using(alias).order_by("pk").values_list("pk", flat=True):
        retarget_historical_resource(
            apps,
            using=alias,
            old=ObjectRef("money/rate", codec.public_id_from_value(pk)),
            new=ObjectRef("money/rate", str(pk)),
        )


class Migration(migrations.Migration):
    dependencies: ClassVar[list[tuple[str, str]]] = [
        ("money", "__latest__"),
        ("rebac", "__latest__"),
    ]
    operations: ClassVar[list[migrations.operations.base.Operation]] = [
        migrations.RunPython(retarget_rate_readers),
    ]
