"""Retire mailbox cursor copies only after current streams cover their progress."""

from django.db import migrations, models, router


def applies(state):
    model = state.models.get(("messaging", "channel"))
    if model is None or "cursor" not in model.fields:
        return False
    if not isinstance(model.fields["cursor"], models.JSONField):
        raise ValueError("Unexpected legacy messaging cursor field.")
    return True


def forwards(apps, schema_editor):
    channel = apps.get_model("messaging", "Channel")
    stream = apps.get_model("integrate", "SyncStream")
    alias = schema_editor.connection.alias
    if not router.allow_migrate_model(alias, channel):
        return
    for pk, cursor in channel._base_manager.using(alias).exclude(cursor={}).values_list("pk", "cursor"):
        if not isinstance(cursor, dict) or set(cursor) != {"mailboxes"} or not isinstance(cursor["mailboxes"], dict):
            raise ValueError(f"Channel {pk} has unsupported retained cursor state.")
        for mailbox, previous in cursor["mailboxes"].items():
            if not isinstance(previous, dict) or set(previous) != {"last_uid", "uidvalidity"}:
                raise ValueError(f"Channel {pk} has an unsupported mailbox cursor.")
            if any(type(previous[key]) is not int or previous[key] < 0 for key in previous):
                raise ValueError(f"Channel {pk} has invalid mailbox progress.")
            rows = stream._base_manager.using(alias).filter(integration_id=pk, key="messages", partition=mailbox)
            generation = rows.order_by("-generation").values_list("generation", flat=True).first()
            current = rows.filter(generation=generation, state="open").values_list("cursor", flat=True)
            if not any(
                isinstance(value, dict)
                and value.get("uidvalidity") == previous["uidvalidity"]
                and type(value.get("last_uid")) is int
                and value["last_uid"] >= previous["last_uid"]
                for value in current
            ):
                raise ValueError(f"Channel {pk} has mailbox progress not covered by its current stream.")


class Migration(migrations.Migration):
    dependencies = [("messaging", "__latest__"), ("integrate", "__latest__")]
    operations = [
        migrations.RunPython(forwards),
        migrations.RemoveField(model_name="channel", name="cursor"),
    ]
