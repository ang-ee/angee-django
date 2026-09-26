"""Focused contracts for workflow definition write ownership."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from rebac import PermissionDenied, system_context

from angee.workflows.models import WorkflowStatus
from angee.workflows.testing.models import Edge, Step, Workflow
from tests.conftest import create_platform_admin

User = get_user_model()


@pytest.mark.django_db(transaction=True)
def test_step_config_hook_reads_deferred_fields(composed_tables: None) -> None:
    del composed_tables
    with system_context(reason="step deferred config setup"):
        workflow = Workflow.objects.create(name="Deferred config")
        step = Step.objects.create(
            workflow=workflow, key="entry", name="Entry", step_class="fixture", config={"retained": 3},
        )
        deferred = Step.objects.only("pk").get(pk=step.pk)
    # Confirm lazy-loading is exercised for both the config and its implementation selector.
    assert {"config", "step_class"} <= deferred.get_deferred_fields()

    with system_context(reason="step deferred config validation"):
        deferred.validate_impl_configs()
        assert deferred.config == {"retained": 3}


@pytest.mark.django_db(transaction=True)
def test_edge_save_rejects_stale_cached_endpoint_ancestry(composed_tables: None) -> None:
    """Moving a cached endpoint cannot bypass its persisted workflow ownership."""

    del composed_tables
    with system_context(reason="definition stale endpoint validation"):
        original = Workflow.objects.create(name="Original")
        replacement = Workflow.objects.create(name="Replacement")
        source = Step.objects.create(workflow=original, key="source", name="Source", step_class="fixture")
        target = Step.objects.create(workflow=original, key="target", name="Target", step_class="fixture")
        edge = Edge(workflow=original, source=source, target=target)
        moved = Step.objects.get(pk=source.pk)
        moved.workflow = replacement
        moved.save()
        with pytest.raises(ValidationError, match="source must belong"):
            edge.save()


@pytest.mark.django_db(transaction=True)
def test_input_binding_is_versioned_and_copied_with_the_definition(composed_tables: None) -> None:
    del composed_tables
    binding = {"kind": "workflow_input", "path": []}
    with system_context(reason="test versioned input binding"):
        workflow = Workflow.objects.create(name="Bound definition")
        step = Step.objects.create(
            workflow=workflow,
            key="entry",
            name="Entry",
            step_class="agent_session",
            is_entry=True,
            input_binding=binding,
        )
        first = workflow.publish()
        workflow.refresh_from_db()
        published_revision = workflow.draft_revision
        step.input_binding = {"kind": "constant", "value": None}
        step.save(update_fields={"input_binding"})
        workflow.refresh_from_db()
        binding_revision = workflow.draft_revision
        second = workflow.publish_if_changed()
        first_binding = first.steps.get().input_binding
        second_binding = second.steps.get().input_binding if second is not None else None

    assert binding_revision == published_revision + 1
    assert first_binding == binding
    assert second is not None
    assert second_binding == {"kind": "constant", "value": None}


@pytest.mark.django_db(transaction=True)
def test_definition_rows_advance_revision_once_per_locked_batch(composed_tables: None) -> None:
    """Nested row saves explicitly share one lineage revision owner."""

    del composed_tables
    with system_context(reason="test definition revision batch"):
        workflow = Workflow.objects.create(name="Batch")
        with Workflow.objects._definition_write((workflow.pk,)) as session:
            first = Step(workflow=workflow, key="first", name="First", step_class="fixture", is_entry=True)
            first.save(session=session)
            second = Step(workflow=workflow, key="second", name="Second", step_class="fixture")
            second.save(session=session)
            Edge(workflow=workflow, source=first, target=second, condition="timer").save(session=session)

        workflow.refresh_from_db()
        assert workflow.draft_revision == 1

        with Workflow.objects._definition_write((workflow.pk,)) as session:
            first.save(update_fields={"name"}, session=session)
            second.save(update_fields={"position"}, session=session)
        workflow.refresh_from_db()
        assert workflow.draft_revision == 1


@pytest.mark.django_db(transaction=True)
def test_noop_and_update_fields_compare_only_persisted_content(composed_tables: None) -> None:
    """Incidental saves and unsaved attributes do not fabricate revisions."""

    del composed_tables
    with system_context(reason="test definition no-op revisions"):
        workflow = Workflow.objects.create(name="No-op")
        step = Step.objects.create(workflow=workflow, key="start", name="Start", step_class="fixture", is_entry=True)
        workflow.refresh_from_db()
        revision = workflow.draft_revision

        step.name = "Only in memory"
        step.save(update_fields={"position"})
        workflow.refresh_from_db()
        assert workflow.draft_revision == revision
        assert Step.objects.get(pk=step.pk).name == "Start"

        step.position = {"x": 10, "y": 20}
        step.save(update_fields={"position"})
        workflow.refresh_from_db()
        assert workflow.draft_revision == revision + 1


@pytest.mark.django_db(transaction=True)
def test_workflow_definition_fields_join_the_revision_owner(composed_tables: None) -> None:
    """Head settings revise only when a persisted definition field changes."""

    del composed_tables
    with system_context(reason="test workflow-owned definition fields"):
        workflow = Workflow.objects.create(name="Settings")
        workflow.description = "Draft description"
        workflow.save(update_fields={"description"})
        workflow.refresh_from_db()
        assert workflow.draft_revision == 1

        workflow.name = "Only in memory"
        workflow.save(update_fields={"description"})
        workflow.refresh_from_db()
        assert workflow.name == "Settings"
        assert workflow.draft_revision == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("field_name", ["error_workflow", "error_workflow_id"])
def test_foreign_key_declaration_changes_honor_native_update_fields(
    composed_tables: None, field_name: str
) -> None:
    """A saved relation advances the revision through its name or storage name."""

    del composed_tables
    with system_context(reason="test declaration foreign key revision"):
        workflow = Workflow.objects.create(name="Settings")
        handler = Workflow.objects.create(name="Error handler")
        workflow.error_workflow_id = handler.pk
        workflow.save(update_fields={field_name})
        workflow.refresh_from_db()
        assert workflow.error_workflow_id == handler.pk
        assert workflow.draft_revision == 1

        workflow.save(update_fields={field_name})
        workflow.refresh_from_db()
        assert workflow.draft_revision == 1


@pytest.mark.django_db(transaction=True)
def test_lineage_heads_remains_chainable_and_excludes_publications(composed_tables: None) -> None:
    """Stable-key lookup selects the editable head from a shared-key lineage."""

    del composed_tables
    with system_context(reason="test lineage head lookup"):
        workflow = Workflow.objects.create(name="Lineage", key="lineage")
        Step.objects.create(workflow=workflow, key="entry", name="Entry", step_class="fixture", is_entry=True)
        publication = workflow.publish()
        Workflow.objects.create(name="Other", key="other")

        assert publication.key == workflow.key
        assert list(Workflow.objects.lineage_heads("lineage").filter(name="Lineage")) == [workflow]
        assert not Workflow.objects.lineage_heads("missing").exists()


@pytest.mark.django_db(transaction=True)
def test_stale_workflow_instance_cannot_regress_database_revision(composed_tables: None) -> None:
    """The database revision remains authoritative across stale head saves."""

    del composed_tables
    with system_context(reason="test stale workflow revision"):
        workflow = Workflow.objects.create(name="Stale")
        stale = Workflow.objects.get(pk=workflow.pk)
        Step.objects.create(workflow=workflow, key="start", name="Start", step_class="fixture")

        stale.save()
        stale.refresh_from_db()
        assert stale.draft_revision == 1

        stale.description = "Changed from stale object"
        stale.save()
        stale.refresh_from_db()
        assert stale.draft_revision == 2


@pytest.mark.django_db(transaction=True)
def test_step_delete_owns_incident_edges_and_one_revision(composed_tables: None) -> None:
    """Step deletion explicitly performs Collector-skipped edge cascades."""

    del composed_tables
    with system_context(reason="test definition child cascade"):
        workflow = Workflow.objects.create(name="Delete")
        source = Step.objects.create(
            workflow=workflow, key="source", name="Source", step_class="fixture", is_entry=True
        )
        target = Step.objects.create(workflow=workflow, key="target", name="Target", step_class="fixture")
        Edge.objects.create(workflow=workflow, source=source, target=target)
        workflow.refresh_from_db()
        revision = workflow.draft_revision

        Step.objects.filter(pk=source.pk).delete()

        workflow.refresh_from_db()
        assert workflow.draft_revision == revision + 1
        assert not Edge.objects.filter(workflow=workflow).exists()


@pytest.mark.django_db(transaction=True)
def test_definition_bulk_writes_are_rejected(composed_tables: None) -> None:
    """Public collection APIs cannot bypass the locked instance owner."""

    del composed_tables
    with system_context(reason="test guarded definition collections"):
        workflow = Workflow.objects.create(name="Guarded")
        step = Step.objects.create(workflow=workflow, key="start", name="Start", step_class="fixture")
        target = Step.objects.create(workflow=workflow, key="target", name="Target", step_class="fixture")
        edge = Edge.objects.create(workflow=workflow, source=step, target=target)

        for queryset, fields in (
            (Workflow.objects.filter(pk=workflow.pk), {"name": "Bypass"}),
            (Step.objects.filter(pk=step.pk), {"name": "Bypass"}),
            (Edge.objects.filter(pk=edge.pk), {"condition": "bypass"}),
        ):
            with pytest.raises(TypeError, match=r"QuerySet\.update"):
                queryset.update(**fields)
        with pytest.raises(TypeError, match="bulk_create"):
            Step.objects.bulk_create([Step(workflow=workflow, key="bulk", name="Bulk", step_class="fixture")])
        with pytest.raises(TypeError, match="bulk_update"):
            Step.objects.bulk_update([step], ["name"])


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("step_class", "config"),
    [("wait", {}), ("gate", {}), ("map", {})],
)
def test_incomplete_typed_step_configs_remain_storable_drafts(
    composed_tables: None,
    step_class: str,
    config: dict[str, object],
) -> None:
    """Readiness gaps remain stored as object-shaped draft config."""

    del composed_tables
    with system_context(reason="test incomplete workflow drafts"):
        workflow = Workflow.objects.create(name=f"Incomplete {step_class}")
        step = Step.objects.create(
            workflow=workflow,
            key=step_class,
            name=step_class.title(),
            step_class=step_class,
            config=config,
        )

        step.refresh_from_db()
        assert step.config == config
        assert step.config_projection().errors


@pytest.mark.django_db(transaction=True)
def test_incomplete_retry_policy_remains_a_storable_draft(composed_tables: None) -> None:
    """Retry value constraints are reported by readiness rather than storage."""

    del composed_tables
    with system_context(reason="test incomplete retry draft"):
        workflow = Workflow.objects.create(name="Retry draft")
        step = Step.objects.create(
            workflow=workflow,
            key="wait",
            name="Wait",
            step_class="wait",
            config={"retry": {"max_attempts": "later"}},
        )
        assert step.config == {"retry": {"max_attempts": "later"}}


@pytest.mark.django_db(transaction=True)
def test_failed_batch_rolls_back_rows_and_revision(composed_tables: None) -> None:
    """A nested failure leaves both definition rows and revision unchanged."""

    del composed_tables
    with system_context(reason="test definition rollback"):
        workflow = Workflow.objects.create(name="Rollback")
        other = Workflow.objects.create(name="Other")
        with pytest.raises(ValidationError, match="same workflow"):
            with Workflow.objects._definition_write((workflow.pk, other.pk)) as session:
                source = Step(workflow=workflow, key="source", name="Source", step_class="fixture")
                source.save(session=session)
                target = Step(workflow=other, key="target", name="Target", step_class="fixture")
                target.save(session=session)
                Edge(workflow=workflow, source=source, target=target).save(session=session)

        workflow.refresh_from_db()
        assert workflow.draft_revision == 0
        assert not Step.objects.filter(workflow=workflow).exists()


@pytest.mark.django_db(transaction=True)
def test_moves_revise_both_parents_and_reject_connected_steps(composed_tables: None) -> None:
    """Moves own old and new lineages, while connected nodes cannot strand edges."""

    del composed_tables
    with system_context(reason="test definition moves"):
        first = Workflow.objects.create(name="First")
        second = Workflow.objects.create(name="Second")
        movable = Step.objects.create(workflow=first, key="movable", name="Movable", step_class="fixture")
        first.refresh_from_db()
        first_revision = first.draft_revision

        movable.workflow = second
        movable.save(update_fields={"workflow"})
        first.refresh_from_db()
        second.refresh_from_db()
        assert first.draft_revision == first_revision + 1
        assert second.draft_revision == 1

        anchor = Step.objects.create(workflow=second, key="anchor", name="Anchor", step_class="fixture")
        Edge.objects.create(workflow=second, source=anchor, target=movable)
        movable.workflow = first
        with pytest.raises(ValidationError, match="connected step"):
            movable.save(update_fields={"workflow"})


@pytest.mark.django_db(transaction=True)
def test_fresh_non_draft_heads_and_identity_reassignment_are_rejected(composed_tables: None) -> None:
    """Publication identity fields remain manager-owned across every save path."""

    del composed_tables
    with system_context(reason="test workflow publication identity"):
        with pytest.raises(ValidationError, match="revision-zero drafts"):
            Workflow.objects.create(name="Forged", status=WorkflowStatus.PUBLISHED)

        workflow = Workflow.objects.create(name="Identity")
        workflow.version = 3
        with pytest.raises(ValidationError, match="versions are immutable"):
            workflow.save()


@pytest.mark.django_db(transaction=True)
def test_published_parent_rejects_child_mutation_even_under_sudo(composed_tables: None) -> None:
    """System authorization does not weaken immutable definition history."""

    del composed_tables
    with system_context(reason="test immutable definition"):
        draft = Workflow.objects.create(name="Historical")
        Step.objects.create(workflow=draft, key="entry", name="Entry", step_class="agent_session", is_entry=True)
        published = draft.publish()

        with pytest.raises(ValidationError, match="immutable"):
            Step.objects.create(workflow=published, key="late", name="Late", step_class="fixture")


@pytest.mark.django_db(transaction=True)
def test_publication_records_revision_and_protects_lineage_history(composed_tables: None) -> None:
    """A publication pins its source revision and prevents deletion of its head."""

    del composed_tables
    with system_context(reason="test protected workflow history"):
        workflow = Workflow.objects.create(name="History")
        Step.objects.create(
            workflow=workflow,
            key="wait",
            name="Wait",
            step_class="wait",
            config={"until": "2030-01-02T03:04:05Z"},
            is_entry=True,
        )
        workflow.refresh_from_db()
        published = workflow.publish()
        assert published.draft_revision == workflow.draft_revision

        with pytest.raises(ValidationError, match="publication history"):
            workflow.delete()

        assert Workflow.objects.filter(pk=workflow.pk).exists()
        assert Workflow.objects.filter(pk=published.pk, status=WorkflowStatus.PUBLISHED).exists()

        published.archive()
        published.refresh_from_db()
        assert published.status == WorkflowStatus.ARCHIVED


@pytest.mark.django_db(transaction=True)
def test_lineage_head_cannot_mark_itself_published(composed_tables: None) -> None:
    """The public transition accepts only the manager's new snapshot target."""

    del composed_tables
    with system_context(reason="test publication-only transition"):
        workflow = Workflow.objects.create(name="Incomplete head")
        with pytest.raises(ValidationError, match="Only a copied version"):
            workflow.mark_published()
        workflow.refresh_from_db()
        assert workflow.status == WorkflowStatus.DRAFT
        assert workflow.version == 0


@pytest.mark.django_db(transaction=True)
def test_publication_inside_changed_batch_records_pending_revision(composed_tables: None) -> None:
    """A snapshot pins the revision committed by its surrounding write batch."""

    del composed_tables
    with system_context(reason="test pending publication revision"):
        workflow = Workflow.objects.create(name="Pending publication")
        with Workflow.objects._definition_write((workflow.pk,)) as session:
            Step(
                workflow=workflow,
                key="wait",
                name="Wait",
                step_class="wait",
                config={"until": "2030-01-02T03:04:05Z"},
                is_entry=True,
            ).save(session=session)
            published = workflow.publish(session=session)

        workflow.refresh_from_db()
        published.refresh_from_db()
        assert workflow.draft_revision == 1
        assert published.draft_revision == workflow.draft_revision


@pytest.mark.django_db(transaction=True)
def test_explicit_actor_bound_child_save_and_cascade_delete(composed_tables: None) -> None:
    """Internal lock reads retain an explicitly bound caller's model policy chain."""

    del composed_tables
    with system_context(reason="seed explicit definition actor"):
        admin = create_platform_admin(username="definition-admin", password="admin")
        workflow = Workflow.objects.create(name="Actor-bound")
        source = Step.objects.create(workflow=workflow, key="source", name="Source", step_class="fixture")
        target = Step.objects.create(workflow=workflow, key="target", name="Target", step_class="fixture")
        Edge.objects.create(workflow=workflow, source=source, target=target)

    bound = Step.objects.as_user(admin).get(pk=source.pk)
    bound.name = "Renamed"
    bound.save(update_fields={"name"})
    Step.objects.as_user(admin).get(pk=source.pk).delete()

    assert not Edge.objects.as_user(admin).filter(workflow=workflow).exists()


@pytest.mark.django_db(transaction=True)
def test_explicit_actor_bound_publication_carries_policy_to_copies(composed_tables: None) -> None:
    """Publication retains the caller binding through manager fetches and copied rows."""

    del composed_tables
    with system_context(reason="seed explicit publication actor"):
        admin = create_platform_admin(username="publication-admin", password="admin")
        workflow = Workflow.objects.create(name="Actor publication")
        Step.objects.create(
            workflow=workflow,
            key="wait",
            name="Wait",
            step_class="wait",
            config={"until": "2030-01-02T03:04:05Z"},
            is_entry=True,
        )

    published = Workflow.objects.as_user(admin).get(pk=workflow.pk).publish()
    assert published.status == WorkflowStatus.PUBLISHED
    assert published.steps.as_user(admin).count() == 1


@pytest.mark.django_db(transaction=True)
def test_denied_explicit_actor_cascade_rolls_back_children(composed_tables: None) -> None:
    """A denied parent delete cannot leak its internally collected edge deletion."""

    del composed_tables
    with system_context(reason="seed denied definition actor"):
        stranger = User.objects.create_user(username="definition-stranger")
        workflow = Workflow.objects.create(name="Denied cascade")
        source = Step.objects.create(workflow=workflow, key="source", name="Source", step_class="fixture")
        target = Step.objects.create(workflow=workflow, key="target", name="Target", step_class="fixture")
        edge = Edge.objects.create(workflow=workflow, source=source, target=target)
        denied = Step.objects.get(pk=source.pk).as_user(stranger)

    with pytest.raises(PermissionDenied):
        denied.delete()

    with system_context(reason="verify denied definition cascade"):
        assert Step.objects.filter(pk=source.pk).exists()
        assert Edge.objects.filter(pk=edge.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_explicit_definition_session_cannot_expand_the_locked_lineage(composed_tables: None) -> None:
    del composed_tables
    with system_context(reason="test explicit definition session scope"):
        first = Workflow.objects.create(name="Locked")
        second = Workflow.objects.create(name="Unrelated")
        with Workflow.objects._definition_write((first.pk,)) as session:
            with pytest.raises(RuntimeError, match="cannot expand"):
                Step(workflow=second, key="late", name="Late", step_class="fixture").save(session=session)
        second.refresh_from_db()
        assert second.draft_revision == 0
        assert not second.steps.exists()


@pytest.mark.django_db(transaction=True)
def test_definition_session_does_not_reopen_an_immutable_parent(composed_tables: None) -> None:
    del composed_tables
    with system_context(reason="test explicit session retains immutable history"):
        draft = Workflow.objects.create(name="Immutable")
        Step.objects.create(workflow=draft, key="entry", name="Entry", step_class="agent_session", is_entry=True)
        published = draft.publish()
        with Workflow.objects._definition_write(
            (published.pk,), _allow_status_transition=True
        ) as session:
            with pytest.raises(ValidationError, match="immutable"):
                Step(workflow=published, key="late", name="Late", step_class="fixture").save(session=session)


@pytest.mark.django_db(transaction=True)
def test_definition_session_requires_its_lexical_transaction(composed_tables: None) -> None:
    del composed_tables
    with system_context(reason="test explicit session transaction scope"):
        draft = Workflow.objects.create(name="Lexical")
        with Workflow.objects._definition_write((draft.pk,)) as session:
            pass
        with pytest.raises(RuntimeError, match="manager's transaction"):
            Step(workflow=draft, key="late", name="Late", step_class="fixture").save(session=session)
