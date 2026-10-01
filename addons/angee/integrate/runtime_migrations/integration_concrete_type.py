"""Backfill the final MTI identity before retiring Integration.kind."""

from django.db import migrations, models


def applies(project_state):
    model = project_state.models.get(("integrate", "integration"))
    if model is None:
        return False
    fields = model.fields
    if "kind" in fields and "concrete_type" not in fields:
        return True
    if "concrete_type" in fields and "kind" not in fields:
        return False
    raise ValueError("Integration has a partial concrete type transition; complete or reverse its migration history.")


def backfill_concrete_type(apps, schema_editor):
    """Assign the deepest installed child with a row for each Integration PK."""

    db = schema_editor.connection.alias
    integration = apps.get_model("integrate", "Integration")
    rows = integration._base_manager.using(db).order_by()
    if not rows.exists():
        return

    # A later RemoveField changes this same table. PostgreSQL must check FK
    # updates immediately or it refuses that DDL for pending trigger events.
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")

    content_type = apps.get_model("contenttypes", "ContentType")
    types = content_type._base_manager.db_manager(db)

    def type_id(model):
        return types.get_or_create(app_label=model._meta.app_label, model=model._meta.model_name)[0].pk

    children = sorted(
        (
            model
            for model in apps.get_models()
            if model is not integration
            and model._meta.managed
            and not model._meta.proxy
            and issubclass(model, integration)
        ),
        key=lambda model: (len(model._meta.get_parent_list()), model._meta.label_lower),
    )
    populated = []
    for child in children:
        child_rows = child._base_manager.using(db).order_by()
        if child_rows.exists():
            populated.append((child, child_rows))
    for index, (left, left_rows) in enumerate(populated):
        for right, right_rows in populated[index + 1:]:
            if not issubclass(left, right) and not issubclass(right, left):
                if left_rows.filter(pk__in=right_rows.values("pk")).exists():
                    raise ValueError(
                        f"Integration rows have incompatible children {left._meta.label} and {right._meta.label}."
                    )

    rows.update(concrete_type_id=type_id(integration))
    for child, child_rows in populated:
        rows.filter(pk__in=child_rows.values("pk")).update(concrete_type_id=type_id(child))


class Migration(migrations.Migration):
    dependencies = [("contenttypes", "__latest__")]
    operations = [
        migrations.AddField(
            "integration",
            "concrete_type",
            models.ForeignKey(to="contenttypes.contenttype", on_delete=models.PROTECT, null=True, editable=False),
        ),
        migrations.RunPython(backfill_concrete_type, migrations.RunPython.noop),
        migrations.RemoveField("integration", "kind"),
    ]
