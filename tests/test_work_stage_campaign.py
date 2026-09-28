"""Authored stage sets and hand transitions preserve queue-owned invariants."""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from rebac import actor_context, system_context

from tests.work_campaign import HAND_VERBS, CreateTask, Queue, Stage, hand_context, invoke, make_task, persisted_state
from tests.work_campaign import productivity_create_case as productivity_create_case
from tests.work_campaign import work_case as work_case


@pytest.mark.parametrize("provision", [False, True])
@pytest.mark.parametrize("elevated", [False, True])
def test_stage_provisioning_is_insert_only_and_always_allocates_numbers(work_case, provision, elevated):
    actor, _ = work_case
    with hand_context(actor, elevated):
        queue = Queue.objects.create(key="AUTHORED", slug="authored", name="Authored", provision_stages=provision)
        assert Stage._base_manager.filter(queue=queue).count() == (7 if provision else 0)
        first, second = make_task(queue, category=None), make_task(queue, category=None)
        assert (first.number, second.number) == (1, 2)
        queue.provision_stages = not provision
        with pytest.raises(ValidationError, match="provision_stages"):
            queue.save(update_fields=["provision_stages"])
        queue.refresh_from_db()
        assert queue.provision_stages is provision


def test_authored_stages_keep_seed_names_and_ordered_unstarted_fallback(work_case):
    actor, _ = work_case
    with system_context(reason="test.work.authored_stages"):
        queue = Queue.objects.create(key="SEEDED", slug="seeded", name="Seeded", provision_stages=False)
        Stage.objects.create(queue=queue, name="Incoming", category="triage", position=1)
        Stage.objects.create(queue=queue, name="Automatic", category="unstarted", position=2, rule_owned=True)
        later = Stage.objects.create(queue=queue, name="Later", category="unstarted", position=20)
        first = Stage.objects.create(queue=queue, name="Ready", category="unstarted", position=10)
    with actor_context(actor):
        assert Stage.resolve_default(queue).pk == first.pk
        queue.default_stage = later
        queue.save(update_fields=["default_stage"])
        assert Stage.resolve_default(queue).pk == later.pk
        assert Stage.objects.get(queue=queue, category="triage").name == "Incoming"
        assert CreateTask.objects.create(title="Authored default", queue=queue).stage_id == later.pk


@pytest.mark.parametrize("category", ["triage", "duplicate"])
def test_duplicate_system_categories_are_refused_even_for_seed_writes(work_case, category):
    _, queue = work_case
    with system_context(reason="test.work.duplicate_stage"), transaction.atomic():
        with pytest.raises(IntegrityError), transaction.atomic():
            Stage.objects.create(queue=queue, name="Second reserved", category=category)
    assert Stage._base_manager.filter(queue=queue, category=category).count() == 1


@pytest.mark.parametrize("verb", HAND_VERBS)
def test_every_hand_verb_fails_explicitly_on_a_queue_without_stages(work_case, verb):
    actor, _ = work_case
    with system_context(reason="test.work.empty_queue"):
        queue = Queue.objects.create(
            key="EMPTY", slug="empty", name="Empty", provision_stages=False, triage_enabled=True,
        )
        source, canonical = make_task(queue, category=None), make_task(queue, category=None)
    before = persisted_state(source)
    with actor_context(actor), pytest.raises(ValidationError, match="Queue has no"):
        invoke(source, verb, canonical=canonical)
    assert persisted_state(source) == before


@pytest.mark.parametrize("kind", ["foreign", "triage", "duplicate", "rule_owned"])
@pytest.mark.parametrize("method", ["clean", "save"])
def test_queue_rejects_foreign_or_reserved_default_stage(work_case, kind, method):
    actor, queue = work_case
    with system_context(reason="test.work.invalid_default"):
        if kind == "foreign":
            other = Queue.objects.create(key="FOREIGN", slug="foreign", name="Foreign")
            stage = other.default_stage
        elif kind == "rule_owned":
            stage = Stage.objects.create(queue=queue, name="Automatic", rule_owned=True)
        else:
            stage = Stage._base_manager.get(queue=queue, category=kind)
    original = queue.default_stage_id
    queue.default_stage = stage
    with actor_context(actor), pytest.raises(ValidationError, match="Default stage"):
        getattr(queue, method)()
    assert Queue._base_manager.get(pk=queue.pk).default_stage_id == original


@pytest.mark.parametrize("category", ["backlog", "unstarted", "started", "completed", "canceled", "duplicate"])
def test_return_to_triage_projects_lifecycle_and_replay_preserves_timestamps(work_case, category):
    actor, queue = work_case
    task = make_task(queue, category=category)
    with actor_context(actor):
        assert task.return_to_triage() is task
        task.refresh_from_db()
        assert task.stage.category == "triage"
        assert (task.status, task.done_at, task.dropped_at, task.dropped_reason) == ("open", None, None, None)
        assert task.started_triage_at is not None
        assert task.triaged_at is None
        before = persisted_state(task)
        task.return_to_triage()
        assert persisted_state(task) == before
        task.accept()
        assert task.triaged_at is not None


@pytest.mark.parametrize("queue_state", ["absent", "disabled", "missing_triage"])
def test_return_to_triage_requires_enabled_queue_and_triage_stage(work_case, queue_state):
    actor, queue = work_case
    with system_context(reason="test.work.triage_unavailable"):
        if queue_state == "disabled":
            queue.triage_enabled = False
            queue.save(update_fields=["triage_enabled"])
        elif queue_state == "missing_triage":
            Stage.objects.filter(queue=queue, category="triage").delete()
        task = make_task(None if queue_state == "absent" else queue, category=None)
    before = persisted_state(task)
    with actor_context(actor), pytest.raises(ValidationError, match="triage"):
        task.return_to_triage()
    assert persisted_state(task) == before


@pytest.mark.parametrize("adding", [False, True])
def test_direct_triage_entry_names_the_owning_verb(work_case, adding):
    actor, queue = work_case
    task = CreateTask(title="New", queue=queue) if adding else make_task(queue)
    task.stage = Stage._base_manager.get(queue=queue, category="triage")
    diagnostic = "Use capture" if adding else "Use return_to_triage"
    with actor_context(actor), pytest.raises(ValidationError, match=diagnostic):
        task.save()


@pytest.mark.parametrize("category", ["backlog", "unstarted"])
def test_start_selects_first_eligible_started_stage_by_position(work_case, category):
    actor, queue = work_case
    with system_context(reason="test.work.start_candidates"):
        Stage.objects.create(queue=queue, name="Automatic", category="started", rule_owned=True, position=1)
        first = Stage.objects.create(queue=queue, name="Earlier manual", category="started", position=2)
    task = make_task(queue, category=category)
    with actor_context(actor):
        assert task.start() is task
    task.refresh_from_db()
    assert task.stage_id == first.pk
    assert task.status == "open"


def test_start_in_second_started_stage_is_noop(work_case):
    actor, queue = work_case
    with system_context(reason="test.work.second_started"):
        second = Stage.objects.create(queue=queue, name="Second started", category="started", position=80)
        task = CreateTask.objects.create(title="Already working", queue=queue, stage=second)
    before = persisted_state(task)
    with actor_context(actor):
        assert task.start() is task
    assert persisted_state(task) == before


@pytest.mark.parametrize("category", ["triage", "completed", "canceled", "duplicate"])
def test_start_refuses_triage_and_closed_categories(work_case, category):
    actor, queue = work_case
    task = make_task(queue, category=category)
    before = persisted_state(task)
    with actor_context(actor), pytest.raises(ValidationError, match="Accept|Reopen"):
        task.start()
    assert persisted_state(task) == before


def test_start_without_queue_is_noop(work_case):
    actor, _ = work_case
    task = make_task(None, category=None)
    before = persisted_state(task)
    with actor_context(actor):
        assert task.start() is task
    assert persisted_state(task) == before


@pytest.mark.parametrize("verb", HAND_VERBS)
@pytest.mark.parametrize("elevated", [False, True])
def test_every_hand_verb_refuses_to_leave_rule_owned_stage(work_case, verb, elevated):
    actor, queue = work_case
    task, canonical = make_task(queue, category="backlog"), make_task(queue)
    with system_context(reason="test.work.reserve_source"):
        Stage._base_manager.filter(pk=task.stage_id).update(rule_owned=True)
    before = persisted_state(task)
    with hand_context(actor, elevated), pytest.raises(ValidationError, match="out of a rule-owned stage"):
        invoke(task, verb, canonical=canonical)
    assert persisted_state(task) == before


@pytest.mark.parametrize("verb", HAND_VERBS)
@pytest.mark.parametrize("elevated", [False, True])
def test_every_hand_verb_refuses_rule_owned_targets(work_case, verb, elevated):
    actor, queue = work_case
    task, canonical = make_task(queue, category="triage"), make_task(queue)
    if verb in {"start", "return_to_triage"}:
        task = make_task(queue, category="backlog")
    target_category = {
        "start": "started", "complete": "completed", "reopen": "unstarted", "accept": "unstarted",
        "decline": "canceled", "drop": "canceled", "drop_duplicate": "duplicate",
        "return_to_triage": "triage", "mark_duplicate": "duplicate",
    }[verb]
    # A legacy or concurrently reserved default must still be refused by verbs.
    with system_context(reason="test.work.reserve_target"):
        Stage._base_manager.filter(queue=queue, category=target_category).update(rule_owned=True)
    before = persisted_state(task)
    with hand_context(actor, elevated), pytest.raises(ValidationError, match="rule-owned|Queue has no"):
        invoke(task, verb, canonical=canonical)
    assert persisted_state(task) == before


@pytest.mark.parametrize("verb,category", [("complete", "completed"), ("drop", "canceled")])
def test_category_verbs_skip_rule_owned_stages(work_case, verb, category):
    actor, queue = work_case
    with system_context(reason="test.work.reserved_first"):
        Stage.objects.create(queue=queue, name="Reserved first", category=category, position=1, rule_owned=True)
    expected = Stage._base_manager.get(queue=queue, category=category, rule_owned=False)
    task = make_task(queue)
    with actor_context(actor):
        invoke(task, verb)
    assert task.stage_id == expected.pk


@pytest.mark.parametrize("method", ["clean", "save"])
@pytest.mark.parametrize("elevated", [False, True])
@pytest.mark.parametrize("reservation", ["rule_owned", "triage", "duplicate"])
def test_current_default_cannot_become_entry_reserved(work_case, method, elevated, reservation):
    actor, queue = work_case
    stage = Stage._base_manager.get(pk=queue.default_stage_id)
    if reservation == "rule_owned":
        stage.rule_owned = True
    else:
        stage.category = reservation
    with hand_context(actor, elevated), pytest.raises(ValidationError, match="default stage cannot reserve"):
        getattr(stage, method)()
    stage.refresh_from_db()
    assert (stage.rule_owned, stage.category) == (False, "unstarted")
