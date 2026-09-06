"""Remove the redundant implementation selector from Integration parents."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.db import migrations, models
from django.db.migrations.state import ProjectState

CONSTRAINT_NAME = "uniq_integrate_parent_owner_vendor_impl"


def _constraint(model_state: object) -> models.UniqueConstraint | None:
    constraints = getattr(model_state, "options", {}).get("constraints", ())
    matches = [item for item in constraints if getattr(item, "name", None) == CONSTRAINT_NAME]
    if len(matches) > 1:
        raise ImproperlyConfigured(f"angee.integrate:integration_parent_impl_axis found duplicate {CONSTRAINT_NAME}")
    return matches[0] if matches else None


def _expected_constraint(constraint: models.UniqueConstraint | None) -> bool:
    return bool(
        constraint is not None
        and constraint.fields == ("owner", "vendor", "impl_class")
        and constraint.condition == models.Q(kind="Integration")
    )


def applies(project_state: ProjectState) -> bool:
    """Apply only to the exact old field-plus-constraint state."""

    model = project_state.models.get(("integrate", "integration"))
    if model is None:
        return False
    has_field = "impl_class" in model.fields
    constraint = _constraint(model)
    if has_field and _expected_constraint(constraint):
        return True
    if not has_field and constraint is None:
        return False
    rendered = None if constraint is None else {
        "fields": constraint.fields,
        "condition": str(constraint.condition),
    }
    raise ImproperlyConfigured(
        "angee.integrate:integration_parent_impl_axis found a partial Integration transition: "
        f"impl_class={has_field}, constraint={rendered}"
    )


def preflight_parent_impl_axis(apps, schema_editor) -> None:
    """Reject custom selector values and sibling-child corruption before destructive DDL."""

    integration = apps.get_model("integrate", "Integration")
    table = schema_editor.quote_name(integration._meta.db_table)
    column = schema_editor.quote_name(integration._meta.get_field("impl_class").column)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"SELECT {column}, COUNT(*) FROM {table} GROUP BY {column}")
        values = dict(cursor.fetchall())
    unsupported = {key: count for key, count in values.items() if key != "none"}
    if unsupported:
        raise ImproperlyConfigured(
            "angee.integrate:integration_parent_impl_axis requires explicit mappings for stored "
            f"implementation keys: {unsupported}"
        )

    seen: dict[object, str] = {}
    siblings: list[tuple[object, str, str]] = []
    for model in apps.get_models():
        parent_link = next(
            (
                field
                for parent, field in model._meta.parents.items()
                if parent._meta.label_lower == integration._meta.label_lower
            ),
            None,
        )
        if parent_link is None:
            continue
        for parent_pk in model._base_manager.using(schema_editor.connection.alias).values_list(
            parent_link.attname,
            flat=True,
        ):
            previous = seen.setdefault(parent_pk, model._meta.label)
            if previous != model._meta.label:
                siblings.append((parent_pk, previous, model._meta.label))
    if siblings:
        preview = siblings[:10]
        raise ImproperlyConfigured(
            "angee.integrate:integration_parent_impl_axis found Integration parents with multiple "
            f"concrete children: {preview}"
        )


class Migration(migrations.Migration):
    """Preflight live rows, then remove the neutral selector and its uniqueness rule."""

    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.RunPython(preflight_parent_impl_axis),
        migrations.RemoveConstraint(model_name="integration", name=CONSTRAINT_NAME),
        migrations.RemoveField(model_name="integration", name="impl_class"),
    ]
