"""Retain stored activity keys while giving them protected catalog references."""

from django.conf import settings
from django.db import migrations, models, router
from django.db.migrations.state import ProjectState

from angee.base.mixins import audit_set_null


def applies(project_state: ProjectState) -> bool:
    activity = project_state.models.get(("messaging", "threadactivity"))
    return activity is not None and not isinstance(activity.fields["activity_type"], models.ForeignKey)


class EnsureCatalog(migrations.CreateModel):
    """Create the catalog only on hosts upgrading from before its introduction."""

    def state_forwards(self, app_label, state):
        if (app_label, self.name_lower) not in state.models:
            super().state_forwards(app_label, state)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if (app_label, self.name_lower) not in from_state.models:
            super().database_forwards(app_label, schema_editor, from_state, to_state)


def forwards(apps, schema_editor):
    activity = apps.get_model("messaging", "ThreadActivity")
    catalog = apps.get_model("messaging", "ActivityType")
    alias = schema_editor.connection.alias
    if not router.allow_migrate_model(alias, activity) or not router.allow_migrate_model(alias, catalog):
        return
    rows = activity._base_manager.using(alias).order_by()
    types = catalog._base_manager.using(alias).order_by()
    for key in rows.values_list("activity_type", flat=True).distinct().iterator(chunk_size=500):
        types.get_or_create(key=key, defaults={"name": key})


class Migration(migrations.Migration):
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        EnsureCatalog(
            name="ActivityType",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False, auto_created=True, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("key", models.SlugField(max_length=64, unique=True)),
                ("name", models.CharField(max_length=160)),
                ("glyph", models.CharField(max_length=128, blank=True, default="")),
                ("created_by", models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                    related_name="+", on_delete=audit_set_null)),
                ("updated_by", models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                    related_name="+", on_delete=audit_set_null)),
            ],
            options={"ordering": ("name", "key")},
        ),
        # Deliberately irreversible: reversing must not drop a pre-existing catalog.
        migrations.RunPython(forwards),
        migrations.AlterField(
            "threadactivity", "activity_type",
            models.ForeignKey("messaging.ActivityType", to_field="key", db_column="activity_type",
                on_delete=models.PROTECT, default="todo", related_name="activities"),
        ),
    ]
