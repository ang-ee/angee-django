"""Make a handle's role in a thread one fact before the constraint that says so.

A participant row without a message states a handle's role in a thread. Merges now
collapse a moved duplicate of that fact (``Participant.merge_identity``), so the
fact needs its unique constraint. Rows written before it may already repeat; this
keeps the oldest row of each (thread, handle, role) without a message, deletes the
copies (they state nothing the kept row does not), then adds the constraint.
Reverse drops the constraint; the deleted copies do not come back.
"""

from django.db import migrations, models

CONSTRAINT = "uq_participant_thread_handle_role"


def applies(state):
    model = state.models.get(("messaging", "participant"))
    if model is None or "thread" not in model.fields or "message" not in model.fields:
        return False
    return all(constraint.name != CONSTRAINT for constraint in model.options.get("constraints", ()))


def collapse_repeated_facts(apps, schema_editor):
    participants = apps.get_model("messaging", "Participant")._base_manager.using(schema_editor.connection.alias)
    repeated = (
        participants.filter(message__isnull=True)
        .values("thread_id", "handle_id", "role")
        .annotate(kept=models.Min("pk"), count=models.Count("pk"))
        .filter(count__gt=1)
        .order_by()
    )
    removed = 0
    for fact in repeated.iterator():
        removed += participants.filter(
            message__isnull=True, thread_id=fact["thread_id"], handle_id=fact["handle_id"], role=fact["role"],
        ).exclude(pk=fact["kept"]).order_by().delete()[0]
    if removed:
        print(f"Participant thread identity: {removed} repeated thread participations collapsed.")


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.RunPython(collapse_repeated_facts, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="participant",
            constraint=models.UniqueConstraint(
                fields=("thread", "handle", "role"),
                condition=models.Q(message__isnull=True),
                name=CONSTRAINT,
            ),
        ),
    ]
