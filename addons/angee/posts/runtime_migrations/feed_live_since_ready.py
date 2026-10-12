"""Align the live horizon with connection discovery readiness.

The original horizon migration is retained history. A nonzero pending or failed
discovery generation has no live horizon until the runtime discovery owner
completes it; generation zero retains established connections.
"""

from django.db import migrations, models, router
from django.utils import timezone


def applies(project_state):
    feed = project_state.models.get(("posts", "feed"))
    return feed is not None and "live_since" in feed.fields


def align_live_horizons(apps, schema_editor):
    feed = apps.get_model("posts", "Feed")
    alias = schema_editor.connection.alias
    if not router.allow_migrate_model(alias, feed):
        return
    rows = feed._base_manager.using(alias).order_by()
    discovery_fields = {"discovery_generation", "discovery_retry_at", "discovery_completed_at"}
    present = discovery_fields & {field.name for field in feed._meta.get_fields()}
    if present and present != discovery_fields:
        raise ValueError("Feed has a partial connection discovery state.")
    # Before discovery fields were introduced, every existing connection becomes
    # generation zero. This is the discovery owner's established-connection rule.
    ready = models.Q()
    if present:
        ready = models.Q(discovery_retry_at__isnull=True) & (
            models.Q(discovery_generation=0) | models.Q(discovery_completed_at__isnull=False)
        )
        rows.exclude(ready).update(live_since=None)
    rows.filter(ready, live_since__isnull=True).exclude(external_id="").update(live_since=timezone.now())


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = []
    operations = [migrations.RunPython(align_live_horizons, migrations.RunPython.noop)]
