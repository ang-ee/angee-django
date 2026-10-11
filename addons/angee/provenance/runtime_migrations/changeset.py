"""Create the default changeset table before downstream history-FK migrations.

No legacy attribution is invented. Reversing this initial schema drops its
metadata; protected downstream history foreign keys must be removed first.
"""

from django.conf import settings
from django.db import migrations, models
from django.db.migrations.state import ProjectState
from django.utils import timezone

from angee.base.fields import SqidField


def applies(state: ProjectState) -> bool:
    if settings.ANGEE_CHANGESET_MODEL.lower() != "provenance.changeset":
        return False
    existing = state.models.get(("provenance", "changeset"))
    if existing is None:
        return True
    required = {
        "actor", "action", "activity", "change_reason", "correlation_id", "idempotency_key",
        "origin_content_type", "origin_object_id", "occurred_at", "recorded_at", "state", "outcome",
        "hash", "counts", "touched", "sqid", "id",
    }
    if not required.issubset(existing.fields):
        raise ValueError("Complete or reverse the partial Changeset schema before upgrading.")
    return False


class Migration(migrations.Migration):
    initial = True
    dependencies = [("contenttypes", "0002_remove_content_type_name")]
    operations = [
        migrations.CreateModel(
            name="Changeset",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sqid", SqidField(real_field_name="id", min_length=8, prefix="cst_")),
                ("actor", models.TextField(blank=True, editable=False)),
                ("action", models.CharField(max_length=1, choices=(("C", "Create"), ("U", "Update"), ("D", "Delete")), default="U")),
                ("activity", models.CharField(max_length=64, default="manual")),
                ("change_reason", models.TextField(blank=True)),
                ("correlation_id", models.CharField(max_length=255, blank=True, db_index=True)),
                ("idempotency_key", models.CharField(max_length=255, blank=True, db_index=True)),
                ("origin_content_type", models.ForeignKey("contenttypes.contenttype", null=True, blank=True, on_delete=models.PROTECT, related_name="+")),
                ("origin_object_id", models.CharField(max_length=1024, null=True, blank=True)),
                ("occurred_at", models.DateTimeField(default=timezone.now)),
                ("recorded_at", models.DateTimeField(default=timezone.now, editable=False)),
                ("state", models.CharField(max_length=16, choices=(("open", "Open"), ("closed", "Closed")), default="open")),
                ("outcome", models.CharField(max_length=16, choices=(("success", "Success"), ("failure", "Failure")), blank=True)),
                ("hash", models.CharField(max_length=64, blank=True, editable=False)),
                ("counts", models.JSONField(default=dict, blank=True, editable=False)),
                ("touched", models.JSONField(default=list, blank=True, editable=False)),
            ],
            options={"swappable": "ANGEE_CHANGESET_MODEL", "ordering": ("-recorded_at", "-pk")},
        ),
    ]
