"""The retired-column cutovers, from the released schema, with rows, on SQLite."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
from django.apps import apps
from django.conf import settings
from django.db import connection, migrations, models
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.graph import MigrationGraph
from django.db.migrations.state import ModelState, ProjectState

from angee.compose.migrations import RuntimeMigrations
from angee.dashboards.runtime_migrations import retire_creation_keys as dashboards_keys
from angee.intake.runtime_migrations import retire_original_task
from angee.knowledge.runtime_migrations import retire_creation_keys as knowledge_keys
from angee.messaging.runtime_migrations import retire_creation_keys as messaging_keys
from angee.posts.runtime_migrations import single_scope_quota
from angee.projects.runtime_migrations import retire_creation_keys as projects_keys
from angee.proposals.runtime_migrations import retire_clarification_keys
from angee.provenance.runtime_migrations import retire_idempotency_key
from tests.tables import model_tables

CUTOVERS: tuple[tuple[ModuleType, str], ...] = (
    (dashboards_keys, "dashboards"),
    (knowledge_keys, "knowledge"),
    (messaging_keys, "messaging"),
    (projects_keys, "projects"),
    (retire_clarification_keys, "projects"),
    (retire_original_task, "intake"),
    (single_scope_quota, "posts"),
    (retire_idempotency_key, "provenance"),
)
# Dashboards is not composed by the test host; its manifest is read by the stack build.
DECLARING = ("knowledge", "messaging", "projects", "proposals", "intake", "posts", "provenance")
ORIGINS = frozenset({
    "angee.knowledge:retire_creation_keys",
    "angee.messaging:retire_creation_keys",
    "angee.projects:retire_creation_keys",
    "angee.proposals:retire_clarification_keys",
    "angee.intake:retire_original_task",
    "angee.posts:single_scope_quota",
    "angee.provenance:retire_idempotency_key",
})
KEYS = ("client_creation_key", "creation_fingerprint")
CLARIFICATION_KEYS = ("clarification_creation_key", "clarification_creation_fingerprint")
RETIRED = {
    ("dashboards", "dashboard"): KEYS,
    ("knowledge", "vault"): KEYS,
    ("knowledge", "historicalvault"): KEYS,
    ("messaging", "message"): (*KEYS, "creation_actor"),
    ("projects", "task"): (*KEYS, *CLARIFICATION_KEYS),
    ("projects", "historicaltask"): (*KEYS, *CLARIFICATION_KEYS),
    ("projects", "milestone"): KEYS,
    ("intake", "need"): ("original_task",),
    ("posts", "quota"): ("quota_limit",),
    ("provenance", "changeset"): ("idempotency_key",),
}


def _keys() -> list[tuple[str, models.Field]]:
    return [
        ("client_creation_key", models.CharField(max_length=128, null=True, blank=True)),
        ("creation_fingerprint", models.CharField(max_length=64, blank=True)),
    ]


def _clarification_keys() -> list[tuple[str, models.Field]]:
    return [
        ("clarification_creation_key", models.CharField(max_length=255, null=True, blank=True)),
        ("clarification_creation_fingerprint", models.CharField(max_length=64, blank=True, default="")),
    ]


def _keyed(*scope: str, name: str) -> models.UniqueConstraint:
    return models.UniqueConstraint(
        fields=(*scope, "client_creation_key"), condition=models.Q(client_creation_key__isnull=False), name=name,
    )


def _released(
    app_label: str,
    name: str,
    *fields: tuple[str, models.Field],
    constraints: tuple[models.BaseConstraint, ...] = (),
) -> ModelState:
    return ModelState(
        app_label,
        name,
        [("id", models.AutoField(primary_key=True)), *fields],
        options={"db_table": f"retired_{app_label}_{name.lower()}", "constraints": list(constraints)},
    )


def _user(**options: object) -> models.ForeignKey:
    return models.ForeignKey(settings.AUTH_USER_MODEL, **options)


def released_state() -> ProjectState:
    """The released schema of every column and table the retired-column cutovers drop or tighten."""

    user_label, user_name = settings.AUTH_USER_MODEL.split(".")
    task = models.ForeignKey("projects.task", on_delete=models.CASCADE, related_name="+")
    state = ProjectState()
    for model_state in (
        _released(user_label, user_name),
        _released("integrate", "Integration"),
        _released(
            "dashboards", "Dashboard",
            ("owner", _user(on_delete=models.CASCADE, related_name="+")), *_keys(),
            constraints=(_keyed("owner", name="dashboard_owner_creation_key"),),
        ),
        _released(
            "knowledge", "Vault",
            ("created_by", _user(null=True, on_delete=models.SET_NULL, related_name="+")), *_keys(),
            constraints=(_keyed("created_by", name="knowledge_vault_creation_key"),),
        ),
        _released("knowledge", "HistoricalVault", *_keys()),
        _released(
            "messaging", "Message",
            ("creation_actor", models.CharField(max_length=512, null=True, blank=True)), *_keys(),
            constraints=(_keyed("creation_actor", name="messaging_message_creation_key"),),
        ),
        _released("projects", "Project"),
        _released(
            "projects", "Task",
            ("created_by", _user(null=True, on_delete=models.SET_NULL, related_name="+")),
            ("clarification_round", models.IntegerField(null=True)),
            ("clarification_asker", _user(null=True, on_delete=models.SET_NULL, related_name="+")),
            *_keys(),
            *_clarification_keys(),
            constraints=(
                _keyed("created_by", name="projects_task_creation_key"),
                models.UniqueConstraint(
                    fields=("clarification_round", "clarification_asker", "clarification_creation_key"),
                    condition=models.Q(clarification_creation_key__isnull=False),
                    name="uq_proposals_clarification_creation_key",
                ),
            ),
        ),
        _released("projects", "HistoricalTask", *_keys(), *_clarification_keys()),
        _released(
            "projects", "Milestone",
            ("project", models.ForeignKey("projects.project", on_delete=models.CASCADE, related_name="+")), *_keys(),
            constraints=(_keyed("project", name="projects_milestone_creation_key"),),
        ),
        _released(
            "projects", "ProjectSetupReceipt",
            ("actor", _user(on_delete=models.CASCADE, related_name="+")),
            ("task", task),
            ("project", models.ForeignKey("projects.project", on_delete=models.CASCADE, related_name="+")),
            *_keys(),
            constraints=(_keyed("actor", "task", name="projects_projectsetupreceipt_creation_key"),),
        ),
        _released(
            "intake", "Need",
            (
                "original_task",
                models.ForeignKey("projects.task", null=True, on_delete=models.SET_NULL, related_name="+"),
            ),
        ),
        _released(
            "posts", "Quota",
            (
                "integration",
                models.ForeignKey("integrate.integration", null=True, on_delete=models.SET_NULL, related_name="quotas"),
            ),
            ("scope", models.CharField(max_length=512, null=True, blank=True)),
            ("period_start", models.DateTimeField()),
            ("quota_limit", models.PositiveIntegerField(default=10000)),
            constraints=(
                models.UniqueConstraint(
                    fields=("integration", "period_start"),
                    condition=models.Q(scope__isnull=True),
                    name="uq_quota_integration_period",
                ),
                models.UniqueConstraint(
                    fields=("scope", "period_start"),
                    condition=models.Q(scope__isnull=False),
                    name="uq_quota_scope_period",
                ),
            ),
        ),
        _released("provenance", "Changeset", ("idempotency_key", models.CharField(max_length=255, blank=True))),
    ):
        state.add_model(model_state)
    return state


def _upgraded(state: ProjectState) -> ProjectState:
    for module, app_label in CUTOVERS:
        state = module.Migration(f"0002_{module.__name__.rsplit('.', 1)[-1]}", app_label).mutate_state(state)
    return state


def test_each_guard_selects_its_cutover_from_the_released_schema_only() -> None:
    released = released_state()
    upgraded = _upgraded(released)
    for module, _app_label in CUTOVERS:
        assert module.applies(released), module.__name__
        assert not module.applies(upgraded), module.__name__
        assert not module.applies(ProjectState()), module.__name__


def test_manifests_schedule_every_cutover_once(tmp_path: Path) -> None:
    cutovers = RuntimeMigrations(
        (apps.get_app_config(label) for label in DECLARING), runtime_dir=tmp_path, labels=DECLARING,
    )
    scheduled = cutovers.schedule(released_state(), only=ORIGINS)
    assert {entry.origin for entry in scheduled} == ORIGINS
    assert {entry.round for entry in scheduled} == {0}
    assert cutovers.schedule(_upgraded(released_state()), only=ORIGINS) == ()


def test_cutovers_land_on_the_current_schema() -> None:
    """Every retired column and the receipts table are gone; the quota matches its current definition."""

    upgraded = _upgraded(released_state())
    for (app_label, model_name), names in RETIRED.items():
        assert not set(names) & set(upgraded.models[app_label, model_name].fields), (app_label, model_name)
    assert ("projects", "projectsetupreceipt") not in upgraded.models

    quota = apps.get_model("posts", "Quota")
    current = upgraded.clone()
    for name in ("integration", "scope"):
        current.alter_field("posts", "quota", name, quota._meta.get_field(name).clone(), preserve_default=True)
    graph = MigrationGraph()
    graph.add_node(("posts", "0001_initial"), migrations.Migration("0001_initial", "posts"))
    assert MigrationAutodetector(upgraded, current).changes(graph=graph) == {}
    assert [constraint.deconstruct() for constraint in upgraded.models["posts", "quota"].options["constraints"]] == [
        constraint.deconstruct() for constraint in quota._meta.constraints
    ]


@contextmanager
def _dropped_by_cutover(model: type[models.Model]) -> Iterator[None]:
    with connection.schema_editor() as editor:
        editor.create_model(model)
    try:
        yield
    finally:
        if model._meta.db_table in connection.introspection.table_names():
            with connection.schema_editor() as editor:
                editor.delete_model(model)


def _columns(table: str) -> set[str]:
    with connection.cursor() as cursor:
        return {column.name for column in connection.introspection.get_table_description(cursor, table)}


@pytest.mark.django_db(transaction=True)
def test_populated_columns_retire_and_quotas_keep_one_mode() -> None:
    """Every keyed row survives without its key; only quota rows the new shape cannot hold are deleted."""

    state = released_state()
    receipt = state.apps.get_model("projects", "ProjectSetupReceipt")
    rendered = tuple(
        model for model in (state.apps.get_model(*key) for key in state.models) if model is not receipt
    )
    # The cutover drops the receipts table itself, so the test owns only its creation.
    with model_tables(rendered), _dropped_by_cutover(receipt):
        get = state.apps.get_model
        user = get(settings.AUTH_USER_MODEL)._base_manager.create()
        integration = get("integrate", "Integration")._base_manager.create()
        project = get("projects", "Project")._base_manager.create()
        key = {"client_creation_key": "key", "creation_fingerprint": "f" * 64}
        get("dashboards", "Dashboard")._base_manager.create(owner=user, **key)
        get("knowledge", "Vault")._base_manager.create(created_by=user, **key)
        get("knowledge", "HistoricalVault")._base_manager.create(**key)
        get("messaging", "Message")._base_manager.create(creation_actor="auth/user:1", **key)
        task = get("projects", "Task")._base_manager.create(
            created_by=user, clarification_creation_key="question", clarification_creation_fingerprint="q", **key,
        )
        get("projects", "HistoricalTask")._base_manager.create(clarification_creation_key="question", **key)
        get("projects", "Milestone")._base_manager.create(project=project, **key)
        get("projects", "ProjectSetupReceipt")._base_manager.create(actor=user, task=task, project=project, **key)
        get("intake", "Need")._base_manager.create(original_task=task)
        get("provenance", "Changeset")._base_manager.create(idempotency_key="retry")
        at = datetime(2026, 10, 1, tzinfo=UTC)
        quotas = get("posts", "Quota")._base_manager
        kept = quotas.create(integration=integration, scope="feed:search", period_start=at)
        quotas.create(integration=integration, scope=None, period_start=at, quota_limit=500)
        quotas.create(integration=None, scope="feed:list", period_start=at)

        with connection.schema_editor() as editor:
            for module, app_label in CUTOVERS:
                name = f"0002_{module.__name__.rsplit('.', 1)[-1]}"
                state = module.Migration(name, app_label).apply(state, editor)

        for (app_label, model_name), names in RETIRED.items():
            model = state.apps.get_model(app_label, model_name)
            assert not set(names) & _columns(model._meta.db_table), model
            assert model._base_manager.exists(), model
        assert receipt._meta.db_table not in connection.introspection.table_names()
        assert list(state.apps.get_model("posts", "Quota")._base_manager.values_list("pk", "scope")) == [
            (kept.pk, "feed:search"),
        ]
