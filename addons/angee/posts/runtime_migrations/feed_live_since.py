"""Keep existing bound feeds live when introducing per-record history policy."""

from django.db import migrations, router
from django.db.migrations.state import ProjectState
from django.utils import timezone


def applies(project_state: ProjectState) -> bool:
    feed = project_state.models.get(("posts", "feed"))
    return feed is not None and "live_since" in feed.fields


def stamp_bound_feeds(apps, schema_editor):
    feed = apps.get_model("posts", "Feed")
    alias = schema_editor.connection.alias
    if not router.allow_migrate_model(alias, feed):
        return
    rows = feed._base_manager.using(alias)
    rows.filter(live_since__isnull=True).exclude(external_id="").update(live_since=timezone.now())


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = []
    operations = [migrations.RunPython(stamp_bound_feeds, migrations.RunPython.noop)]
