"""Release historical channel-thread owners before enforcing channel authority."""

from django.db import migrations, models, router
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    thread = project_state.models.get(("messaging", "thread"))
    return thread is not None and "owner" in thread.fields and not any(
        constraint.name == "ck_thread_channel_ownerless" for constraint in thread.options.get("constraints", ())
    )


def forwards(apps, schema_editor):
    thread = apps.get_model("messaging", "Thread")
    alias = schema_editor.connection.alias
    if router.allow_migrate_model(alias, thread):
        if schema_editor.connection.vendor == "postgresql":
            schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        thread._base_manager.using(alias).order_by().filter(channel__isnull=False).update(owner_id=None)


class Migration(migrations.Migration):
    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.AddConstraint(
            "thread", models.CheckConstraint(
                condition=models.Q(channel__isnull=True) | models.Q(owner__isnull=True),
                name="ck_thread_channel_ownerless",
            ),
        ),
    ]
