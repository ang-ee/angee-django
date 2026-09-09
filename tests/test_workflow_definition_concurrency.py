"""PostgreSQL serialization contracts for workflow-definition writers."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import Barrier, Event
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from django.apps import AppConfig
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections
from rebac import system_context

from angee.resources.managers import ResourceManager
from angee.resources.models import Resource
from angee.workflows.definitions import DefinitionEdit, StaleDefinitionError
from tests.conftest import write_addon_manifest
from tests.workflows import WORKFLOW_DEFINITION_MODELS, Step, Workflow, workflow_table_setup

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL row-lock contract"),
]


class ConcurrencyResourceLedger(Resource):
    class Meta(Resource.Meta):
        abstract = False
        app_label = "resources"
        db_table = "test_workflow_concurrency_resource"


@pytest.fixture()
def concurrency_resource_tables(transactional_db: Any) -> Any:
    del transactional_db
    with workflow_table_setup((*WORKFLOW_DEFINITION_MODELS, ConcurrencyResourceLedger)):
        yield


def _thread(call: Any) -> Any:
    close_old_connections()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET lock_timeout TO '5s'")
        with system_context(reason="test workflow definition concurrency"):
            return call()
    finally:
        connections.close_all()


def _ready_draft(name: str) -> tuple[Workflow, Step]:
    workflow = Workflow.objects.create(name=name)
    step = Step.objects.create(
        workflow=workflow,
        key="wait",
        name="Wait",
        step_class="wait",
        config={"until": "2030-01-02T03:04:05Z"},
        is_entry=True,
    )
    workflow.refresh_from_db()
    return workflow, step


def _resource_addon(path: Path, *, suffix: str, reverse: bool = False) -> AppConfig:
    resources = path / "resources" / "install"
    resources.mkdir(parents=True)
    (resources / "100_workflows.workflow.yaml").write_text(
        "_meta:\n  model: workflows.Workflow\nrows:\n"
        "  - xref: left\n    fields: {key: left, name: Left}\n"
        "  - xref: right\n    fields: {key: right, name: Right}\n"
    )
    rows = [("left-step", "left"), ("right-step", "right")]
    if reverse:
        rows.reverse()
    body = "_meta:\n  model: workflows.Step\nrows:\n" + "".join(
        f"  - xref: {xref}\n    fields:\n      workflow: race.{workflow}\n"
        f"      key: wait\n      name: {workflow.title()} {suffix}\n"
        "      step_class: wait\n      config: {until: '2030-01-02T03:04:05Z'}\n      is_entry: true\n"
        for xref, workflow in rows
    )
    (resources / "101_workflows.step.yaml").write_text(body)
    module = ModuleType("race")
    module.__file__ = str(path / "__init__.py")
    config = AppConfig("race", module)
    write_addon_manifest(
        config,
        resources={
            "master": (),
            "install": (
                {"path": "resources/install/100_workflows.workflow.yaml", "adopt": "key"},
                {
                    "path": "resources/install/101_workflows.step.yaml",
                    "depends_on": "resources/install/100_workflows.workflow.yaml",
                },
            ),
            "demo": (),
        },
    )
    return config


def test_publication_and_child_mutation_serialize_to_one_revision_snapshot(
    workflow_tables: None,
) -> None:
    del workflow_tables
    with system_context(reason="prepare publication race"):
        draft, step = _ready_draft("Publication race")
        source_revision = draft.draft_revision
    locked = Event()
    mutation_started = Event()

    def publish() -> int:
        with Workflow.objects._definition_write((draft.pk,)):
            locked.set()
            assert mutation_started.wait(5)
            return Workflow.objects.get(pk=draft.pk).publish().pk

    def mutate() -> None:
        assert locked.wait(5)
        mutation_started.set()
        current = Step.objects.get(pk=step.pk)
        current.name = "Changed after snapshot"
        current.save(update_fields={"name"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        published_future = pool.submit(_thread, publish)
        mutation_future = pool.submit(_thread, mutate)
        published_id = published_future.result(timeout=10)
        mutation_future.result(timeout=10)

    with system_context(reason="verify publication race"):
        published = Workflow.objects.get(pk=published_id)
        draft.refresh_from_db()
        assert published.steps.get().name == "Wait"
        assert published.draft_revision == source_revision
        assert draft.draft_revision == source_revision + 1


def test_duplicate_publish_if_changed_serializes_to_one_snapshot(workflow_tables: None) -> None:
    del workflow_tables
    with system_context(reason="prepare duplicate publication"):
        draft, _step = _ready_draft("Duplicate publication")
    start = Barrier(2)

    def publish() -> int | None:
        start.wait(timeout=5)
        row = Workflow.objects.get(pk=draft.pk)
        published = row.publish_if_changed()
        return None if published is None else published.pk

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (pool.submit(_thread, publish), pool.submit(_thread, publish))
        results = [future.result(timeout=10) for future in futures]

    assert sum(result is not None for result in results) == 1
    with system_context(reason="verify duplicate publication"):
        assert Workflow.objects.filter(published_from_id=draft.pk).count() == 1


def test_definition_cas_loses_cleanly_to_a_locked_legacy_write(workflow_tables: None) -> None:
    del workflow_tables
    with system_context(reason="prepare definition CAS race"):
        draft, step = _ready_draft("CAS race")
        revision = draft.draft_revision
    legacy_locked = Event()
    cas_started = Event()

    def legacy_write() -> None:
        with Workflow.objects._definition_write((draft.pk,)):
            legacy_locked.set()
            assert cas_started.wait(5)
            current = Step.objects.get(pk=step.pk)
            current.name = "Legacy winner"
            current.save(update_fields={"name"})

    def cas_write() -> None:
        assert legacy_locked.wait(5)
        cas_started.set()
        Workflow.objects.apply_definition(
            Workflow.objects.get(pk=draft.pk),
            expected_revision=revision,
            edit=DefinitionEdit(workflow={"description": "Stale command"}),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        legacy_future = pool.submit(_thread, legacy_write)
        cas_future = pool.submit(_thread, cas_write)
        legacy_future.result(timeout=10)
        with pytest.raises(StaleDefinitionError):
            cas_future.result(timeout=10)

    with system_context(reason="verify definition CAS race"):
        draft.refresh_from_db()
        step.refresh_from_db()
        assert draft.draft_revision == revision + 1
        assert draft.description == ""
        assert step.name == "Legacy winner"


def test_step_move_to_immutable_parent_is_rejected_without_touching_old_parent(
    workflow_tables: None,
) -> None:
    del workflow_tables
    with system_context(reason="prepare immutable move"):
        old, step = _ready_draft("Old parent")
        target, _target_step = _ready_draft("Target parent")
        published = target.publish()
        old_revision = old.draft_revision
        published_revision = published.draft_revision

    def move() -> None:
        current = Step.objects.get(pk=step.pk)
        current.workflow = Workflow.objects.get(pk=published.pk)
        current.save(update_fields={"workflow"})

    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(ValidationError, match="immutable"):
            pool.submit(_thread, move).result(timeout=10)

    with system_context(reason="verify immutable move"):
        step.refresh_from_db()
        old.refresh_from_db()
        published.refresh_from_db()
        assert step.workflow_id == old.pk
        assert old.draft_revision == old_revision
        assert published.draft_revision == published_revision


def test_old_parent_delete_serializes_against_a_concurrent_move(workflow_tables: None) -> None:
    del workflow_tables
    with system_context(reason="prepare move delete race"):
        old, step = _ready_draft("Delete parent")
        new = Workflow.objects.create(name="Move parent")
        old_revision = old.draft_revision
        new_revision = new.draft_revision
    locked = Event()
    move_started = Event()

    def delete() -> None:
        with Workflow.objects._definition_write((old.pk,)):
            locked.set()
            assert move_started.wait(5)
            Step.objects.get(pk=step.pk).delete()

    def move() -> None:
        assert locked.wait(5)
        current = Step.objects.get(pk=step.pk)
        move_started.set()
        current.workflow = Workflow.objects.get(pk=new.pk)
        current.save(update_fields={"workflow"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        delete_future = pool.submit(_thread, delete)
        move_future = pool.submit(_thread, move)
        delete_future.result(timeout=10)
        with pytest.raises(ValidationError):
            move_future.result(timeout=10)

    with system_context(reason="verify move delete race"):
        old.refresh_from_db()
        new.refresh_from_db()
        assert not Step.objects.filter(pk=step.pk).exists()
        assert old.draft_revision == old_revision + 1
        assert new.draft_revision == new_revision


def test_resource_preparation_merges_opposite_lineage_orders_before_writes(workflow_tables: None) -> None:
    del workflow_tables
    with system_context(reason="prepare resource lock order"):
        left = Workflow.objects.create(name="Left")
        right = Workflow.objects.create(name="Right")
    start = Barrier(2)

    def prepare(rows: tuple[Workflow, Workflow]) -> tuple[int, ...]:
        resource = SimpleNamespace(
            related_instances=lambda _dataset, _field: rows,
            instance_for_xref=lambda _xref: None,
        )
        group = SimpleNamespace(model=Step, dataset={"_xref": ("a", "b")})
        plans = ResourceManager._write_preparations([(group, resource)])
        assert len(plans) == 1
        start.wait(timeout=5)
        with ExitStack() as stack:
            for plan in plans:
                stack.enter_context(plan.owner.prepare_resource_writes(plan.targets))
            return tuple(sorted(plans[0].targets))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (
            pool.submit(_thread, lambda: prepare((left, right))),
            pool.submit(_thread, lambda: prepare((right, left))),
        )
        results = [future.result(timeout=10) for future in futures]

    assert results == [(left.pk, right.pk), (left.pk, right.pk)]


def test_native_resource_loaders_serialize_opposite_lineage_orders(
    concurrency_resource_tables: None,
    tmp_path: Path,
) -> None:
    del concurrency_resource_tables
    initial = _resource_addon(tmp_path / "initial", suffix="Initial")
    ConcurrencyResourceLedger.objects.load_addons((initial,), tiers=[Resource.Tier.INSTALL])
    left_first = _resource_addon(tmp_path / "left-first", suffix="Alpha")
    right_first = _resource_addon(tmp_path / "right-first", suffix="Beta", reverse=True)
    start = Barrier(2)

    def load(owner: AppConfig) -> None:
        start.wait(timeout=5)
        ConcurrencyResourceLedger.objects.load_addons((owner,), tiers=[Resource.Tier.INSTALL])

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (pool.submit(_thread, lambda: load(left_first)), pool.submit(_thread, lambda: load(right_first)))
        for future in futures:
            future.result(timeout=15)

    with system_context(reason="verify resource lock order"):
        rows = list(Step.objects.order_by("workflow__key").values_list("name", flat=True))
        revisions = list(Workflow.objects.order_by("key").values_list("draft_revision", flat=True))
    assert rows in [["Left Alpha", "Right Alpha"], ["Left Beta", "Right Beta"]]
    assert revisions == [3, 3]


def test_delete_rechecks_parent_when_a_move_commits_after_its_initial_read(
    workflow_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_tables
    with system_context(reason="prepare inverse move delete race"):
        old, step = _ready_draft("Observed old parent")
        new = Workflow.objects.create(name="Committed new parent")
        old_revision = old.draft_revision
        new_revision = new.draft_revision
    manager_type = type(Workflow.objects)
    original = manager_type._definition_write
    parent_observed = Event()
    move_committed = Event()
    pause_first = [True]

    @contextmanager
    def gated_write(manager: Any, workflow_ids: Any, **kwargs: Any) -> Any:
        if pause_first[0]:
            pause_first[0] = False
            parent_observed.set()
            assert move_committed.wait(5)
        with original(manager, workflow_ids, **kwargs) as rows:
            yield rows

    monkeypatch.setattr(manager_type, "_definition_write", gated_write)

    def delete() -> None:
        Step.objects.get(pk=step.pk).delete()

    def move() -> None:
        assert parent_observed.wait(5)
        current = Step.objects.get(pk=step.pk)
        current.workflow = Workflow.objects.get(pk=new.pk)
        current.save(update_fields={"workflow"})
        move_committed.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        delete_future = pool.submit(_thread, delete)
        move_future = pool.submit(_thread, move)
        move_future.result(timeout=10)
        with pytest.raises(ValidationError):
            delete_future.result(timeout=10)

    with system_context(reason="verify inverse move delete race"):
        step.refresh_from_db()
        old.refresh_from_db()
        new.refresh_from_db()
        assert step.workflow_id == new.pk
        assert old.draft_revision == old_revision + 1
        assert new.draft_revision == new_revision + 1
