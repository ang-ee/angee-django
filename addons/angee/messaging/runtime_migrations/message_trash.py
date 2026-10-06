"""Carry hidden and removed message moderation into the trash flag."""

from django.conf import settings
from django.db import migrations, models, router
from django.db.migrations.state import ProjectState

from angee.base.fields import StateField

RETIRED = ("hidden", "removed")
STATUSES = (
    ("draft", "Draft"),
    ("queued", "Queued"),
    ("sent", "Sent"),
    ("synced", "Synced"),
    ("edited", "Edited"),
    ("failed", "Failed"),
)


def applies(project_state: ProjectState) -> bool:
    message = project_state.models.get(("messaging", "message"))
    if message is None:
        return False
    retired = {value for value, _label in message.fields["status"].choices} & set(RETIRED)
    if "is_trashed" not in message.fields and retired == set(RETIRED):
        return True
    if "is_trashed" in message.fields and not retired:
        return False
    raise ValueError("Message has a partial trash transition; complete or reverse its migration history.")


def carry_moderation(apps, schema_editor):
    message = apps.get_model("messaging", "Message")
    alias = schema_editor.connection.alias
    if not router.allow_migrate_model(alias, message):
        return
    rows = message._base_manager.using(alias).order_by()
    # The AlterField that follows changes this same table. PostgreSQL must check
    # foreign keys of the updated rows now or it refuses that DDL.
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    moderated = rows.filter(status__in=RETIRED)
    moderated.filter(direction="outbound").update(is_trashed=True, trashed_at=models.F("updated_at"), status="sent")
    moderated.update(is_trashed=True, trashed_at=models.F("updated_at"), status="synced")


def uncarry_moderation(apps, schema_editor):
    message = apps.get_model("messaging", "Message")
    alias = schema_editor.connection.alias
    if not router.allow_migrate_model(alias, message):
        return
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    message._base_manager.using(alias).order_by().filter(is_trashed=True).update(status="removed")


class Migration(migrations.Migration):
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField(
            "message",
            "is_trashed",
            models.BooleanField(default=False, db_index=True, editable=False),
        ),
        migrations.AddField(
            "message",
            "trashed_at",
            models.DateTimeField(null=True, blank=True, editable=False),
        ),
        migrations.AddField(
            "message",
            "trashed_by",
            models.ForeignKey(
                settings.AUTH_USER_MODEL,
                on_delete=models.SET_NULL,
                null=True,
                blank=True,
                related_name="+",
                editable=False,
            ),
        ),
        migrations.AddField(
            "message",
            "trash_reason",
            models.TextField(blank=True, default="", editable=False),
        ),
        migrations.RunPython(carry_moderation, uncarry_moderation),
        migrations.AlterField(
            "message",
            "status",
            StateField(choices=STATUSES, max_length=6, default="synced", db_index=True),
        ),
    ]
