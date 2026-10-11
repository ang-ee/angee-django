"""Add retained link state and its native history without folding the old stores.

Forward adds columns and HistoricalRecordLink; RecordRevision and SyncDiscrepancy
remain untouched until their separate fold and retirement declarations. Existing
links receive empty state, not invented observations or application evidence.

Reverse removes the new columns and history table, losing any observations,
application snapshots, attribution and inbound/outbox evidence written there.
The old stores are preserved, but do not contain every newly captured link write.
"""

import simple_history.models
from django.conf import settings
from django.db import migrations, models
from django.db.migrations.state import ProjectState

from angee.base.fields import StateField

LEGACY_FIELDS = {
    "absence_count", "created_at", "created_by", "external_key", "id",
    "last_seen_at", "last_verified_generation", "local_base_hash", "metadata",
    "origin", "parent", "remote_base_hash", "remote_version", "status", "stream",
    "target_content_type", "target_object_id", "tombstoned_at", "updated_at", "updated_by",
}
"""The complete pre-history RecordLink schema, after the generic target rename."""


def applies(state: ProjectState) -> bool:
    """Select the exact old shape; reject partial columns or a partial history."""

    link = state.models.get(("integrate", "recordlink"))
    history = state.models.get(("integrate", "historicalrecordlink"))
    if link is None and history is None:
        return False
    added = {
        operation.name for operation in Migration.operations
        if isinstance(operation, migrations.AddField)
    }
    history_fields = {
        name for operation in Migration.operations
        if isinstance(operation, migrations.CreateModel)
        for name, _field in operation.fields
    }
    if link is not None:
        if set(link.fields) == LEGACY_FIELDS and history is None:
            return True
        if added <= link.fields.keys() and history is not None and history_fields <= history.fields.keys():
            base = link.fields["applied_base"]
            if (
                isinstance(base, models.ForeignKey)
                and base.remote_field.model == "integrate.historicalrecordlink"
                and base.remote_field.on_delete is models.PROTECT
                and base.db_constraint
                and base.null
            ):
                return False
    raise ValueError(
        "RecordLink has an unsupported or partial history/state schema; "
        "complete or reverse its migration history before upgrading."
    )


class Migration(migrations.Migration):
    """Native reversible schema operations; the composer attaches integrate's leaf."""

    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        migrations.swappable_dependency(settings.ANGEE_CHANGESET_MODEL),
    ]
    operations = [
        migrations.AddField("recordlink", "applied_at", models.DateTimeField(blank=True, null=True)),
        migrations.AddField("recordlink", "asserted_by", models.TextField(blank=True, editable=False)),
        migrations.AddField("recordlink", "dependency_digest", models.CharField(blank=True, max_length=64)),
        migrations.AddField("recordlink", "inbound", models.JSONField(blank=True, default=list)),
        migrations.AddField("recordlink", "mapped_payload", models.JSONField(blank=True, default=dict)),
        migrations.AddField("recordlink", "mapping_version", models.PositiveIntegerField(default=1)),
        migrations.AddField("recordlink", "outbox_last_error", models.JSONField(blank=True, default=dict)),
        migrations.AddField("recordlink", "outbox_retry_at", models.DateTimeField(blank=True, null=True)),
        migrations.AddField(
            "recordlink", "outbox_state",
            StateField(
                blank=True, choices=[("pending", "Pending"), ("refused", "Refused"), ("error", "Error")],
                db_index=True, max_length=7, null=True,
            ),
        ),
        migrations.AddField("recordlink", "pinned", models.BooleanField(default=False)),
        migrations.AddField("recordlink", "source_hash", models.CharField(blank=True, max_length=64)),
        migrations.AddField("recordlink", "source_payload", models.JSONField(blank=True, default=dict)),
        migrations.CreateModel(
            name="HistoricalRecordLink",
            fields=[
                ("id", models.BigIntegerField(auto_created=True, blank=True, db_index=True, verbose_name="ID")),
                ("created_at", models.DateTimeField(blank=True, db_index=True, editable=False)),
                ("updated_at", models.DateTimeField(blank=True, db_index=True, editable=False)),
                ("external_key", models.CharField(max_length=512)),
                ("asserted_by", models.TextField(blank=True, editable=False)),
                ("target_object_id", models.CharField(blank=True, max_length=255, null=True)),
                ("status", StateField(
                    choices=[
                        ("current", "Current"), ("observed", "Observed"), ("unavailable", "Unavailable"),
                        ("discrepant", "Discrepant"), ("withdrawn", "Withdrawn"), ("tombstone", "Tombstone"),
                    ],
                    db_index=True, default="observed", max_length=11,
                )),
                ("source_payload", models.JSONField(blank=True, default=dict)),
                ("source_hash", models.CharField(blank=True, max_length=64)),
                ("mapped_payload", models.JSONField(blank=True, default=dict)),
                ("mapping_version", models.PositiveIntegerField(default=1)),
                ("dependency_digest", models.CharField(blank=True, max_length=64)),
                ("applied_at", models.DateTimeField(blank=True, null=True)),
                ("pinned", models.BooleanField(default=False)),
                ("inbound", models.JSONField(blank=True, default=list)),
                ("outbox_state", StateField(
                    blank=True, choices=[("pending", "Pending"), ("refused", "Refused"), ("error", "Error")],
                    db_index=True, max_length=7, null=True,
                )),
                ("outbox_retry_at", models.DateTimeField(blank=True, null=True)),
                ("outbox_last_error", models.JSONField(blank=True, default=dict)),
                ("remote_version", models.CharField(blank=True, max_length=512)),
                ("remote_base_hash", models.CharField(blank=True, max_length=64)),
                ("local_base_hash", models.CharField(blank=True, max_length=64)),
                ("origin", models.CharField(blank=True, max_length=16)),
                ("last_seen_at", models.DateTimeField(blank=True, null=True)),
                ("last_verified_generation", models.PositiveIntegerField(default=0)),
                ("absence_count", models.PositiveIntegerField(default=0)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("tombstoned_at", models.DateTimeField(blank=True, null=True)),
                ("history_id", models.AutoField(primary_key=True, serialize=False)),
                ("history_date", models.DateTimeField(db_index=True)),
                ("history_change_reason", models.TextField(null=True)),
                ("history_type", models.CharField(
                    choices=[("+", "Created"), ("~", "Changed"), ("-", "Deleted")], max_length=1,
                )),
                # Native snapshot FKs do not constrain past relations. The live
                # applied_base FK below protects the current pinned history row.
                ("applied_base", models.ForeignKey(
                    blank=True, db_constraint=False, null=True, on_delete=models.DO_NOTHING,
                    related_name="+", to="integrate.historicalrecordlink",
                )),
                ("changeset", models.ForeignKey(
                    blank=True, null=True, on_delete=models.PROTECT, related_name="+", to=settings.ANGEE_CHANGESET_MODEL,
                )),
                ("created_by", models.ForeignKey(
                    blank=True, db_constraint=False, null=True, on_delete=models.DO_NOTHING,
                    related_name="+", to=settings.AUTH_USER_MODEL,
                )),
                ("history_user", models.ForeignKey(
                    null=True, on_delete=models.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL,
                )),
                ("parent", models.ForeignKey(
                    blank=True, db_constraint=False, null=True, on_delete=models.DO_NOTHING,
                    related_name="+", to="integrate.recordlink",
                )),
                ("stream", models.ForeignKey(
                    blank=True, db_constraint=False, null=True, on_delete=models.DO_NOTHING,
                    related_name="+", to="integrate.syncstream",
                )),
                ("target_content_type", models.ForeignKey(
                    blank=True, db_constraint=False, null=True, on_delete=models.DO_NOTHING,
                    related_name="+", to="contenttypes.contenttype",
                )),
                ("updated_by", models.ForeignKey(
                    blank=True, db_constraint=False, null=True, on_delete=models.DO_NOTHING,
                    related_name="+", to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "verbose_name": "historical record link",
                "verbose_name_plural": "historical record links",
                "ordering": ("-history_date", "-history_id"),
                "get_latest_by": ("history_date", "history_id"),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.AddField(
            "recordlink", "applied_base",
            models.ForeignKey(
                blank=True, null=True, on_delete=models.PROTECT, related_name="+", to="integrate.historicalrecordlink",
            ),
        ),
    ]
