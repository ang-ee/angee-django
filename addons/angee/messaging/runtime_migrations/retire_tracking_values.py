"""Retain legacy tracked messages as ordinary immutable notifications.

Every column of each TrackingValue row, including audit and identity, survives
in the notification's frozen legacy payload. A plain body part carries its
human-readable rendering. Only then may the old table leave the active schema.
"""

import hashlib
import json
from itertools import groupby

from django.core.serializers.json import DjangoJSONEncoder
from django.db import migrations, router


def applies(state):
    tracking = state.models.get(("messaging", "trackingvalue"))
    if tracking is None:
        return False
    required = {("messaging", name) for name in ("message", "part", "fragment")}
    if not required.issubset(state.models):
        raise ValueError("TrackingValue retirement requires Message, Part and Fragment.")
    return True


def retain_notifications(apps, schema_editor):
    tracking = apps.get_model("messaging", "TrackingValue")
    message = apps.get_model("messaging", "Message")
    part = apps.get_model("messaging", "Part")
    fragment = apps.get_model("messaging", "Fragment")
    alias = schema_editor.connection.alias
    if not tracking._meta.can_migrate(alias) or not router.allow_migrate_model(alias, tracking):
        return
    refused = [
        model._meta.label for model in (message, part, fragment)
        if not model._meta.can_migrate(alias) or not router.allow_migrate_model(alias, model)
    ]
    if refused:
        raise ValueError(
            f"TrackingValue retention requires all related models on database {alias!r}; "
            f"refused: {', '.join(refused)}."
        )
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    rows = tracking._base_manager.using(alias).order_by("message_id", "position", "pk").values()
    for message_id, changes in groupby(rows.iterator(), key=lambda row: row["message_id"]):
        values = json.loads(json.dumps(list(changes), cls=DjangoJSONEncoder))
        row = message._base_manager.using(alias).get(pk=message_id)
        metadata = dict(row.metadata or {})
        if "legacy_tracking" in metadata:
            raise ValueError("Message legacy_tracking is occupied; reconcile before retirement.")
        metadata["legacy_tracking"] = values
        body = "\n".join(f"{value['field_label']}: {value['old_display']} → {value['new_display']}" for value in values)
        retained, _ = fragment._base_manager.using(alias).get_or_create(
            hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
            defaults={"text": body, "kind": "paragraph", "created_by_id": row.created_by_id},
        )
        last = part._base_manager.using(alias).filter(message_id=message_id).order_by("-position").first()
        part._base_manager.using(alias).create(
            message_id=message_id, position=last.position + 1 if last is not None else 0,
            type="text/plain", disposition="inline", role="body", fragment_id=retained.pk,
            created_by_id=row.created_by_id,
        )
        message._base_manager.using(alias).filter(pk=message_id).update(
            message_type="notification", metadata=metadata, preview=row.preview or body[:280],
        )
    message._base_manager.using(alias).filter(message_type="auto_comment").update(message_type="notification")


class Migration(migrations.Migration):
    # RuntimeMigrations.plan appends this app's existing leaf; source declarations
    # deliberately do not name generated migration numbers.
    dependencies = []
    operations = [
        migrations.RunPython(retain_notifications, hints={"model_name": "trackingvalue"}),
        migrations.DeleteModel(name="TrackingValue"),
    ]
