"""Legacy stage ties upgrade before the new contextual uniqueness constraint."""

import pytest
from django.db import IntegrityError, connection, models, transaction
from django.db.migrations.state import ModelState, ProjectState

from angee.work.compose_migrations.work.stage_fractional_position import Migration, applies


@pytest.mark.django_db(transaction=True)
def test_work_stage_transition_preserves_legacy_rows_and_orders_ties():
    state = ProjectState()
    state.add_model(ModelState("work", "Queue", [("id", models.AutoField(primary_key=True))]))
    state.add_model(ModelState("work", "Stage", [
        ("id", models.AutoField(primary_key=True)),
        ("queue", models.ForeignKey("work.Queue", on_delete=models.CASCADE)),
        ("position", models.PositiveIntegerField(default=0)),
        ("name", models.CharField(max_length=80)),
    ], options={"db_table": "test_legacy_work_stage"}))
    # Use a distinct queue table so the probe does not touch the source host's owner.
    state.models[("work", "queue")].options["db_table"] = "test_legacy_work_queue"
    queue = state.apps.get_model("work", "Queue")
    stage = state.apps.get_model("work", "Stage")
    with connection.schema_editor() as editor:
        editor.create_model(queue)
        editor.create_model(stage)
    try:
        first_queue = queue.objects.create()
        second_queue = queue.objects.create()
        rows = [stage.objects.create(queue=first_queue, name="First"),
                stage.objects.create(queue=first_queue, name="Second"),
                stage.objects.create(queue=second_queue, name="Other")]
        assert applies(state)
        with connection.schema_editor() as editor:
            final = Migration("stage_fractional_position", "work").apply(state, editor)
        assert not applies(final)
        upgraded = final.apps.get_model("work", "Stage")
        assert list(upgraded.objects.order_by("pk").values_list("pk", "name", "position")) == [
            (rows[0].pk, "First", 1024), (rows[1].pk, "Second", 2048), (rows[2].pk, "Other", 1024),
        ]
        with pytest.raises(IntegrityError), transaction.atomic():
            upgraded.objects.create(queue_id=first_queue.pk, name="Collision", position=1024)
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(final.apps.get_model("work", "Stage") if "final" in locals() else stage)
            editor.delete_model(queue)
