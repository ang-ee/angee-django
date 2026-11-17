"""The stage primitive's shared behaviour: applicability, admission, projection, provisioning."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, ClassVar

import pytest
from django.core.exceptions import ValidationError
from django.db import models
from rebac import system_context

from angee.base.models.stages import Stage, StagedModelMixin
from tests.tables import model_tables


class PipelineContainer(models.Model):
    """A container with the optional explicit default the primitive reads."""

    default_stage = models.ForeignKey("tests.PipelineStage", null=True, on_delete=models.SET_NULL)

    class Meta:
        app_label = "tests"


class PipelineStage(Stage):
    """A stage that may apply to records of one kind only and reserves ``Closed`` for a verb."""

    container_field_name = "container"
    container = models.ForeignKey(PipelineContainer, on_delete=models.CASCADE)
    only_kind = models.CharField(max_length=20, blank=True)

    admissions: ClassVar[list[dict[str, Any]]] = []

    class Meta:
        app_label = "tests"
        constraints = [models.UniqueConstraint(
            fields=("container", "position"), name="pipeline_stage_container_position",
        )]

    @classmethod
    def for_record(cls, record: StagedModelMixin[Any]) -> models.QuerySet[PipelineStage]:
        if getattr(record, "every_kind", False):
            return super().for_record(record)
        kind = getattr(record, "kind", "")
        return super().for_record(record).filter(models.Q(only_kind="") | models.Q(only_kind=kind))

    @classmethod
    def validate_transition(
        cls,
        previous: PipelineStage | None,
        following: PipelineStage | None,
        *,
        manual: bool,
        admitted: bool,
        adding: bool,
    ) -> None:
        cls.admissions.append({
            "previous": previous.name if previous is not None else None,
            "following": following.name if following is not None else None,
            "manual": manual,
            "admitted": admitted,
            "adding": adding,
        })
        if following is not None and following.name == "Closed" and not admitted:
            raise ValidationError({"stage": "Use close to enter Closed."})


class PipelineRecord(StagedModelMixin[PipelineStage], models.Model):
    """A record whose kind decides which stages apply to it; its note decides nothing."""

    stage_container_field_name = "container"
    stage_scope_fields = ("kind",)
    container = models.ForeignKey(PipelineContainer, on_delete=models.CASCADE)
    stage = models.ForeignKey(PipelineStage, null=True, on_delete=models.SET_NULL)
    kind = models.CharField(max_length=20, default="task")
    note = models.CharField(max_length=40, blank=True)

    every_kind: bool = False
    """An instance fact, not a column: this write may rest in a stage of any kind."""

    class Meta:
        app_label = "tests"

    def carry_instance_facts(self, stored: PipelineRecord) -> None:
        stored.every_kind = self.every_kind


@pytest.fixture
def pipeline_tables(transactional_db: None) -> Iterator[None]:
    PipelineStage.admissions.clear()
    with model_tables((PipelineContainer, PipelineStage, PipelineRecord)):
        yield


@pytest.fixture
def pipeline(pipeline_tables: None) -> tuple[PipelineContainer, dict[str, PipelineStage]]:
    container = PipelineContainer.objects.create()
    stages = {
        "Bugs first": PipelineStage.objects.create(container=container, name="Bugs first", position=1, only_kind="bug"),
        "New": PipelineStage.objects.create(container=container, name="New", position=2),
        "Doing": PipelineStage.objects.create(container=container, name="Doing", position=3, fold=False),
        "Closed": PipelineStage.objects.create(
            container=container,
            name="Closed",
            position=4,
            fold=True,
            rotting_days=14,
            description="Nothing left to do.",
        ),
    }
    return container, stages


def test_stage_carries_its_board_hints(pipeline: Any) -> None:
    _container, stages = pipeline
    closed = PipelineStage.objects.get(pk=stages["Closed"].pk)
    assert (closed.fold, closed.rotting_days, closed.description) == (True, 14, "Nothing left to do.")
    assert (stages["Doing"].fold, stages["Doing"].rotting_days, stages["Doing"].description) == (False, None, "")


def test_applicability_narrows_the_default_and_the_scope_check(pipeline: Any) -> None:
    container, stages = pipeline
    task = PipelineRecord(container=container, kind="task")
    bug = PipelineRecord(container=container, kind="bug")
    assert task.resolve_default_stage() == stages["New"]
    assert bug.resolve_default_stage() == stages["Bugs first"]

    task.stage = stages["Bugs first"]
    with pytest.raises(ValidationError, match="does not apply to this pipeline record"):
        task.validate_stage_scope()
    foreign = PipelineStage.objects.create(container=PipelineContainer.objects.create(), name="Elsewhere")
    task.stage = foreign
    with pytest.raises(ValidationError, match="record's container"):
        task.validate_stage_scope()


def test_an_explicit_default_the_record_cannot_hold_falls_back(pipeline: Any) -> None:
    container, stages = pipeline
    PipelineContainer.objects.filter(pk=container.pk).update(default_stage=stages["Bugs first"])
    container.refresh_from_db()
    assert PipelineRecord(container=container, kind="bug").resolve_default_stage() == stages["Bugs first"]
    assert PipelineRecord(container=container, kind="task").resolve_default_stage() == stages["New"]


def test_stage_entered_at_follows_moves_only(pipeline: Any) -> None:
    container, stages = pipeline
    record = PipelineRecord.objects.create(container=container, stage=stages["New"])
    entered = record.stage_entered_at
    assert entered is not None

    record.kind = "chore"
    record.save()
    record.refresh_from_db()
    assert record.stage_entered_at == entered

    record.stage = stages["Doing"]
    record.save(update_fields=("stage",))
    record.refresh_from_db()
    assert record.stage == stages["Doing"]
    assert record.stage_entered_at is not None and record.stage_entered_at > entered

    moved = record.stage_entered_at
    record.stage = stages["New"]
    record.save(update_fields=("kind",))
    record.refresh_from_db()
    assert (record.stage, record.stage_entered_at) == (stages["Doing"], moved), "an omitted stage is not a move"

    record.stage = None
    record.save()
    record.refresh_from_db()
    assert (record.stage, record.stage_entered_at) == (None, None)


def test_a_partial_save_judges_the_stage_binding_it_stores(pipeline: Any) -> None:
    container, stages = pipeline
    other = PipelineContainer.objects.create()
    elsewhere = PipelineStage.objects.create(container=other, name="Elsewhere", position=1)
    record = PipelineRecord.objects.create(container=container, stage=stages["New"])
    stale = PipelineRecord.objects.get(pk=record.pk)
    record.container, record.stage = other, elsewhere
    record.save(update_fields=("container", "stage"))

    stale.kind = "chore"
    stale.save(update_fields=("kind",))
    stored = PipelineRecord.objects.get(pk=record.pk)
    assert (stored.container_id, stored.stage_id, stored.kind) == (other.pk, elsewhere.pk, "chore")

    with pytest.raises(ValidationError, match="record's container"):
        stale.save(update_fields=("stage",))
    assert PipelineRecord.objects.get(pk=record.pk).stage_id == elsewhere.pk, "the stale container admits nothing"


def test_a_partial_save_judges_applicability_on_the_row_it_stores(pipeline: Any) -> None:
    container, stages = pipeline
    record = PipelineRecord.objects.create(container=container, kind="task", stage=stages["New"])
    stale = PipelineRecord.objects.get(pk=record.pk)
    record.kind, record.stage = "bug", stages["Bugs first"]
    record.save()
    stale.stage = stages["Bugs first"]
    stale.save(update_fields=("stage",))
    stored = PipelineRecord.objects.get(pk=record.pk)
    assert (stored.kind, stored.stage) == ("bug", stages["Bugs first"]), "the stored kind admits the stage"

    record = PipelineRecord.objects.create(container=container, kind="bug", stage=stages["Bugs first"])
    stale = PipelineRecord.objects.get(pk=record.pk)
    record.kind, record.stage = "task", stages["New"]
    record.save()
    stale.stage = stages["Bugs first"]
    with pytest.raises(ValidationError, match="does not apply"):
        stale.save(update_fields=("stage",))
    stored = PipelineRecord.objects.get(pk=record.pk)
    assert (stored.kind, stored.stage) == ("task", stages["New"]), "a stale kind admits nothing"


def test_a_narrowed_stage_leaves_its_resting_records_editable(pipeline: Any) -> None:
    """Scope is judged again only when a write changes the stage, the container or a scope field."""

    container, stages = pipeline
    record = PipelineRecord.objects.create(container=container, kind="task", stage=stages["Doing"])
    PipelineStage.objects.filter(pk=stages["Doing"].pk).update(only_kind="bug")

    record.note = "Edited while resting"
    record.full_clean()
    record.save()
    record.note = "Edited in part"
    record.save(update_fields=("note",))
    assert PipelineRecord.objects.get(pk=record.pk).note == "Edited in part"

    record.kind = "chore"
    with pytest.raises(ValidationError, match="does not apply to this pipeline record"):
        record.full_clean()
    with pytest.raises(ValidationError, match="does not apply to this pipeline record"):
        record.save(update_fields=("kind",))
    assert PipelineRecord.objects.get(pk=record.pk).kind == "task", "a changed scope field is judged"
    record.kind = "bug"
    record.save()
    assert PipelineRecord.objects.get(pk=record.pk).kind == "bug"

    other = PipelineRecord.objects.create(container=container, kind="task", stage=stages["New"])
    other.stage = stages["Doing"]
    with pytest.raises(ValidationError, match="does not apply to this pipeline record"):
        other.save()


def test_a_record_admitted_into_a_reserved_stage_stays_editable(pipeline: Any) -> None:
    """A write that keeps the stage is no move, so the stage owner is not asked to admit it again."""

    container, stages = pipeline
    record = PipelineRecord.objects.create(container=container, stage=stages["New"])
    record.stage = stages["Closed"]
    record.save(admitted_stage=stages["Closed"])
    admitted = len(PipelineStage.admissions)

    record.note = "Closed, then annotated"
    record.full_clean()
    record.save()
    record.save(update_fields=("note",))
    assert record.stage_transition(manual=True) == (stages["Closed"], stages["Closed"])
    assert len(PipelineStage.admissions) == admitted, "keeping the stage asks for no admission"
    stored = PipelineRecord.objects.get(pk=record.pk)
    assert (stored.stage, stored.note) == (stages["Closed"], "Closed, then annotated")

    record.stage = stages["Doing"]
    record.save()
    assert PipelineStage.admissions[-1] == {
        "previous": "Closed", "following": "Doing", "manual": False, "admitted": False, "adding": False,
    }


def test_a_partial_save_carries_instance_facts_into_the_row_it_stores(pipeline: Any) -> None:
    """Scope and applicability judge the snapshot with the facts the instance holds outside its columns."""

    container, stages = pipeline
    record = PipelineRecord.objects.create(container=container, kind="task", stage=stages["New"])
    stale = PipelineRecord.objects.get(pk=record.pk)
    stale.stage = stages["Bugs first"]
    with pytest.raises(ValidationError, match="does not apply"):
        stale.save(update_fields=("stage",))
    stale.every_kind = True
    stale.save(update_fields=("stage",))
    assert PipelineRecord.objects.get(pk=record.pk).stage_id == stages["Bugs first"].pk


def test_the_stage_owner_admits_every_move_with_its_facts(pipeline: Any) -> None:
    container, stages = pipeline
    record = PipelineRecord.objects.create(container=container, stage=stages["New"])
    assert PipelineStage.admissions[-1] == {
        "previous": None, "following": "New", "manual": False, "admitted": False, "adding": True,
    }

    record.stage = stages["Closed"]
    with pytest.raises(ValidationError, match="Use close to enter Closed"):
        record.save()
    assert PipelineStage.admissions[-1] == {
        "previous": "New", "following": "Closed", "manual": False, "admitted": False, "adding": False,
    }
    with pytest.raises(ValidationError, match="Use close to enter Closed"):
        record.stage_transition(manual=True)
    assert PipelineStage.admissions[-1]["manual"] is True

    with pytest.raises(ValidationError, match="admitted by its owning verb"):
        record.save(admitted_stage=stages["Doing"])
    record.save(admitted_stage=stages["Closed"])
    record.refresh_from_db()
    assert record.stage == stages["Closed"]
    assert PipelineStage.admissions[-1]["admitted"] is True


def test_a_trusted_system_save_skips_admission_but_a_hand_move_never_does(pipeline: Any) -> None:
    container, stages = pipeline
    record = PipelineRecord.objects.create(container=container, stage=stages["New"])
    admitted = len(PipelineStage.admissions)
    record.stage = stages["Closed"]
    with system_context(reason="tests.stage_primitive.system_move"):
        record.save()
        assert len(PipelineStage.admissions) == admitted, "a sudo system write is trusted"
        record.stage = stages["New"]
        record.save()
        record.stage = stages["Closed"]
        with pytest.raises(ValidationError, match="Use close to enter Closed"):
            record.stage_transition(manual=True)
    assert PipelineRecord.objects.get(pk=record.pk).stage == stages["New"]


def test_clean_admits_projects_and_scopes_the_pending_move(pipeline: Any) -> None:
    container, stages = pipeline
    record = PipelineRecord.objects.create(container=container, stage=stages["New"])
    entered = record.stage_entered_at
    record.stage = stages["Closed"]
    with pytest.raises(ValidationError, match="Use close to enter Closed"):
        record.clean()
    assert record.stage_entered_at == entered, "a refused move projects nothing"

    record.stage = stages["Doing"]
    record.clean()
    assert record.stage_entered_at is not None and record.stage_entered_at > entered, "clean projects the move"
    assert PipelineRecord.objects.get(pk=record.pk).stage_entered_at == entered, "projection waits for the save"

    record.stage = stages["Bugs first"]
    with pytest.raises(ValidationError, match="does not apply to this pipeline record"):
        record.clean()


def test_provision_creates_once_and_keeps_existing_rows(pipeline_tables: None) -> None:
    container = PipelineContainer.objects.create()
    rows = (
        {"name": "Inbox", "position": 10, "tone": "warning"},
        {"name": "Doing", "position": 20},
        {"name": "Done", "fold": True},
    )
    first = PipelineStage.provision(container, rows)
    assert [stage.name for stage in PipelineStage.for_container(container)] == ["Inbox", "Doing", "Done"]
    assert first["Done"].fold is True and first["Done"].position > first["Doing"].position

    PipelineStage.objects.filter(pk=first["Inbox"].pk).update(tone="danger")
    again = PipelineStage.provision(container, rows)
    assert {key: stage.pk for key, stage in again.items()} == {key: stage.pk for key, stage in first.items()}
    assert again["Inbox"].tone == "danger", "provisioning never rewrites an existing stage"
    assert PipelineStage.for_container(PipelineContainer.objects.create()).count() == 0
