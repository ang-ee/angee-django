"""Consolidate released duplicate relations on Task.

Operational duplicate relations become retained redirects, flattened to their
ultimate survivor. A baseline history row records each converted task without
inventing old changeset attribution. Conflicting redirects, missing endpoints
and cycles refuse the upgrade before the source rows go. Work owns the optional
stage-concealment backfill, ordered after these shared columns.
"""

from django.conf import settings
from django.db import migrations, models
from django.utils import timezone

TASK_MODELS = ("task", "historicaltask")


def applies(state):
    task = state.models.get(("projects", "task"))
    relation = state.models.get(("projects", "taskrelation"))
    if task is None:
        return False
    shared = set(task_fields())
    missing = False
    for name in TASK_MODELS:
        model = state.models.get(("projects", name))
        if model is None:
            continue
        present = shared & model.fields.keys()
        if present and present != shared:
            raise RuntimeError(f"projects.{name} has partial shared identity/trash state; repair it before upgrading.")
        missing = missing or not present
    return missing or (
        relation is not None and any(value == "duplicate" for value, _label in relation.fields["kind"].choices)
    )


def task_fields(history=False):
    return {
        "merged_into": models.ForeignKey(
            "projects.Task",
            null=True,
            blank=True,
            editable=False,
            on_delete=models.DO_NOTHING if history else models.PROTECT,
            related_name="+" if history else "merged_from",
            db_constraint=not history,
        ),
        "is_trashed": models.BooleanField(default=False, db_index=True, editable=False),
        "trashed_at": models.DateTimeField(null=True, blank=True, editable=False),
        "trashed_by": models.ForeignKey(
            settings.AUTH_USER_MODEL,
            null=True,
            blank=True,
            editable=False,
            related_name="+",
            on_delete=models.DO_NOTHING if history else models.SET_NULL,
            db_constraint=not history,
        ),
        "trash_reason": models.TextField(blank=True, default="", editable=False),
    }


def _without_duplicate(field):
    _name, _path, args, kwargs = field.deconstruct()
    kwargs.pop("choices_enum", None)
    kwargs["choices"] = [(value, label) for value, label in field.choices if value != "duplicate"]
    return type(field)(*args, **kwargs)


class ConsolidateTaskIdentity(migrations.operations.base.Operation):
    """Move duplicate identity and removal facts to the shared task columns."""

    reduces_to_sql = False
    reversible = False

    def state_forwards(self, app_label, state):
        for name in TASK_MODELS:
            model = state.models.get((app_label, name))
            if model is None:
                continue
            for field_name, field in task_fields(name == "historicaltask").items():
                if field_name not in model.fields:
                    state.add_field(app_label, name, field_name, field, preserve_default=True)
        relation = state.models.get((app_label, "taskrelation"))
        if relation is not None:
            state.alter_field(
                app_label,
                "taskrelation",
                "kind",
                _without_duplicate(relation.fields["kind"]),
                preserve_default=True,
            )

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        alias = schema_editor.connection.alias
        expanded = from_state.clone()
        for name in TASK_MODELS:
            if (app_label, name) not in expanded.models:
                continue
            after = to_state.apps.get_model(app_label, name)
            if not self.allow_migrate_model(alias, after):
                continue
            for field_name in task_fields(name == "historicaltask"):
                if field_name not in expanded.models[app_label, name].fields:
                    before = expanded.apps.get_model(app_label, name)
                    field = after._meta.get_field(field_name)
                    schema_editor.add_field(before, field)
                    # SQLite may rebuild one table per column; subsequent
                    # additions must retain every column already added.
                    expanded.add_field(app_label, name, field_name, field.clone(), preserve_default=True)
        task_model = to_state.apps.get_model(app_label, "Task")
        if not self.allow_migrate_model(alias, task_model):
            return
        tasks = task_model._base_manager.using(alias)
        redirects = dict(tasks.exclude(merged_into_id=None).order_by().values_list("pk", "merged_into_id"))
        relations = None
        if (app_label, "taskrelation") in from_state.models and self.allow_migrate_model(
            alias, from_state.apps.get_model(app_label, "TaskRelation"),
        ):
            relations = from_state.apps.get_model(app_label, "TaskRelation")._base_manager.using(alias)
            for source, target in (
                relations.filter(kind="duplicate")
                .order_by("pk")
                .values_list(
                    "task_id",
                    "related_task_id",
                )
            ):
                if source in redirects and redirects[source] != target:
                    raise RuntimeError(
                        f"Task {source} has conflicting duplicate survivors; resolve them before upgrading."
                    )
                redirects[source] = target
        endpoints = set(redirects) | set(redirects.values())
        present = set(tasks.filter(pk__in=endpoints).order_by().values_list("pk", flat=True))
        if endpoints - present:
            raise RuntimeError("A duplicate task endpoint is missing; restore it before upgrading.")
        flattened = {}
        for source in sorted(redirects):
            seen, target = {source}, redirects[source]
            while target in redirects:
                if target in seen:
                    raise RuntimeError(f"Task {source} participates in a duplicate cycle; resolve it before upgrading.")
                seen.add(target)
                target = redirects[target]
            flattened[source] = target
        changed = set(flattened)
        now = timezone.now()
        columns = {field.attname for field in tasks.model._meta.concrete_fields}
        for row in tasks.filter(pk__in=changed).order_by("pk").iterator():
            tasks.filter(pk=row.pk).update(
                merged_into_id=flattened[row.pk],
                is_trashed=True,
                trashed_at=row.trashed_at or (row.updated_at if "updated_at" in columns else now),
                trashed_by_id=row.trashed_by_id or (row.updated_by_id if "updated_by_id" in columns else None),
                trash_reason="merge",
            )
        if (app_label, "historicaltask") in to_state.models and self.allow_migrate_model(
            alias, to_state.apps.get_model(app_label, "HistoricalTask"),
        ):
            history_model = to_state.apps.get_model(app_label, "HistoricalTask")
            copied = [
                field.attname
                for field in history_model._meta.concrete_fields
                if not field.name.startswith("history_") and field.name != "changeset" and field.attname in columns
            ]
            for row in tasks.filter(pk__in=changed).order_by("pk").iterator():
                history_model._base_manager.using(alias).create(
                    **{name: getattr(row, name) for name in copied},
                    history_date=now,
                    history_type="~",
                    history_user_id=None,
                    history_change_reason="Consolidated task duplicate identity and removal",
                )
        if relations is not None:
            relations.filter(kind="duplicate").delete()
        if schema_editor.connection.vendor == "postgresql":
            schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")

    def describe(self):
        return "Consolidate task redirects and removal in shared record fields"


class Migration(migrations.Migration):
    dependencies = [("projects", "__latest__")]
    operations = [ConsolidateTaskIdentity()]
