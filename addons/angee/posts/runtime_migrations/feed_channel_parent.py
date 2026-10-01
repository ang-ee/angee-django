"""Insert Channel parents for live feeds before changing their MTI parent."""

from django.db import migrations, models
from django.db.migrations.operations.base import Operation


BRIDGE_FIELDS = (
    "config",
    "cursor",
    "poll_interval",
    "subscription_state",
    "next_subscription_refresh_at",
    "last_sync_started_at",
    "last_sync_completed_at",
    "last_sync_items",
    "sync_stage",
    "sync_error",
    "sync_run_id",
    "sync_progress",
    "last_sync_summary",
    "next_sync_at",
)


def applies(project_state):
    feed = project_state.models.get(("posts", "feed"))
    channel = project_state.models.get(("messaging", "channel"))
    if feed is None:
        return False
    if channel is None:
        raise ValueError("Feed exists without its required Channel model.")
    fields = feed.fields
    old = (
        "integrate.integration" in feed.bases
        and "integration_ptr" in fields
        and "backend_class" in fields
    )
    new = (
        "messaging.channel" in feed.bases
        and "channel_ptr" in fields
        and "feed_backend_class" in fields
    )
    if old and not new and all(name in fields for name in BRIDGE_FIELDS):
        return True
    if new and not old and all(name not in fields for name in BRIDGE_FIELDS):
        return False
    raise ValueError("Feed has a partial Channel parent transition; complete or reverse its migration history.")


def insert_channel_parents(apps, schema_editor):
    """Copy each legacy feed's bridge state into a same-key Channel row."""

    feed = apps.get_model("posts", "Feed")
    channel = apps.get_model("messaging", "Channel")
    connection = schema_editor.connection
    quote = connection.ops.quote_name
    source_columns = {field.column for field in feed._meta.local_concrete_fields}
    columns = []
    values = []
    params = []
    for field in channel._meta.local_concrete_fields:
        columns.append(quote(field.column))
        if field.primary_key:
            values.append(f"source.{quote(feed._meta.pk.column)}")
        elif field.name == "backend_class":
            values.append("%s")
            params.append(field.get_db_prep_save("feed", connection))
        elif field.column in source_columns:
            values.append(f"source.{quote(field.column)}")
        else:
            if field.has_default():
                default = field.get_default()
            elif field.null:
                default = None
            else:
                raise ValueError(f"Channel field {field.name} has no migration default.")
            values.append("%s")
            params.append(field.get_db_prep_save(default, connection))
    sql = (
        f"INSERT INTO {quote(channel._meta.db_table)} ({', '.join(columns)}) "
        f"SELECT {', '.join(values)} FROM {quote(feed._meta.db_table)} source"
    )
    with connection.cursor() as cursor:
        cursor.execute(sql, params)


class ReparentFeedState(Operation):
    """Change the historical Feed base after Django changes its local fields."""

    reduces_to_sql = False
    reversible = False

    def state_forwards(self, app_label, state):
        state.models[("posts", "feed")].bases = ("messaging.channel", models.Model)
        state.reload_model("posts", "feed", delay=True)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        pass

    def describe(self):
        return "Reparent posts.Feed onto messaging.Channel"


class Migration(migrations.Migration):
    dependencies = [("messaging", "__latest__")]
    operations = [
        migrations.RunPython(insert_channel_parents),
        migrations.RenameField("feed", "backend_class", "feed_backend_class"),
        *(migrations.RemoveField("feed", name) for name in BRIDGE_FIELDS),
        migrations.RenameField("feed", "integration_ptr", "channel_ptr"),
        migrations.AlterField(
            "feed",
            "channel_ptr",
            models.OneToOneField(
                auto_created=True,
                on_delete=models.CASCADE,
                parent_link=True,
                primary_key=True,
                serialize=False,
                to="messaging.channel",
            ),
        ),
        ReparentFeedState(),
    ]
