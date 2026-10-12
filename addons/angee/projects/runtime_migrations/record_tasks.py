"""Turn released record activities into record tasks.

Released histories keep scheduled and logged activities in messaging's
``ThreadActivity``, typed by messaging's ``ActivityType`` catalog. The catalog
moves to projects with its keys and primary keys, so its public ids survive, and
each activity becomes a projects ``Task`` about the same record (its chatter
attachment): the summary is the title (a summary longer than a title also opens
the note), note and feedback are the note, the assignee keeps it, the creator owns
it where the history already has task owners, to-do is open, done keeps its
completion time and canceled is dropped as obsolete. Each new task gets a
creation history row dated at the conversion. An activity already promoted to a
task makes that task the record's task instead of a second one (with a change
history row), unless the task is project or queue work: that task keeps its own
access and takes only the activity's type. Activity public ids (``tac_``) are not
mapped: they name rows that no longer exist.

A record task answers to its record alone, so the cutover refuses while any
activity sits on a record type whose owner has not declared ``record_read`` and
``record_write`` on ``messaging/thread_attachment``; it names the types, their
activities and their assignees. An assignee who cannot read the record keeps the
task but no longer sees it: access is evaluated only once the schema is synced.
Activity metadata has no task field, so the cutover refuses while any activity
carries some. Resource ledgers that name the catalog follow it. Messaging's two
models are retired here, after the copy, so no autodetected deletion can run
first.

The cutover is irreversible: it moves rows and drops messaging's tables, holding
the task tables' lock throughout (about 17 s per 20,000 activities on
PostgreSQL). Back the database up and plan a maintenance window.
"""

from django.core.management.color import no_style
from django.db import migrations, models
from django.db.migrations.state import ModelState
from django.utils import timezone

RANK_STEP = 1024.0
BATCH_SIZE = 500
CATALOG_LEDGER = ("angee.messaging", "angee.projects", "resources/master/010_projects.activitytype.yaml")


def applies(state):
    activity = state.models.get(("messaging", "threadactivity"))
    task = state.models.get(("projects", "task"))
    if activity is None or task is None:
        return False
    # messaging's activity_type_key turns the stored key into a catalog reference first.
    if not isinstance(activity.fields["activity_type"], models.ForeignKey):
        return False
    moved = sorted(name for name in ("subject", "activity_type") if name in task.fields)
    if moved or ("projects", "activitytype") in state.models:
        raise RuntimeError(
            "projects:record_tasks found a half-migrated history: messaging still has its activities while "
            f"projects already has {', '.join(moved) or 'its activity-type catalog'}. Restore the database "
            "from the backup taken before the cutover."
        )
    return True


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
    task_model = apps.get_model("projects", "Task")
    tasks = task_model._base_manager.using(alias)
    carrying = [pk for pk, metadata in activities.order_by("pk").values_list("pk", "metadata") if metadata]
    if carrying:
        shown = ", ".join(str(pk) for pk in carrying[:5])
        raise RuntimeError(
            f"{len(carrying)} record activities carry metadata a task has no field for (ids {shown}"
            f"{', …' if len(carrying) > 5 else ''}); export or clear it, then migrate again."
        )
    _refuse_undeclared_record_types(apps, activities)
    field_names = {field.name for field in task_model._meta.get_fields()}
    # Audit, owner and queue columns arrive in different releases: use those this history has.
    columns = {field.attname for field in task_model._meta.concrete_fields}
    promoted, project_work = {}, set()
    if "converted_from_activity" in field_names:
        work = [task_model._meta.get_field(name).attname for name in ("project", "queue") if name in field_names]
        # Released models order by fields this migration's state lacks; never inherit their ordering.
        for activity_id, task_id, *placement in (
            tasks.filter(converted_from_activity__isnull=False).order_by()
            .values_list("converted_from_activity_id", "pk", *work)
        ):
            promoted[activity_id] = task_id
            if any(value is not None for value in placement):
                project_work.add(task_id)
    # The released timestamps are the facts to keep; this migration's model is its own.
    for name in {"created_at", "updated_at"} & columns:
        field = task_model._meta.get_field(name)
        field.auto_now = field.auto_now_add = False
    title_length = task_model._meta.get_field("title").max_length
    top_rank = tasks.filter(project__isnull=True).aggregate(rank=models.Max("sort_order"))["rank"] or 0.0
    top_sub_rank = tasks.filter(parent__isnull=True).aggregate(rank=models.Max("sub_sort_order"))["rank"] or 0.0
    index = 0
    batch = []
    reused = []
    for row in activities.order_by("pk").iterator(chunk_size=BATCH_SIZE):
        if row.pk in promoted:
            task_id = promoted[row.pk]
            if task_id in project_work:
                tasks.filter(pk=task_id).update(activity_type_id=row.activity_type_id)
            else:
                tasks.filter(pk=task_id).update(subject_id=row.attachment_id, activity_type_id=row.activity_type_id)
                reused.append(task_id)
            continue
        index += 1
        status, done_at, dropped_at, dropped_reason = "open", None, None, None
        if row.status == "done":
            status, done_at = "done", row.completed_at or row.updated_at
        elif row.status == "canceled":
            status, dropped_at, dropped_reason = "dropped", row.completed_at or row.updated_at, "obsolete"
        title = row.summary
        if len(title) > title_length:
            title = title[: title_length - 1] + "…"
        opening = row.summary if title != row.summary else ""
        recorded = {
            "owner_id": row.created_by_id, "created_by_id": row.created_by_id, "updated_by_id": row.updated_by_id,
            "created_at": row.created_at, "updated_at": row.updated_at,
        }
        batch.append(task_model(
            title=title,
            note="\n\n".join(text for text in (opening, row.note, row.feedback) if text),
            assignee_id=row.user_id,
            due_date=row.due_date,
            status=status,
            done_at=done_at,
            dropped_at=dropped_at,
            dropped_reason=dropped_reason,
            subject_id=row.attachment_id,
            activity_type_id=row.activity_type_id,
            sort_order=top_rank + index * RANK_STEP,
            sub_sort_order=top_sub_rank + index * RANK_STEP,
            **{name: value for name, value in recorded.items() if name in columns},
        ))
        if len(batch) == BATCH_SIZE:
            _insert(apps, tasks, batch)
            batch = []
    if batch:
        _insert(apps, tasks, batch)
    if reused:
        _record_history(apps, tasks, list(tasks.filter(pk__in=reused).order_by("pk")), "~",
                        "Became its record's task in the activity cutover")
    if project_work:
        print(
            f"  record_tasks: {len(project_work)} task(s) promoted from activities stay project or queue work "
            "(their own access), typed by their activity but not attached to its record."
        )
    if schema_editor.connection.vendor == "postgresql":
        # Fire the deferred foreign-key checks these rows queued: PostgreSQL refuses
        # to alter projects_task below while trigger events are pending.
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def _insert(apps, tasks, batch):
    """Insert one batch of converted tasks and their creation history rows."""

    created = tasks.bulk_create(batch, batch_size=BATCH_SIZE)
    _record_history(apps, tasks, created, "+", "Converted from a record activity")


def _record_history(apps, tasks, rows, history_type, reason):
    """Write one history row per task, as of now: the state the conversion leaves."""

    try:
        history_model = apps.get_model("projects", "HistoricalTask")
    except LookupError:
        return
    if not rows:
        return
    now = timezone.now()
    copied = [
        field.attname for field in history_model._meta.concrete_fields
        if not field.name.startswith("history_") and hasattr(rows[0], field.attname)
    ]
    history_model._base_manager.using(tasks.db).bulk_create(
        [
            history_model(
                **{attname: getattr(task, attname) for attname in copied},
                history_date=now,
                history_type=history_type,
                history_user_id=None,
                history_change_reason=reason,
            )
            for task in rows
        ],
        batch_size=BATCH_SIZE,
    )


def _refuse_undeclared_record_types(apps, activities):
    """Refuse activities on record types whose readers no declared record arm reaches.

    The composed ``messaging/thread_attachment`` schema on disk (base plus every
    installed extension) names the record types that take tasks; the synced
    store still holds the released schema while this runs.
    """

    from django.apps import apps as installed
    from django.db.models import Count
    from rebac.resources import model_resource_type
    from rebac.schema import PermArrow, PermBinOp

    from angee.base.permissions import effective_rebac_definition

    counts = list(
        activities.order_by()
        .values_list("attachment__content_type__app_label", "attachment__content_type__model")
        .annotate(activities=Count("pk"), assignees=Count("user", distinct=True))
    )
    if not counts:
        return
    definition = effective_rebac_definition(installed.get_model("messaging", "ThreadAttachment"))
    relations = {relation.name: relation for relation in definition.relations} if definition else {}
    permission = next((item for item in definition.permissions if item.name == "record_read"), None) \
        if definition else None
    declared, pending = set(), [permission.expression] if permission else []
    while pending:
        expression = pending.pop()
        if isinstance(expression, PermBinOp):
            pending += [expression.left, expression.right]
        elif isinstance(expression, PermArrow) and expression.via in relations:
            declared.update(subject.type for subject in relations[expression.via].allowed_subjects)
    undeclared = []
    for app_label, model_name, count, assignees in counts:
        try:
            resource_type = model_resource_type(installed.get_model(app_label, model_name))
        except LookupError:
            resource_type = None
        if resource_type not in declared:
            undeclared.append(f"{app_label}.{model_name} ({count} activities, {assignees} assignees)")
    if undeclared:
        raise RuntimeError(
            "record activities sit on record types that take no record tasks: " + "; ".join(undeclared)
            + ". A record task answers to its record alone, so these would reach only administrators. "
            "Declare record_read and record_write for these types on messaging/thread_attachment in their "
            "owner's permissions.extends.zed (or delete the activities), then migrate again."
        )


def retarget_ledgers(apps, schema_editor):
    """Point seeded catalog rows' ledgers at projects; the rows keep their keys and public ids."""

    ledgers = apps.get_model("resources", "Resource")._base_manager.using(schema_editor.connection.alias)
    catalog = ledgers.filter(target_model__iexact="messaging.activitytype")
    retired_addon, owner_addon, owner_path = CATALOG_LEDGER
    # The framework's own catalog rows now come from projects' resource file.
    catalog.filter(source_addon=retired_addon).update(source_addon=owner_addon, source_path=owner_path)
    catalog.update(target_model="projects.ActivityType")


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


class AddFieldIfPresent(migrations.AddField):
    """Mirror a new task column on the history model where the history has one."""

    def _present(self, app_label, state):
        return (app_label, self.model_name_lower) in state.models

    def state_forwards(self, app_label, state):
        if self._present(app_label, state):
            super().state_forwards(app_label, state)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if self._present(app_label, from_state):
            super().database_forwards(app_label, schema_editor, from_state, to_state)


def _history_reference(to, **options):
    """Return the column django-simple-history keeps for a task reference."""

    return models.ForeignKey(
        blank=True, null=True, db_constraint=False, on_delete=models.deletion.DO_NOTHING, related_name="+",
        to=to, **options,
    )


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
        AddFieldIfPresent(
            "historicaltask", "subject",
            _history_reference(
                "messaging.threadattachment",
                help_text="The record this task is about, through its chatter attachment.",
            ),
        ),
        AddFieldIfPresent(
            "historicaltask", "activity_type", _history_reference("projects.activitytype", to_field="key"),
        ),
        migrations.RunPython(convert_activities, migrations.RunPython.noop),
        migrations.RunPython(retarget_ledgers, migrations.RunPython.noop),
        RemoveConstraintIfPresent("task", "uq_projects_task_converted_activity"),
        RemoveFieldIfPresent("task", "converted_from_activity"),
        RemoveFieldIfPresent("historicaltask", "converted_from_activity"),
        RetireMessagingActivities(),
    ]
