"""Canonical run references retain the workflow's declared concrete subject behavior."""

import time
from contextlib import contextmanager, nullcontext
from datetime import timedelta
from typing import Annotated

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, models
from django.test.utils import CaptureQueriesContext
from pydantic import BaseModel, Field
from rebac import (
    RelationshipTuple,
    delete_relationship,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.errors import MissingActorError

from angee.base.querysets import row_lock_paths
from angee.base.refs import generic_pointer_target
from angee.base.testing.queries import is_rebac_revision_read
from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.graphql.testing.documents import execute_schema, result_data
from angee.workflows import schema as workflow_schema
from angee.workflows.decision_steps import DecisionStep
from angee.workflows.runner import runner
from angee.workflows.states import RunOrigin, RunRelation, RunStatus
from angee.workflows.steps import Step
from angee.workflows.testing.drivers import run_until
from angee.workflows.testing.models import StepRecord, StepRun, Workflow, WorkflowRun
from angee.workflows.transactions import database_timeout
from tests.conftest import Vault, addon_schema, create_user, vault_for
from tests.mtidemo.models import MtiChild, MtiParent
from tests.workflow_steps import Echo, Value, document

pytestmark = pytest.mark.usefixtures("workflow_step_classes")


def test_context_preserves_concrete_mti_subject(execution, register_step, monkeypatch):
    """A child subject retains its fields through scoped reads and locked writes."""

    actor, _ = execution
    lock = models.QuerySet.select_for_update
    locked = []

    def capture_lock(rows, **kwargs):
        if rows.model is MtiChild:
            locked.append(kwargs)
        return lock(rows, **kwargs)

    monkeypatch.setattr(models.QuerySet, "select_for_update", capture_lock)
    with system_context(reason="test.workflow_mti_subject"):
        subject = MtiChild.objects.create(title="Parent identity", detail="Child state")

    class SubjectWriter(Echo):
        subject = MtiChild._meta.label_lower

        def run(self, ctx):
            assert isinstance(ctx.subject, MtiChild)
            assert ctx.subject.detail == "Child state"
            locked = ctx.subject_for_update()
            assert isinstance(locked, MtiChild)
            locked.detail = "Updated child state"
            locked.save(update_fields=("detail",))
            return ctx.done(ctx.input)

    register_step(SubjectWriter)
    workflow = Workflow.objects.install_definition(
        key="concrete_subject",
        name="Concrete subject",
        subject_model=MtiChild._meta.label_lower,
        draft=document("entry"),
        actor=actor,
    )
    run = WorkflowRun.objects.start(workflow, actor=actor, subject=subject)
    assert WorkflowRun.objects.with_actor(actor).for_subject(subject).get().pk == run.pk
    step_run = StepRun.system_queryset().get(run=run)

    assert runner.execute(step_run.pk)
    assert locked == [{"no_key": True, "of": row_lock_paths(MtiChild)}]

    retained = WorkflowRun.system_queryset().get(pk=run.pk)
    assert retained.status == RunStatus.SUCCEEDED
    assert retained.subject_content_type.model_class() is MtiParent
    assert MtiChild.system_queryset().get(pk=subject.pk).detail == "Updated child state"
    replacement = WorkflowRun.objects.reprocess(retained, actor=actor)
    assert replacement.reprocess_of_id == retained.pk
    assert replacement.subject_content_type_id == retained.subject_content_type_id
    assert replacement.subject_model_class is MtiChild
    assert replacement.subject_object_id == subject.pk
    assert [row.record_model_label for row in StepRecord.system_queryset().filter(run=replacement)] == [
        MtiParent._meta.label,
    ]


def test_mti_subject_lookup_and_replay_share_canonical_identity(execution):
    """Parent and child lookup share stored identity without widening run visibility."""
    actor, _ = execution
    reader = create_user("subject-run-reader")
    outsider = create_user("subject-run-outsider")
    with system_context(reason="test.workflow_subject_identity"):
        child = MtiChild.objects.create(title="Shared identity", detail="Child")
        other = MtiChild.objects.create(title="Separate identity", detail="Other child")
        parent = MtiParent.objects.get(pk=child.pk)
    workflow = Workflow.objects.install_definition(
        key="subject_identity", name="Subject identity", subject_model=MtiChild._meta.label_lower,
        draft=document("entry"), actor=actor,
    )
    workflow.with_actor(actor).grant_record_access("starter", reader)
    run = WorkflowRun.objects.start(workflow, actor=actor, subject=child, request_key="subject-replay")
    for subject in (child, parent):
        assert WorkflowRun.objects.with_actor(actor).for_subject(subject).get().pk == run.pk
        assert not WorkflowRun.objects.with_actor(reader).for_subject(subject).exists()
    run.with_actor(actor).grant_record_access("reader", reader)
    for subject in (child, parent):
        assert WorkflowRun.objects.with_actor(reader).for_subject(subject).get().pk == run.pk
        assert not WorkflowRun.objects.with_actor(outsider).for_subject(subject).exists()
    replay = WorkflowRun.objects.start(workflow, actor=actor, subject=child, request_key="subject-replay")
    assert replay.pk == run.pk
    with pytest.raises(ValidationError, match="different request"):
        WorkflowRun.objects.start(workflow, actor=actor, subject=other, request_key="subject-replay")
    with pytest.raises(ValidationError, match="wrong model"):
        WorkflowRun.objects.start(workflow, actor=actor, subject=parent, request_key="subject-replay")


def test_undeclared_subject_uses_the_canonical_record_type(execution):
    """Without a concrete workflow contract the canonical record owns subject loading."""
    actor, _ = execution
    with system_context(reason="test.workflow_optional_subject"):
        child = MtiChild.objects.create(title="Optional subject", detail="Child")
    workflow = Workflow.objects.install_definition(
        key="optional_subject", name="Optional subject", draft=document("entry"), actor=actor,
    )
    run = WorkflowRun.objects.start(workflow, actor=actor, subject=child)
    assert WorkflowRun.objects.with_actor(actor).for_subject(child).get().pk == run.pk
    assert run.subject_model_class is MtiParent
    WorkflowRun.objects.cancel(run, actor=actor)
    with system_context(reason="test.workflow_subject_deleted"):
        MtiChild.objects.filter(pk=child.pk).delete()
    with pytest.raises(MtiParent.DoesNotExist):
        WorkflowRun.objects.reprocess(run, actor=actor)


@pytest.mark.parametrize("ambient_system", [False, True])
def test_subject_loading_requires_current_access_to_the_declared_child(execution, register_step, ambient_system):
    """Access to the canonical ancestor cannot substitute for the declared child's policy."""
    admin, _ = execution
    actor = create_user("child-subject-starter")
    with system_context(reason="test.workflow_subject_grants"):
        child = MtiChild.objects.create(title="Private child", detail="Child-only evidence")
        parent = MtiParent.objects.get(pk=child.pk)
        child_access = RelationshipTuple(to_object_ref(child), "reader", to_subject_ref(actor))
        write_relationships([child_access])

    class ChildReader(Echo):
        key = "read_mti_child"
        subject = MtiChild._meta.label_lower

        def run(self, ctx):
            assert ctx.subject.detail == "Child-only evidence"
            return ctx.done(ctx.input)

    register_step(ChildReader)
    workflow = Workflow.objects.install_definition(
        key="private_child", name="Private child", subject_model=MtiChild._meta.label_lower,
        draft=document("entry", step=ChildReader.key), actor=admin,
    )
    workflow.with_actor(admin).grant_record_access("starter", actor)
    run = WorkflowRun.objects.start(workflow, actor=actor, subject=child)
    with system_context(reason="test.workflow_subject_revocation"):
        delete_relationship(child_access)
        write_relationships([RelationshipTuple(to_object_ref(parent), "reader", to_subject_ref(actor))])
    assert parent.with_actor(actor).has_access("read")
    run_until(run)
    assert run.status == RunStatus.FAILED
    assert "absent or inaccessible" in StepRun.system_queryset().get(run=run).attempts.with_actor(actor).get().error
    with system_context(reason="test.workflow_subject_ambient") if ambient_system else nullcontext():
        with pytest.raises(PermissionDenied, match="referenced record"):
            WorkflowRun.objects.reprocess(run, actor=actor)


@pytest.mark.parametrize("ambient_system", [False, True])
def test_start_requires_read_access_to_subject(execution, ambient_system):
    """Workflow start access never grants access to an independently protected subject."""
    admin, _ = execution
    actor = create_user("subject_starter")
    subject = Workflow.objects.install_definition(
        key="protected_subject", name="Protected subject", draft=document("entry"), actor=admin,
    )
    workflow = Workflow.objects.install_definition(
        key="subject_admission", name="Subject admission", subject_model="workflows.workflow",
        draft=document("entry"), actor=admin,
    )
    workflow.with_actor(admin).grant_record_access("starter", actor)
    with (
        system_context(reason="test.explicit_subject_actor") if ambient_system else nullcontext(),
        pytest.raises(PermissionDenied, match="referenced record"),
    ):
        WorkflowRun.objects.start(workflow, actor=actor, subject=subject)
    assert not WorkflowRun.system_queryset().filter(version__workflow=workflow).exists()

    subject.with_actor(admin).grant_record_access("viewer", actor)
    with system_context(reason="test.explicit_subject_actor") if ambient_system else nullcontext():
        run = WorkflowRun.objects.start(workflow, actor=actor, subject=subject)
    assert run.run_as_id == actor.pk and run.subject_object_id == subject.pk
    evidence = StepRecord.system_queryset().get(run=run)
    assert (evidence.record_model_label, evidence.record_public_id) == (subject._meta.label, subject.sqid)


def test_entry_record_input_is_checked_deduplicated_and_retained(execution, register_step):
    """Only relation-marked values and the subject become canonical retained sources."""
    admin, _ = execution
    actor = create_user("record-input-starter")
    other = create_user("private-input-owner")
    source = vault_for(actor, name="Source")
    another = vault_for(actor, name="Another")
    private = vault_for(other, name="Private")

    class Nested(BaseModel):
        record: str = Field(json_schema_extra={"relation": {"resource": "knowledge.Vault"}})

    class ReferencedInput(BaseModel):
        source: str = Field(json_schema_extra={"relation": {"resource": "knowledge.Vault"}})
        nested: Nested
        records: list[Annotated[str, Field(json_schema_extra={"relation": {"resource": "knowledge.Vault"}})]]
        plain: str

    class ReadReferences(Step[ReferencedInput, None, None]):
        key = "read_input_references"

        def run(self, ctx):
            return ctx.done()

    register_step(ReadReferences)
    workflow = Workflow.objects.install_definition(
        key="record_input", name="Record input", draft=document("entry", step=ReadReferences.key), actor=admin,
    )
    workflow.with_actor(admin).grant_record_access("starter", actor)
    payload = {
        "source": source.sqid, "nested": {"record": another.sqid},
        "records": [source.sqid], "plain": private.sqid,
    }
    run = WorkflowRun.objects.start(workflow, actor=actor, subject=source, input=payload, request_key="input-evidence")
    assert {
        (row.record_model_label, row.record_public_id)
        for row in StepRecord.system_queryset().filter(run=run)
    } == {("knowledge.Vault", source.sqid), ("knowledge.Vault", another.sqid)}
    assert WorkflowRun.objects.start(
        workflow, actor=actor, subject=source, input=payload, request_key="input-evidence",
    ).pk == run.pk
    assert StepRecord.system_queryset().filter(run=run).count() == 2
    # An ordinary field naming a record links nothing: an admin who reads it finds no run about it.
    runs = WorkflowRun.objects.with_actor(admin)
    assert not runs.about(private).exists()
    assert set(runs.filter(pk=run.pk).concerns(Vault)) == {source, another}
    viewer = create_user("record-input-viewer")
    run.with_actor(actor).grant_record_access("reader", viewer)
    schema = addon_schema(workflow_schema.schemas, "console")
    hidden_input = result_data(execute_schema(
        schema, "query($id: String!) { workflowrun_by_pk(id: $id) { input } }",
        {"id": run.sqid}, user=viewer,
    ))["workflowrun_by_pk"]["input"]
    assert hidden_input == {
        "source": None, "nested": {"record": None}, "records": [None], "plain": private.sqid,
    }

    payload["nested"]["record"] = private.sqid
    with pytest.raises(PermissionDenied, match="referenced record"):
        WorkflowRun.objects.start(workflow, actor=actor, subject=source, input=payload)
    assert WorkflowRun.system_queryset().filter(version__workflow=workflow).count() == 1


class NamedInput(BaseModel):
    named: str = Field(json_schema_extra={"relation": {"resource": "knowledge.Vault"}})


class ReadNamed(Step[NamedInput, None, None]):
    key = "read_named_input"

    def run(self, ctx):
        return ctx.done()


def read_named_workflow(register_step, actor):
    register_step(ReadNamed)
    return Workflow.objects.install_definition(
        key="read_named", name="Read named", draft=document("entry", step=ReadNamed.key), actor=actor,
    )


def test_a_waiting_decision_run_is_about_its_declared_input_from_admission(
    execution, register_step, django_capture_on_commit_callbacks,
):
    """The run is about its input's declared relation before its step runs, while it waits, and past its deletion."""
    actor, sent = execution
    subject, named, recorded = (vault_for(actor, name=name) for name in ("Subject", "Named", "Recorded"))

    class AskAboutNamed(DecisionStep[NamedInput, None, None]):
        key = "ask_about_named"
        outcomes = {"done": "Done"}

        def ask(self, ctx):
            ctx.record(recorded)
            return ctx.ask(DecisionRequest(
                kind="Confirm", records=(ctx.subject,), assignees=(actor,),
                proposal=DecisionProposal(alternatives=[{"key": "keep", "label": "Keep", "outcome": "done"}]),
            ))

    register_step(AskAboutNamed)
    workflow = Workflow.objects.install_definition(
        key="ask_about_named", name="Ask about named", draft=document("ask", step=AskAboutNamed.key), actor=actor,
    )
    run = WorkflowRun.objects.start(workflow, actor=actor, subject=subject, input={"named": named.sqid})
    runs = WorkflowRun.objects.with_actor(actor)
    assert StepRun.system_queryset().get(run=run).status == "ready"
    assert list(runs.about(named)) == [run]

    run_until(run)
    assert StepRun.system_queryset().get(run=run).hold == "decision"
    assert list(runs.about(named)) == [run]
    assert set(runs.filter(pk=run.pk).concerns(Vault)) == {subject, named, recorded}
    sent.clear()
    with django_capture_on_commit_callbacks(execute=True):
        Vault.system_queryset().filter(pk=named.pk).delete()
    assert not [name for name, _ in sent if name == "workflows.cancel"]
    assert WorkflowRun.system_queryset().get(pk=run.pk).status == "waiting"


def test_concerns_and_about_read_as_their_reader(execution, register_step):
    """A run reader never gets an input-named record it cannot read, and an unreadable run yields nothing."""
    admin, _ = execution
    owner, viewer, outsider = (create_user(f"concerns-{name}") for name in ("owner", "viewer", "outsider"))
    subject = vault_for(owner, name="Shared subject")
    private = vault_for(owner, name="Private input")
    write_relationships([
        RelationshipTuple(to_object_ref(subject), "viewer", to_subject_ref(viewer)),
        RelationshipTuple(to_object_ref(private), "viewer", to_subject_ref(outsider)),
    ])
    workflow = read_named_workflow(register_step, admin)
    workflow.with_actor(admin).grant_record_access("starter", owner)
    run = WorkflowRun.objects.start(workflow, actor=owner, subject=subject, input={"named": private.sqid})
    run.with_actor(owner).grant_record_access("reader", viewer)

    def concerns(actor):
        return set(WorkflowRun.objects.with_actor(actor).filter(pk=run.pk).concerns(Vault))

    assert concerns(owner) == {subject, private}
    assert concerns(viewer) == {subject}
    assert concerns(outsider) == set()
    assert not WorkflowRun.objects.with_actor(outsider).about(private).exists()
    assert list(WorkflowRun.objects.with_actor(viewer).about(subject)) == [run]
    with system_context(reason="tests.workflows.concerns_system"):
        assert set(WorkflowRun.objects.filter(pk=run.pk).concerns(Vault)) == {subject, private}
    with pytest.raises(MissingActorError):
        set(WorkflowRun.system_queryset().filter(pk=run.pk).concerns(Vault))


def test_concerns_selects_records_by_their_model_on_the_subject_and_the_links(execution, register_step):
    """Rows of another model with the same primary keys never answer, through a subject or a link."""
    actor, _ = execution
    subject, named, legacy = (vault_for(actor, name=name) for name in ("Subject", "Named", "Legacy subject"))
    with system_context(reason="tests.workflows.concerns_twins"):
        for record in (subject, named, legacy):
            MtiParent.objects.create(pk=record.pk, title=f"Twin of {record.name}")
    workflow = read_named_workflow(register_step, actor)
    run = WorkflowRun.objects.start(workflow, actor=actor, subject=subject, input={"named": named.sqid})
    older = WorkflowRun.objects.start(workflow, actor=actor, input={"named": named.sqid})
    # A run admitted before admission linked its subject names that subject only in its own columns.
    content_type, pk = generic_pointer_target(legacy)
    with system_context(reason="tests.workflows.concerns_legacy_subject"):
        WorkflowRun.objects.filter(pk=older.pk).update(subject_content_type=content_type, subject_object_id=pk)
    selected = WorkflowRun.objects.with_actor(actor).filter(pk__in=(run.pk, older.pk))
    assert set(selected.concerns(Vault)) == {subject, named, legacy}
    assert not selected.concerns(MtiParent).exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("relation", ["owned", "continuation"])
def test_a_parent_run_concerns_what_its_child_runs_are_about(child_graph, relation):
    """``about`` reaches a child's parent, so the parent's ``concerns`` reaches the child's records."""
    actor, _, admitted, build = child_graph
    targets = [vault_for(actor, name=f"Child subject {index}") for index in range(2)]

    def build_parent(target):
        parent, _ = build(relation=relation, await_child=relation == "owned", subject=target,
                          subject_model="knowledge.vault")
        run_until(parent)
        return parent

    parent = build_parent(targets[0])
    child = admitted[0]
    runs = WorkflowRun.objects.with_actor(actor)
    assert set(runs.about(targets[0])) == {parent, child}
    assert set(runs.filter(pk=parent.pk).concerns(Vault)) == {targets[0]}
    assert not runs.filter(pk=parent.pk).concerns(Vault, descendants=False).exists()
    assert set(runs.filter(pk=child.pk).concerns(Vault)) == {targets[0]}
    roots = runs.filter(parent_step=None)
    read(roots, Vault)
    statements, _ = read(roots, Vault)
    build_parent(targets[1])
    # A second parent and child leave the walk's statements as they were: no child id is bound.
    assert read(roots, Vault) == (statements, targets)


def test_concerns_reads_a_child_model_in_statements_that_do_not_grow_with_the_runs(execution, register_step):
    """A child model's records come back through its canonical links, in the same statements for one run or three."""
    actor, _ = execution

    class SourceInput(BaseModel):
        source: str = Field(json_schema_extra={"relation": {"resource": "mtidemo.MtiChild"}})

    class ReadSource(Step[SourceInput, None, None]):
        key = "read_concerned_source"

        def run(self, ctx):
            return ctx.done()

    register_step(ReadSource)
    workflow = Workflow.objects.install_definition(
        key="concerned_source", name="Concerned source", draft=document("entry", step=ReadSource.key), actor=actor,
    )
    with system_context(reason="tests.workflows.concerns_child"):
        sources = [MtiChild.objects.create(title=f"Source {index}", detail="Child state") for index in range(3)]

    def start(source):
        return WorkflowRun.objects.start(workflow, actor=actor, input={"source": source.sqid})

    scoped = WorkflowRun.objects.with_actor(actor)
    selected = scoped.filter(version__workflow=workflow)
    runs = [start(sources[0])]
    assert list(scoped.about(sources[0])) == runs
    read(selected, MtiChild)
    one = {descendants: read(selected, MtiChild, descendants=descendants) for descendants in (True, False)}
    runs += [start(source) for source in sources[1:]]
    for descendants in (True, False):
        statements, rows = read(selected, MtiChild, descendants=descendants)
        # The same SQL text for three runs as for one: the selection is a subquery, never a bound id list.
        assert statements == one[descendants][0] and len(statements) == 1
        assert (one[descendants][1], set(rows)) == ([sources[0]], set(sources))
    assert {row.pk for row in scoped.filter(pk=runs[0].pk).concerns(MtiParent)} == {sources[0].pk}
    for descendants in (True, False):
        sliced = scoped.filter(pk__in=[run.pk for run in runs]).order_by("pk")[:1]
        assert list(sliced.concerns(MtiChild, descendants=descendants)) == [sources[0]]
    assert not scoped.filter(pk=-1).concerns(MtiChild).exists()


def test_one_walk_reaches_a_hundred_generations_in_one_statement(execution, register_step):
    """``concerns`` from a chain's root and ``about`` from its leaf cross 100 generations in one statement each."""
    actor, _ = execution
    depth, admitted, chain_workflow = 100, [], []
    leaf = vault_for(actor, name="Leaf subject")

    class Continue(Step[Value, Value, None]):
        key = "continue_chain"

        def run(self, ctx):
            if (generation := ctx.input.value + 1) < depth:
                admitted.append(ctx.start_run(
                    chain_workflow[0], input={"value": generation}, relation=str(RunRelation.CONTINUATION),
                    subject=leaf if generation == depth - 1 else None,
                ))
            return ctx.done(ctx.input)

    register_step(Continue)
    chain_workflow.append(Workflow.objects.install_definition(
        key="continue_chain", name="Continue chain", draft=document("entry", step=Continue.key), actor=actor,
    ))
    chain = [WorkflowRun.objects.start(chain_workflow[0], actor=actor, input={"value": 0})]
    for generation in range(depth):
        run_until(chain[generation])
        chain += admitted[generation:generation + 1]
    assert len(chain) == depth
    runs = WorkflowRun.objects.with_actor(actor)
    read(runs.filter(pk=chain[0].pk), Vault)
    deep, rows = read(runs.filter(pk=chain[0].pk), Vault)
    shallow, shallow_rows = read(runs.filter(pk=chain[depth - 10].pk), Vault)
    assert (rows, shallow_rows) == ([leaf], [leaf]) and len(deep) == len(shallow) == 1
    # Primary keys only: loading run instances adds REBAC's field-gate read, which is not the walk's.
    with CaptureQueriesContext(connection) as queries:
        assert set(runs.about(leaf).values_list("pk", flat=True)) == {run.pk for run in chain}
    assert len([query for query in queries if not is_rebac_revision_read(query["sql"])]) == 1


def test_a_cycle_of_runs_ends_both_walks(execution):
    """Two runs that name each other's step as their parent, which admission never writes, still end both walks."""
    actor, _ = execution
    first, second = vault_for(actor, name="First"), vault_for(actor, name="Second")
    workflow = Workflow.objects.install_definition(key="cycle", name="Cycle", draft=document("entry"), actor=actor)
    runs = [WorkflowRun.objects.start(workflow, actor=actor, subject=subject, input={"value": 1})
            for subject in (first, second)]
    steps = [StepRun.system_queryset().get(run=run) for run in runs]
    with system_context(reason="tests.workflows.run_cycle"):
        for run, step in zip(runs, reversed(steps), strict=True):
            WorkflowRun.objects.filter(pk=run.pk).update(
                parent_step=step, origin=RunOrigin.WORKFLOW, relation=RunRelation.CONTINUATION,
            )
    scoped = WorkflowRun.objects.with_actor(actor)
    with statements_within(timedelta(seconds=30)):
        assert set(scoped.filter(pk=runs[0].pk).concerns(Vault)) == {first, second}
        assert set(scoped.about(first)) == set(runs)


@contextmanager
def statements_within(limit):
    """Abort a statement that runs past ``limit``, so a walk that never ends fails instead of hanging."""
    if connection.vendor != "sqlite":
        with database_timeout(limit):
            yield
        return
    connection.ensure_connection()
    deadline = time.monotonic() + limit.total_seconds()
    connection.connection.set_progress_handler(lambda: time.monotonic() > deadline, 100_000)
    try:
        yield
    finally:
        connection.connection.set_progress_handler(None, 0)


def test_a_filter_before_about_selects_from_everything_the_walk_reaches(execution):
    """A finished run between two open ones is skipped by an open-runs filter, not a wall the walk stops at."""
    actor, _ = execution
    leaf = vault_for(actor, name="Leaf subject")
    workflow = Workflow.objects.install_definition(
        key="filtered_chain", name="Filtered chain", draft=document("entry"), actor=actor,
    )
    chain = [WorkflowRun.objects.start(workflow, actor=actor, subject=subject, input={"value": 1})
             for subject in (None, None, leaf)]
    run_until(chain[1])
    steps = [StepRun.system_queryset().get(run=run) for run in chain]
    with system_context(reason="tests.workflows.filtered_chain"):
        # R0 (running) -> R1 (succeeded) -> R2 (running): a finished run between two open ones.
        for child, parent_step in ((chain[1], steps[0]), (chain[2], steps[1])):
            WorkflowRun.objects.filter(pk=child.pk).update(
                parent_step=parent_step, origin=RunOrigin.WORKFLOW, relation=RunRelation.CONTINUATION,
            )
        assert [WorkflowRun.objects.get(pk=run.pk).status for run in chain] == ["running", "succeeded", "running"]
    runs = WorkflowRun.objects.with_actor(actor)
    assert list(runs.about(leaf)) == chain
    assert list(runs.exclude(status__in=RunStatus.terminal_values()).about(leaf)) == [chain[0], chain[2]]


def test_both_walks_take_more_runs_than_a_statement_can_bind(execution):
    """33,001 runs about one record: neither walk binds a run id, past SQLite's 32,766 parameters."""
    actor, _ = execution
    subject = vault_for(actor, name="Shared subject")
    workflow = Workflow.objects.install_definition(key="wide", name="Wide", draft=document("entry"), actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor, subject=subject, input={"value": 1})
    content_type, pk = generic_pointer_target(subject)
    with system_context(reason="tests.workflows.wide_runs"):
        # No driver admits 33,000 runs in a test's time; these rows carry only what both walks read.
        WorkflowRun._base_manager.bulk_create([
            WorkflowRun(version_id=run.version_id, run_as_id=run.run_as_id, origin=run.origin, input={},
                        subject_content_type=content_type, subject_object_id=pk)
            for _ in range(33_000)
        ], batch_size=500)
        runs = WorkflowRun.objects.filter(version=run.version)
        assert runs.about(subject).count() == 33_001
        assert list(runs.concerns(Vault)) == [subject]


def read(selected, model, **options):
    """The data statements a ``concerns`` read issues, REBAC revision reads aside, and its records."""
    with CaptureQueriesContext(connection) as queries:
        rows = list(selected.concerns(model, **options))
    return [query["sql"] for query in queries if not is_rebac_revision_read(query["sql"])], rows
