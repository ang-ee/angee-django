"""Upgrade released schema shapes through the runtime migrations that retire them."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from django.db import connection, models
from django.db.migrations.state import ModelState, ProjectState

from angee.intake.runtime_migrations import single_target
from angee.knowledge.runtime_migrations import markdown_page_child
from angee.workflows.runtime_migrations import independent_decisions, retire_pre_rebuild


def _key() -> tuple[str, models.Field]:
    return ("id", models.BigAutoField(primary_key=True))


def _add(state: ProjectState, app_label: str, name: str, fields: list, **options) -> None:
    options = {"db_table": f"test_released_{name.lower()}", **options}
    state.add_model(ModelState(app_label, name, fields, options=options))


def _columns(table: str) -> set[str]:
    with connection.cursor() as cursor:
        return {column.name for column in connection.introspection.get_table_description(cursor, table)}


@contextmanager
def _tables(state: ProjectState, app_label: str) -> Iterator[None]:
    """Drop whatever released or upgraded test tables remain afterwards."""

    try:
        yield
    finally:
        names = {model._meta.db_table for model in state.apps.get_app_config(app_label).get_models()}
        with connection.cursor() as cursor:
            for table in sorted(names | {f"{name}_child" for name in names}):
                cursor.execute(f"DROP TABLE IF EXISTS {connection.ops.quote_name(table)}")


def _pre_rebuild_workflows() -> ProjectState:
    state = ProjectState()
    _add(state, "workflows", "Workflow", [_key(), ("name", models.CharField(max_length=40))])
    _add(state, "workflows", "Step", [_key(), ("workflow", models.ForeignKey("workflows.workflow", models.CASCADE))])
    _add(state, "workflows", "StepRun", [_key(), ("step", models.ForeignKey("workflows.step", models.CASCADE))])
    return state


@pytest.mark.django_db(transaction=True)
def test_pre_rebuild_workflows_history_is_dropped_before_the_current_schema() -> None:
    released = _pre_rebuild_workflows()
    assert retire_pre_rebuild.applies(released)
    # Its run rows carry no decision link, yet it is retired whole rather than cut over.
    assert not independent_decisions.applies(released)
    rebuilt = released.clone()
    _add(rebuilt, "workflows", "WorkflowVersion", [_key()])
    assert not retire_pre_rebuild.applies(rebuilt)

    migration = retire_pre_rebuild.Migration("0099_retire_pre_rebuild", "workflows")
    with _tables(released, "workflows"):
        with connection.schema_editor() as editor:
            for model in released.apps.get_app_config("workflows").get_models():
                editor.create_model(model)
            upgraded = migration.apply(released, editor)

        assert not [key for key in upgraded.models if key[0] == "workflows"]
        assert not retire_pre_rebuild.applies(upgraded)
        assert not {"test_released_workflow", "test_released_step", "test_released_steprun"} & set(
            connection.introspection.table_names()
        )


def _released_knowledge() -> ProjectState:
    state = ProjectState()
    kind = ("kind", models.CharField(max_length=32, default="note"))
    _add(state, "knowledge", "Page", [
        _key(), ("title", models.CharField(max_length=80)), kind, ("created_at", models.DateTimeField(null=True)),
    ], constraints=[models.CheckConstraint(
        condition=models.Q(kind__in=("note", "folder", "template")), name="ck_test_released_page_kind",
    )])
    _add(state, "knowledge", "HistoricalPage", [
        ("history_id", models.BigAutoField(primary_key=True)), ("id", models.BigIntegerField(db_index=True)), kind,
    ])
    _add(state, "knowledge", "MarkdownPage", [
        _key(),
        ("page", models.OneToOneField("knowledge.page", models.CASCADE, related_name="markdown")),
        ("body", models.TextField(blank=True)),
        ("created_at", models.DateTimeField(null=True)),
    ])
    return state


@pytest.mark.django_db(transaction=True)
def test_released_markdown_rows_become_page_children_keyed_by_their_page() -> None:
    released = _released_knowledge()
    assert markdown_page_child.applies(released)
    partial = released.clone()
    partial.models["knowledge", "markdownpage"].fields["page_ptr"] = models.BigIntegerField()
    with pytest.raises(ValueError, match="partial page-child transition"):
        markdown_page_child.applies(partial)

    registry = released.apps
    page_model = registry.get_model("knowledge", "Page")
    markdown_model = registry.get_model("knowledge", "MarkdownPage")
    migration = markdown_page_child.Migration("0099_markdown_page_child", "knowledge")
    with _tables(released, "knowledge"):
        with connection.schema_editor() as editor:
            for model in registry.get_app_config("knowledge").get_models():
                editor.create_model(model)
            page_model.objects.create(title="Guides", kind="folder")
            note = page_model.objects.create(title="Welcome", kind="note")
            template = page_model.objects.create(title="Handout", kind="template")
            # Markdown keys follow creation order, not their pages' keys. Bulk rows skip the
            # current backlink receiver, which matches historical models by label.
            markdown_model.objects.bulk_create([
                markdown_model(page=template, body="Template body"), markdown_model(page=note, body="Note body"),
            ])
            upgraded = migration.apply(released, editor)

        child = upgraded.models["knowledge", "markdownpage"]
        assert child.bases == ("knowledge.page",)
        assert set(child.fields) == {"page_ptr", "body", "kind"}
        assert "kind" not in upgraded.models["knowledge", "page"].fields
        assert not upgraded.models["knowledge", "page"].options["constraints"]
        assert not markdown_page_child.applies(upgraded)
        rows = upgraded.apps.get_model("knowledge", "MarkdownPage")._base_manager.order_by("pk")
        assert [(row.pk, row.title, row.kind, row.body) for row in rows] == [
            (note.pk, "Welcome", "note", "Note body"),
            (template.pk, "Handout", "template", "Template body"),
        ]
        assert "kind" not in _columns("test_released_page")
        assert "kind" not in _columns("test_released_historicalpage")
        assert _columns("test_released_markdownpage") == {"page_ptr_id", "body", "kind"}


@pytest.mark.django_db(transaction=True)
def test_a_note_without_markdown_stops_the_page_child_upgrade() -> None:
    released = _released_knowledge()
    page_model = released.apps.get_model("knowledge", "Page")
    migration = markdown_page_child.Migration("0099_markdown_page_child", "knowledge")
    with _tables(released, "knowledge"):
        with pytest.raises(ValueError, match="has no markdown row"), connection.schema_editor() as editor:
            for model in released.apps.get_app_config("knowledge").get_models():
                editor.create_model(model)
            page_model.objects.create(title="Empty", kind="note")
            migration.apply(released, editor)


def _released_intake(*, constrained: bool = True) -> ProjectState:
    state = ProjectState()
    constraints = [models.CheckConstraint(
        condition=models.Q(targets_project=True, project__isnull=False, task__isnull=True)
        | models.Q(targets_project=False, task__isnull=False),
        name="ck_intake_need_exactly_one_target",
    )] if constrained else []
    _add(state, "intake", "Need", [
        _key(),
        ("targets_project", models.BooleanField(default=False)),
        ("project", models.BigIntegerField(null=True)),
        ("task", models.BigIntegerField(null=True)),
    ], constraints=constraints)
    return state


@pytest.mark.django_db(transaction=True)
def test_a_released_need_keeps_only_the_target_its_flag_names() -> None:
    released = _released_intake()
    assert single_target.applies(released)
    need_model = released.apps.get_model("intake", "Need")
    migration = single_target.Migration("0099_single_target", "intake")
    with _tables(released, "intake"):
        with connection.schema_editor() as editor:
            editor.create_model(need_model)
            project_request = need_model.objects.create(targets_project=True, project=7)
            task_request = need_model.objects.create(task=3)
            copied = need_model.objects.create(task=4, project=7)
            upgraded = migration.apply(released, editor)

        assert not single_target.applies(upgraded)
        assert not upgraded.models["intake", "need"].options["constraints"]
        assert "targets_project" not in _columns("test_released_need")
        rows = upgraded.apps.get_model("intake", "Need")._base_manager.order_by("pk")
        assert [(row.pk, row.project, row.task) for row in rows] == [
            (project_request.pk, 7, None), (task_request.pk, None, 3), (copied.pk, None, 4),
        ]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(("targets_project", "project", "task"), [(True, None, None), (True, 7, 3), (False, 7, None)])
def test_a_need_its_flag_cannot_explain_stops_the_single_target_upgrade(
    targets_project: bool, project: int | None, task: int | None,
) -> None:
    released = _released_intake(constrained=False)
    need_model = released.apps.get_model("intake", "Need")
    with _tables(released, "intake"):
        with connection.schema_editor() as editor:
            editor.create_model(need_model)
            need_model.objects.create(targets_project=targets_project, project=project, task=task)
            with pytest.raises(ValueError, match="request"):
                single_target.single_target(released.apps, editor)
