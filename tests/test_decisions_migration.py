"""Schema-only concern cutover through Django's historical state owner."""

import pytest
from django.apps import apps
from django.db import migrations, models
from django.db.migrations.state import ModelState, ProjectState

from angee.decisions.runtime_migrations import records_and_proposals as transition
from angee.decisions.runtime_migrations import single_questions


def legacy_state():
    state = ProjectState.from_apps(apps)
    state.remove_model("decisions", "decisionrecord")
    decision = state.models["decisions", "decision"]
    decision.fields.pop("proposal")
    decision.fields.update(
        {
            "subject_content_type": models.ForeignKey("contenttypes.ContentType", on_delete=models.PROTECT),
            "subject_object_id": models.PositiveBigIntegerField(),
            "closed_reason": models.CharField(max_length=16, null=True),
            "superseded_by": models.ForeignKey("decisions.Decision", null=True, on_delete=models.PROTECT),
            "supersede": models.BooleanField(default=True),
            "invalid_attempts": models.PositiveSmallIntegerField(default=0),
            "max_attempts": models.PositiveSmallIntegerField(default=3),
            "expires_at": models.DateTimeField(null=True),
        }
    )
    decision.options["constraints"] = [
        constraint
        for constraint in decision.options["constraints"]
        if constraint.name != "decisions_resolution_consistent"
    ] + [
        models.CheckConstraint(condition=models.Q(id__gte=0), name=name)
        for name in (
            "decisions_open_subject_unique",
            "decisions_positive_attempts",
            "decisions_resolution_consistent",
        )
    ]
    decision.options["indexes"] += [
        models.Index(fields=["expires_at"], name="decisions_open_expiry"),
        models.Index(fields=["subject_content_type", "subject_object_id"], name="decisions_subject"),
    ]
    return state


def test_schema_cutover_applies_once_without_data_operations():
    before = legacy_state()
    current = ProjectState.from_apps(apps)
    assert transition.applies(before)
    assert not transition.applies(current)
    assert not transition.applies(ProjectState())
    for operation in transition.Migration.operations:
        assert not isinstance(operation, (migrations.RunPython, migrations.RunSQL))
        operation.state_forwards("decisions", before)
    assert not transition.applies(before)
    decision = before.models["decisions", "decision"]
    expected = current.models["decisions", "decision"]
    assert set(decision.fields) == set(expected.fields)
    assert {constraint.name for constraint in decision.options["constraints"]} == {
        "decisions_verdict_consistent", "decisions_resolution_consistent",
    }
    assert decision.options["indexes"] == expected.options["indexes"]
    record = before.models["decisions", "decisionrecord"]
    assert set(record.fields) == set(current.models["decisions", "decisionrecord"].fields)


@pytest.mark.parametrize("missing", ["expires_at", "subject_content_type", "already_added_proposal"])
def test_schema_cutover_rejects_partial_legacy_state(missing):
    state = legacy_state()
    if missing == "already_added_proposal":
        state.models["decisions", "decision"].fields["proposal"] = models.JSONField(default=dict)
    else:
        state.models["decisions", "decision"].fields.pop(missing)
    with pytest.raises(RuntimeError, match="legacy decisions schema is incomplete"):
        transition.applies(state)


@pytest.mark.parametrize("missing", ["proposal", "decisionrecord"])
def test_schema_cutover_rejects_partial_new_state(missing):
    state = ProjectState.from_apps(apps)
    if missing == "proposal":
        state.models["decisions", "decision"].fields.pop(missing)
    else:
        state.remove_model("decisions", missing)
    with pytest.raises(RuntimeError, match="record cutover is incomplete"):
        transition.applies(state)

def grouped_state():
    state = ProjectState.from_apps(apps)
    decision = state.models["decisions", "decision"]
    for name in ("answered_by", "answered_at"):
        decision.fields.pop(name)
    decision.fields.update({
        "form_schema": models.JSONField(default=dict),
        "basis": models.JSONField(default=dict),
        "errors": models.JSONField(default=list),
        "group": models.ForeignKey("decisions.DecisionGroup", on_delete=models.PROTECT),
        "index": models.PositiveSmallIntegerField(default=0),
        "resolution": models.JSONField(null=True),
        "resolved_by": models.ForeignKey("iam.User", on_delete=models.PROTECT, null=True),
        "resolved_at": models.DateTimeField(null=True),
    })
    decision.options["constraints"] = [
        models.UniqueConstraint(fields=("group", "index"), name="decisions_group_index"),
        models.CheckConstraint(condition=models.Q(id__gte=0), name="decisions_resolution_consistent"),
    ]
    state.add_model(ModelState("decisions", "DecisionGroup", [
        ("id", models.BigAutoField(primary_key=True)),
        ("issuer", models.ForeignKey("iam.User", on_delete=models.PROTECT)),
        ("reasked_from", models.ForeignKey("decisions.DecisionGroup", on_delete=models.PROTECT, null=True)),
    ]))
    state.add_model(ModelState("decisions", "DecisionEvidence", [
        ("id", models.BigAutoField(primary_key=True)),
        ("decision", models.ForeignKey("decisions.Decision", on_delete=models.CASCADE)),
        ("content_type", models.ForeignKey("contenttypes.ContentType", on_delete=models.PROTECT)),
        ("object_id", models.PositiveBigIntegerField()),
    ], options={
        "indexes": [models.Index(fields=("content_type", "object_id"), name="decisions_evidence_record")],
        "constraints": [models.UniqueConstraint(fields=("decision", "content_type", "object_id"),
                                                name="decisions_evidence_unique")],
    }))
    return state


def test_single_questions_schema_matches_models_without_converting_data():
    state = grouped_state()
    assert single_questions.applies(state)
    for operation in single_questions.Migration.operations:
        assert not isinstance(operation, (migrations.RunPython, migrations.RunSQL))
        operation.state_forwards("decisions", state)
    current = ProjectState.from_apps(apps).models["decisions", "decision"]
    assert set(state.models["decisions", "decision"].fields) == set(current.fields)
    assert state.models["decisions", "decision"].options["constraints"] == current.options["constraints"]
    assert ("decisions", "decisiongroup") not in state.models
    assert ("decisions", "decisionevidence") not in state.models
    assert not single_questions.applies(state)


def test_single_questions_waits_for_workflow_owner_to_remove_old_link():
    state = grouped_state()
    state.models["workflows", "steprun"].fields["decision_group"] = models.ForeignKey(
        "decisions.DecisionGroup", null=True, on_delete=models.PROTECT,
    )
    assert not single_questions.applies(state)
    state.models["workflows", "steprun"].fields.pop("decision_group")
    assert single_questions.applies(state)


def test_single_questions_rejects_partial_old_schema():
    state = grouped_state()
    state.models["decisions", "decision"].fields.pop("basis")
    with pytest.raises(RuntimeError, match="old decision schema is incomplete"):
        single_questions.applies(state)
