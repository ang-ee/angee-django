"""Copy historical Django auth groups into IAM without changing primary keys."""

from __future__ import annotations

from itertools import islice
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.core.management.color import no_style
from django.db import migrations, models
from django.db.migrations.state import ProjectState
from django.utils import timezone

_BATCH_SIZE = 1_000


def applies(project_state: ProjectState) -> bool:
    """Apply while auth owns groups and IAM has no concrete group table."""

    owner = project_state.models.get(("iam", "user"))
    if owner is None:
        return False
    source = project_state.models.get(("auth", "group"))
    target = project_state.models.get(("iam", "group"))
    if source is None:
        return False
    if target is None:
        return True
    if target.options.get("proxy"):
        return True
    if _is_current_group(target):
        return False
    raise ImproperlyConfigured(
        "angee.iam:adopt_auth_groups found an unexpected concrete IAM Group state"
    )


def _is_current_group(model_state: Any) -> bool:
    name = model_state.fields.get("name")
    description = model_state.fields.get("description")
    return (
        isinstance(name, models.CharField)
        and name.max_length == 150
        and name.unique
        and isinstance(description, models.TextField)
        and description.blank
        and description.default == ""
    )


def copy_auth_groups(apps: Any, schema_editor: Any) -> None:
    """Copy source rows losslessly and reset the destination PK sequence."""

    source_model = apps.get_model("auth", "Group")
    group_model = apps.get_model("iam", "Group")
    database = schema_editor.connection.alias
    source = source_model._base_manager.using(database).order_by("pk").values_list("pk", "name")
    destination = group_model._base_manager.using(database)
    existing_by_pk = dict(destination.values_list("pk", "name"))
    existing_by_name = dict(destination.values_list("name", "pk"))

    rows = []
    now = timezone.now()
    for pk, name in source.iterator(chunk_size=_BATCH_SIZE):
        if pk in existing_by_pk:
            if existing_by_pk[pk] != name:
                raise ImproperlyConfigured(
                    "angee.iam:adopt_auth_groups found an IAM group primary-key collision"
                )
            continue
        if name in existing_by_name:
            raise ImproperlyConfigured(
                "angee.iam:adopt_auth_groups found an IAM group name at a different primary key"
            )
        rows.append(
            group_model(
                pk=pk,
                name=name,
                description="",
                created_at=now,
                updated_at=now,
            )
        )

    pending = iter(rows)
    while batch := list(islice(pending, _BATCH_SIZE)):
        destination.bulk_create(batch, batch_size=_BATCH_SIZE)

    statements = schema_editor.connection.ops.sequence_reset_sql(no_style(), [group_model])
    if statements:
        with schema_editor.connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)


def group_state_operations() -> list[Any]:
    """Return fresh operations replacing the historical proxy with IAM state."""

    return [
        migrations.CreateModel(
            name="Group",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("name", models.CharField(max_length=150, unique=True)),
                ("description", models.TextField(blank=True, default="")),
            ],
        ),
    ]


class Migration(migrations.Migration):
    """Preserve auth-group rows while IAM takes ownership of the concept."""

    dependencies = [("iam", "__latest__"), ("auth", "__latest__")]
    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                *group_state_operations(),
                migrations.RunPython(copy_auth_groups),
            ],
            state_operations=group_state_operations(),
        )
    ]
