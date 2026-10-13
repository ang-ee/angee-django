"""Move record-task subjects off chatter attachments, including retained history.

This source migration runs before schema autodetection. It refuses a historical
attachment pointer that no longer resolves, rather than silently erasing that
history. The prior record_tasks migration is preserved unchanged.
"""

from django.db import migrations, models

MODELS = ("task", "historicaltask")


def applies(state):
    task = state.models.get(("projects", "task"))
    return task is not None and "subject" in task.fields


def subject_fields(history=False):
    return {
        "subject_content_type": models.ForeignKey(
            "contenttypes.ContentType", null=True, blank=True, related_name="+",
            on_delete=models.DO_NOTHING if history else models.PROTECT,
            db_constraint=not history,
        ),
        "subject_object_id": models.PositiveBigIntegerField(null=True, blank=True),
    }


class MoveTaskSubjects(migrations.operations.base.Operation):
    """Preserve each task and version's record identity before dropping its old FK."""

    reduces_to_sql = False
    reversible = False

    def state_forwards(self, app_label, state):
        for name in MODELS:
            model = state.models.get((app_label, name))
            if model is None or "subject" not in model.fields:
                continue
            for field_name, field in subject_fields(name == "historicaltask").items():
                state.add_field(app_label, name, field_name, field, preserve_default=True)
            indexes = [index for index in model.options.get("indexes", ()) if "subject" not in index.fields]
            state.alter_model_options(app_label, name, {**model.options, "indexes": indexes})
            state.remove_field(app_label, name, "subject")

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        alias = schema_editor.connection.alias
        attachment_model = from_state.apps.get_model("messaging", "ThreadAttachment")
        attachments = attachment_model._base_manager.using(alias)
        for name in MODELS:
            source = from_state.models.get((app_label, name))
            if source is None or "subject" not in source.fields:
                continue
            before = from_state.apps.get_model(app_label, name)
            if not self.allow_migrate_model(alias, before):
                continue
            after = to_state.apps.get_model(app_label, name)
            rows = before._base_manager.using(alias).exclude(subject_id=None).order_by()
            has_subjects = rows.exists()
            if has_subjects:
                if not self.allow_migrate_model(alias, attachment_model):
                    raise RuntimeError("Task subject migration requires its attachment mappings on the same database.")
                missing = rows.exclude(subject_id__in=attachments.values("pk"))
                if missing.exists():
                    raise RuntimeError(
                        f"{app_label}.{name}: a retained subject attachment is missing; "
                        "restore its record mapping before migrating task subjects."
                    )
            # SQLite rebuilds a table for some alterations. Advance the native
            # state after every addition so the next rebuild retains it.
            expanded = from_state.clone()
            for field_name, field in subject_fields(name == "historicaltask").items():
                intermediate = expanded.apps.get_model(app_label, name)
                schema_editor.add_field(intermediate, after._meta.get_field(field_name))
                expanded.add_field(app_label, name, field_name, field, preserve_default=True)
            if has_subjects:
                writable = expanded.apps.get_model(app_label, name)._base_manager.using(alias)
                target = attachments.filter(pk=models.OuterRef("subject_id"))
                writable.exclude(subject_id=None).update(
                    subject_content_type_id=models.Subquery(target.values("content_type_id")[:1]),
                    subject_object_id=models.Subquery(target.values("object_id")[:1]),
                )
            if schema_editor.connection.vendor == "postgresql":
                schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            for index in list(expanded.apps.get_model(app_label, name)._meta.indexes):
                if "subject" in index.fields:
                    schema_editor.remove_index(expanded.apps.get_model(app_label, name), index)
                    expanded.remove_index(app_label, name, index.name)
            intermediate = expanded.apps.get_model(app_label, name)
            schema_editor.remove_field(intermediate, intermediate._meta.get_field("subject"))

    def describe(self):
        return "Move task and task-history subjects to their records"


class Migration(migrations.Migration):
    dependencies = [("projects", "__latest__"), ("messaging", "__latest__")]
    operations = [MoveTaskSubjects()]
