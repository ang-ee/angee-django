"""The schema cutover discards questions, including populated PostgreSQL tables."""

import pytest
from django.apps import apps
from django.db import connection, migrations, models
from django.db.migrations.state import ModelState, ProjectState
from django.utils import timezone

from angee.decisions.runtime_migrations import independent_step_questions, single_questions
from angee.workflows.runtime_migrations import independent_decisions
from tests.conftest import create_platform_admin


def grouped_state():
    state = ProjectState.from_apps(apps)
    state.models["workflows", "steprun"].fields.pop("decision")
    state.models["intake", "need"].fields.pop("access_decision")
    state.models["extraction", "extraction"].fields.pop("correction_decision")
    decision = state.models["decisions", "decision"]
    decision.fields.pop("answered_by")
    decision.fields.pop("answered_at")
    decision.fields.update({
        "verdict": models.CharField(max_length=32, default="pending"),
        "form_schema": models.JSONField(default=dict), "basis": models.JSONField(default=dict),
        "errors": models.JSONField(default=list),
        "group": models.ForeignKey("decisions.DecisionGroup", on_delete=models.PROTECT),
        "index": models.PositiveSmallIntegerField(default=0), "resolution": models.JSONField(null=True),
        "resolved_by": models.ForeignKey("iam.User", null=True, on_delete=models.PROTECT),
        "resolved_at": models.DateTimeField(null=True),
    })
    decision.options["db_table"] = "decisions_decision"
    decision.options["constraints"] = [models.UniqueConstraint(
        fields=("group", "index"), name="decisions_group_index",
    )]
    state.models["decisions", "decisionrecord"].options["db_table"] = "decisions_decisionrecord"
    state.add_model(ModelState("decisions", "DecisionGroup", [
        ("id", models.BigAutoField(primary_key=True)),
        ("issuer", models.ForeignKey("iam.User", on_delete=models.PROTECT)),
        ("reasked_from", models.ForeignKey("decisions.DecisionGroup", null=True, on_delete=models.PROTECT)),
    ]))
    state.add_model(ModelState("decisions", "DecisionEvidence", [
        ("id", models.BigAutoField(primary_key=True)),
        ("decision", models.ForeignKey("decisions.Decision", on_delete=models.CASCADE)),
        ("content_type", models.ForeignKey("contenttypes.ContentType", on_delete=models.PROTECT)),
        ("object_id", models.PositiveBigIntegerField()),
    ]))
    return state


def reverse_linked_state():
    """The interrupted Job A shape asks from Decision.step_run."""
    state = ProjectState.from_apps(apps)
    state.models["workflows", "steprun"].fields.pop("decision")
    state.models["intake", "need"].fields.pop("access_decision")
    state.models["extraction", "extraction"].fields.pop("correction_decision")
    state.models["decisions", "decision"].fields["step_run"] = models.ForeignKey(
        "workflows.StepRun", null=True, on_delete=models.PROTECT,
    )
    return state


def test_reverse_link_cutover_discards_questions_before_waiters():
    state = reverse_linked_state()
    assert independent_step_questions.applies(state)
    assert not independent_decisions.applies(state)
    for operation in independent_step_questions.Migration.operations:
        assert isinstance(operation, (migrations.DeleteModel, migrations.CreateModel))
        operation.state_forwards("decisions", state)
    assert not independent_step_questions.applies(state)
    assert independent_decisions.applies(state)
    assert not independent_decisions.applies(ProjectState.from_apps(apps))


def test_cutover_is_schema_only_and_retires_the_complete_shape():
    state = grouped_state()
    assert single_questions.applies(state)
    for operation in single_questions.Migration.operations:
        assert not isinstance(operation, (migrations.RunPython, migrations.RunSQL, migrations.AlterField))
        operation.state_forwards("decisions", state)
    assert not single_questions.applies(state)
    assert ("decisions", "decisiongroup") not in state.models
    assert ("decisions", "decisionevidence") not in state.models
    assert "step_run" not in state.models["decisions", "decision"].fields
    assert state.models["decisions", "decision"].fields["verdict"].get_internal_type() == "JSONField"


def test_cutover_waits_for_incoming_owner_links():
    state = grouped_state()
    state.models["workflows", "steprun"].fields["decision_group"] = models.ForeignKey(
        "decisions.DecisionGroup", null=True, on_delete=models.PROTECT,
    )
    assert not single_questions.applies(state)
    state.models["workflows", "steprun"].fields.pop("decision_group")
    assert single_questions.applies(state)
    state.models["decisions", "decision"].fields.pop("basis")
    with pytest.raises(RuntimeError, match="incomplete"):
        single_questions.applies(state)


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(connection.vendor != "postgresql", reason="The JSON cast regression requires PostgreSQL.")
@pytest.mark.parametrize(("shape", "populated"), [("grouped", False), ("grouped", True), ("reverse", True)])
def test_postgresql_fresh_and_populated_question_cutover(composed_tables, shape, populated):
    actor = create_platform_admin("cutover-owner")
    state = grouped_state() if shape == "grouped" else reverse_linked_state()
    with connection.cursor() as cursor:
        cursor.execute("CREATE SCHEMA jobab_cutover")
        cursor.execute("SET search_path TO jobab_cutover, public")
    try:
        if populated:
            with connection.schema_editor() as editor:
                for name in (("DecisionGroup", "Decision", "DecisionRecord", "DecisionEvidence")
                             if shape == "grouped" else ("Decision", "DecisionRecord")):
                    editor.create_model(state.apps.get_model("decisions", name))
            old = state.apps.get_model("decisions", "Decision")
            if shape == "grouped":
                group = state.apps.get_model("decisions", "DecisionGroup").objects.create(issuer_id=actor.pk)
                for index, verdict in enumerate(("pending", "completed")):
                    old._base_manager.create(
                        group_id=group.pk, kind="legacy", index=index, verdict=verdict, proposal={},
                    )
            else:
                old._base_manager.create(kind="old-open", proposal={}, verdict=None)
                old._base_manager.create(kind="old-answered", proposal={}, verdict=["accepted"],
                                         answered_by_id=actor.pk, answered_at=timezone.now())
            assert old._base_manager.count() == 2
            operations = (single_questions if shape == "grouped" else independent_step_questions).Migration.operations
        else:
            for name in ("decisionrecord", "decisionevidence", "decision", "decisiongroup"):
                state.remove_model("decisions", name)
            operations = [operation for operation in single_questions.Migration.operations
                          if isinstance(operation, migrations.CreateModel)]
        for operation in operations:
            before = state.clone()
            operation.state_forwards("decisions", state)
            with connection.schema_editor() as editor:
                operation.database_forwards("decisions", editor, before, state)
        current = state.apps.get_model("decisions", "Decision")
        assert current._base_manager.count() == 0
        row = current._base_manager.create(kind="current", proposal={"alternatives": []}, verdict=None)
        current._base_manager.filter(pk=row.pk).update(
            verdict=["accepted"], answered_by_id=actor.pk, answered_at=timezone.now(),
        )
        assert current._base_manager.get(pk=row.pk).verdict == ["accepted"]
        assert "decisions_decisiongroup" not in connection.introspection.table_names()
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SET search_path TO public")
            cursor.execute("DROP SCHEMA jobab_cutover CASCADE")


def test_workflow_cutover_discards_waiters_instead_of_converting_them():
    state = grouped_state()
    state.remove_model("workflows", "steprecord")
    for name in ("StepArtifact", "WorkflowRunEvidence"):
        state.add_model(ModelState("workflows", name, [("id", models.BigAutoField(primary_key=True))]))
    state.models["workflows", "steprun"].fields["decision_group"] = models.ForeignKey(
        "decisions.DecisionGroup", null=True, on_delete=models.PROTECT,
    )
    assert independent_decisions.applies(state)
    for operation in independent_decisions.Migration.operations:
        assert isinstance(operation, (migrations.DeleteModel, migrations.RemoveField))
        operation.state_forwards("workflows", state)
    assert ("workflows", "steprun") not in state.models
    assert ("workflows", "workflowrun") not in state.models
    assert single_questions.applies(state)
