"""Turn released record activities into tasks about their records.

Released histories keep scheduled and logged activities in messaging's
``ThreadActivity``, typed by messaging's ``ActivityType`` catalog. The catalog
moves to projects with its keys and primary keys, so public ids survive, and each
activity becomes a projects ``Task`` about the same record (its chatter
attachment): the summary is the title, the assignee keeps it, to-do is open, done
keeps its completion time and canceled is dropped as obsolete. Tasks promoted from
an activity keep their row and lose the provenance column. Resource ledgers that
name the catalog follow it, so seeds keep their rows. Messaging's two models are
retired here, after the copy, so no autodetected deletion can run first.
"""

from django.core.management.color import no_style
from django.db import migrations, models
from django.db.migrations.state import ModelState

RANK_STEP = 1024.0


def applies(state):
    activity = state.models.get(("messaging", "threadactivity"))
    task = state.models.get(("projects", "task"))
    if activity is None or task is None:
        return False
    # messaging's activity_type_key turns the stored key into a catalog reference first.
    return isinstance(activity.fields["activity_type"], models.ForeignKey) and "subject" not in task.fields


class MoveActivityTypes(migrations.operations.base.Operation):
    """Recreate messaging's activity-type catalog in projects, rows and keys included."""

    reduces_to_sql = False
    reversible = False

    def state_forwards(self, app_label, state):
        source = state.models["messaging", "activitytype"]
        options = {key: value for key, value in source.options.items() if key != "db_table"}
        state.add_model(ModelState(
            app_label, "ActivityType", [(name, field.clone()) for name, field in source.fields.items()],
            options=options, bases=source.bases, managers=source.managers,
        ))

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        source = from_state.apps.get_model("messaging", "ActivityType")
        target = to_state.apps.get_model(app_label, "ActivityType")
        schema_editor.create_model(target)
        quote = schema_editor.quote_name
        columns = ", ".join(quote(field.column) for field in target._meta.local_concrete_fields)
        schema_editor.execute(
            f"INSERT INTO {quote(target._meta.db_table)} ({columns}) "
            f"SELECT {columns} FROM {quote(source._meta.db_table)}"
        )
        for statement in schema_editor.connection.ops.sequence_reset_sql(no_style(), [target]):
            schema_editor.execute(statement)

    def describe(self):
        return "Move the activity-type catalog to projects"


def convert_activities(apps, schema_editor):
    alias = schema_editor.connection.alias
    activities = apps.get_model("messaging", "ThreadActivity")._base_manager.using(alias)
    tasks = apps.get_model("projects", "Task")._base_manager.using(alias)
    top_rank = tasks.filter(project__isnull=True).aggregate(rank=models.Max("sort_order"))["rank"] or 0.0
    top_sub_rank = tasks.filter(parent__isnull=True).aggregate(rank=models.Max("sub_sort_order"))["rank"] or 0.0
    for index, row in enumerate(activities.order_by("pk").iterator(chunk_size=500), start=1):
        status, done_at, dropped_at, dropped_reason = "open", None, None, None
        if row.status == "done":
            status, done_at = "done", row.completed_at or row.updated_at
        elif row.status == "canceled":
            status, dropped_at, dropped_reason = "dropped", row.completed_at or row.updated_at, "obsolete"
        task = tasks.create(
            title=row.summary[:240],
            note="\n\n".join(text for text in (row.note, row.feedback) if text),
            assignee_id=row.user_id,
            due_date=row.due_date,
            status=status,
            done_at=done_at,
            dropped_at=dropped_at,
            dropped_reason=dropped_reason,
            subject_id=row.attachment_id,
            activity_type_id=row.activity_type_id,
            owner_id=row.created_by_id,
            created_by_id=row.created_by_id,
            updated_by_id=row.updated_by_id,
            sort_order=top_rank + index * RANK_STEP,
            sub_sort_order=top_sub_rank + index * RANK_STEP,
        )
        tasks.filter(pk=task.pk).update(created_at=row.created_at, updated_at=row.updated_at)
    if "converted_from_activity" in {field.name for field in tasks.model._meta.get_fields()}:
        tasks.filter(converted_from_activity__isnull=False).update(converted_from_activity=None)
    if schema_editor.connection.vendor == "postgresql":
        # Fire the deferred foreign-key checks these rows queued: PostgreSQL refuses
        # to alter projects_task below while trigger events are pending.
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def retarget_ledgers(apps, schema_editor):
    """Point seeded catalog rows' ledgers at projects; the rows keep their keys and public ids."""

    try:
        ledger = apps.get_model("resources", "Resource")
    except LookupError:
        return
    ledger._base_manager.using(schema_editor.connection.alias).filter(
        target_model__iexact="messaging.activitytype",
    ).update(target_model="projects.ActivityType")


class RemoveConstraintIfPresent(migrations.RemoveConstraint):
    """Drop the activity provenance constraint only where the history has it."""

    def _present(self, app_label, state):
        options = state.models[app_label, self.model_name_lower].options
        return any(constraint.name == self.name for constraint in options.get("constraints", ()))

    def state_forwards(self, app_label, state):
        if self._present(app_label, state):
            super().state_forwards(app_label, state)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if self._present(app_label, from_state):
            super().database_forwards(app_label, schema_editor, from_state, to_state)


class RemoveFieldIfPresent(migrations.RemoveField):
    """Drop the activity provenance column only where the history has the model and the column."""

    def _present(self, app_label, state):
        model = state.models.get((app_label, self.model_name_lower))
        return model is not None and self.name in model.fields

    def state_forwards(self, app_label, state):
        if self._present(app_label, state):
            super().state_forwards(app_label, state)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if self._present(app_label, from_state):
            super().database_forwards(app_label, schema_editor, from_state, to_state)


class RetireMessagingActivities(migrations.operations.base.Operation):
    """Drop messaging's activities and their catalog once projects holds them."""

    reduces_to_sql = False
    reversible = False

    RETIRED = (("messaging", "threadactivity"), ("messaging", "activitytype"))

    def state_forwards(self, app_label, state):
        for (label, name), model in state.models.items():
            if (label, name) in self.RETIRED:
                continue
            for field_name, field in model.fields.items():
                target = getattr(getattr(field, "remote_field", None), "model", None)
                if isinstance(target, str) and tuple(target.lower().split(".", 1)) in self.RETIRED:
                    raise ValueError(f"{label}.{name}.{field_name} still references {target}; retire it first.")
        for label, name in self.RETIRED:
            state.remove_model(label, name)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        schema_editor.delete_model(from_state.apps.get_model("messaging", "ThreadActivity"))
        schema_editor.delete_model(from_state.apps.get_model("messaging", "ActivityType"))

    def describe(self):
        return "Retire messaging's activities"


class Migration(migrations.Migration):
    dependencies = [("projects", "__latest__"), ("messaging", "__latest__"), ("resources", "__latest__")]
    operations = [
        MoveActivityTypes(),
        migrations.AddField(
            "task", "subject",
            models.ForeignKey(
                blank=True, null=True, on_delete=models.deletion.CASCADE, related_name="subject_tasks",
                to="messaging.threadattachment",
                help_text="The record this task is about, through its chatter attachment.",
            ),
        ),
        migrations.AddField(
            "task", "activity_type",
            models.ForeignKey(
                blank=True, null=True, on_delete=models.deletion.PROTECT, related_name="tasks",
                to="projects.activitytype", to_field="key",
            ),
        ),
        migrations.RunPython(convert_activities, migrations.RunPython.noop),
        migrations.RunPython(retarget_ledgers, migrations.RunPython.noop),
        RemoveConstraintIfPresent("task", "uq_projects_task_converted_activity"),
        RemoveFieldIfPresent("task", "converted_from_activity"),
        RemoveFieldIfPresent("historicaltask", "converted_from_activity"),
        RetireMessagingActivities(),
    ]
