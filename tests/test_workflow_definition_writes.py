"""Focused contracts for workflow definition write ownership."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from rebac import PermissionDenied, app_settings, system_context
from rebac.roles import grant

from angee.workflows.models import WorkflowStatus
from tests.workflows import Edge, Step, Workflow

pytest_plugins = ("tests.workflows",)
User = get_user_model()


@pytest.mark.django_db(transaction=True)
def test_input_binding_is_versioned_and_copied_with_the_definition(workflow_tables: None) -> None:
    del workflow_tables
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
def test_definition_rows_advance_revision_once_per_locked_batch(workflow_tables: None) -> None:
    """Nested legacy row saves share one lineage revision owner."""

    del workflow_tables
    with system_context(reason="test definition revision batch"):
        workflow = Workflow.objects.create(name="Batch")
        with Workflow.objects._definition_write((workflow.pk,)):
            first = Step.objects.create(workflow=workflow, key="first", name="First", is_entry=True)
            second = Step.objects.create(workflow=workflow, key="second", name="Second")
            Edge.objects.create(workflow=workflow, source=first, target=second, condition="timer")

        workflow.refresh_from_db()
        assert workflow.draft_revision == 1

        with Workflow.objects._definition_write((workflow.pk,)):
            first.save(update_fields={"name"})
            second.save(update_fields={"position"})
        workflow.refresh_from_db()
        assert workflow.draft_revision == 1


@pytest.mark.django_db(transaction=True)
def test_noop_and_update_fields_compare_only_persisted_content(workflow_tables: None) -> None:
    """Incidental saves and unsaved attributes do not fabricate revisions."""

    del workflow_tables
    with system_context(reason="test definition no-op revisions"):
        workflow = Workflow.objects.create(name="No-op")
        step = Step.objects.create(workflow=workflow, key="start", name="Start", is_entry=True)
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
def test_workflow_definition_fields_join_the_revision_owner(workflow_tables: None) -> None:
    """Head settings revise only when a persisted definition field changes."""

    del workflow_tables
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
def test_stale_workflow_instance_cannot_regress_database_revision(workflow_tables: None) -> None:
    """The database revision remains authoritative across stale head saves."""

    del workflow_tables
    with system_context(reason="test stale workflow revision"):
        workflow = Workflow.objects.create(name="Stale")
        stale = Workflow.objects.get(pk=workflow.pk)
        Step.objects.create(workflow=workflow, key="start", name="Start")

        stale.save()
        stale.refresh_from_db()
        assert stale.draft_revision == 1

        stale.description = "Changed from stale object"
        stale.save()
        stale.refresh_from_db()
        assert stale.draft_revision == 2


@pytest.mark.django_db(transaction=True)
def test_step_delete_owns_incident_edges_and_one_revision(workflow_tables: None) -> None:
    """Step deletion explicitly performs Collector-skipped edge cascades."""

    del workflow_tables
    with system_context(reason="test definition child cascade"):
        workflow = Workflow.objects.create(name="Delete")
        source = Step.objects.create(workflow=workflow, key="source", name="Source", is_entry=True)
        target = Step.objects.create(workflow=workflow, key="target", name="Target")
        Edge.objects.create(workflow=workflow, source=source, target=target)
        workflow.refresh_from_db()
        revision = workflow.draft_revision

        Step.objects.filter(pk=source.pk).delete()

        workflow.refresh_from_db()
        assert workflow.draft_revision == revision + 1
        assert not Edge.objects.filter(workflow=workflow).exists()


@pytest.mark.django_db(transaction=True)
def test_definition_bulk_writes_are_rejected(workflow_tables: None) -> None:
    """Public collection APIs cannot bypass the locked instance owner."""

    del workflow_tables
    with system_context(reason="test guarded definition collections"):
        workflow = Workflow.objects.create(name="Guarded")
        step = Step.objects.create(workflow=workflow, key="start", name="Start")
        target = Step.objects.create(workflow=workflow, key="target", name="Target")
        edge = Edge.objects.create(workflow=workflow, source=step, target=target)

        for queryset, fields in (
            (Workflow.objects.filter(pk=workflow.pk), {"name": "Bypass"}),
            (Step.objects.filter(pk=step.pk), {"name": "Bypass"}),
            (Edge.objects.filter(pk=edge.pk), {"condition": "bypass"}),
        ):
            with pytest.raises(TypeError, match=r"QuerySet\.update"):
                queryset.update(**fields)
        with pytest.raises(TypeError, match="bulk_create"):
            Step.objects.bulk_create([Step(workflow=workflow, key="bulk", name="Bulk")])
        with pytest.raises(TypeError, match="bulk_update"):
            Step.objects.bulk_update([step], ["name"])


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("step_class", "config"),
    [("wait", {}), ("gate", {}), ("map", {})],
)
def test_incomplete_typed_step_configs_remain_storable_drafts(
    workflow_tables: None,
    step_class: str,
    config: dict[str, object],
) -> None:
    """Readiness gaps remain stored as object-shaped draft config."""

    del workflow_tables
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
def test_incomplete_retry_policy_remains_a_storable_draft(workflow_tables: None) -> None:
    """Retry value constraints are reported by readiness rather than storage."""

    del workflow_tables
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
def test_failed_batch_rolls_back_rows_and_revision(workflow_tables: None) -> None:
    """A nested failure leaves both definition rows and revision unchanged."""

    del workflow_tables
    with system_context(reason="test definition rollback"):
        workflow = Workflow.objects.create(name="Rollback")
        other = Workflow.objects.create(name="Other")
        with pytest.raises(ValidationError, match="same workflow"):
            with Workflow.objects._definition_write((workflow.pk, other.pk)):
                source = Step.objects.create(workflow=workflow, key="source", name="Source")
                target = Step.objects.create(workflow=other, key="target", name="Target")
                Edge.objects.create(workflow=workflow, source=source, target=target)

        workflow.refresh_from_db()
        assert workflow.draft_revision == 0
        assert not Step.objects.filter(workflow=workflow).exists()


@pytest.mark.django_db(transaction=True)
def test_moves_revise_both_parents_and_reject_connected_steps(workflow_tables: None) -> None:
    """Moves own old and new lineages, while connected nodes cannot strand edges."""

    del workflow_tables
    with system_context(reason="test definition moves"):
        first = Workflow.objects.create(name="First")
        second = Workflow.objects.create(name="Second")
        movable = Step.objects.create(workflow=first, key="movable", name="Movable")
        first.refresh_from_db()
        first_revision = first.draft_revision

        movable.workflow = second
        movable.save(update_fields={"workflow"})
        first.refresh_from_db()
        second.refresh_from_db()
        assert first.draft_revision == first_revision + 1
        assert second.draft_revision == 1

        anchor = Step.objects.create(workflow=second, key="anchor", name="Anchor")
        Edge.objects.create(workflow=second, source=anchor, target=movable)
        movable.workflow = first
        with pytest.raises(ValidationError, match="connected step"):
            movable.save(update_fields={"workflow"})


@pytest.mark.django_db(transaction=True)
def test_fresh_non_draft_heads_and_identity_reassignment_are_rejected(workflow_tables: None) -> None:
    """Publication identity fields remain manager-owned across every save path."""

    del workflow_tables
    with system_context(reason="test workflow publication identity"):
        with pytest.raises(ValidationError, match="revision-zero drafts"):
            Workflow.objects.create(name="Forged", status=WorkflowStatus.PUBLISHED)

        workflow = Workflow.objects.create(name="Identity")
        workflow.version = 3
        with pytest.raises(ValidationError, match="versions are immutable"):
            workflow.save()


@pytest.mark.django_db(transaction=True)
def test_published_parent_rejects_child_mutation_even_under_sudo(workflow_tables: None) -> None:
    """System authorization does not weaken immutable definition history."""

    del workflow_tables
    with system_context(reason="test immutable definition"):
        published = Workflow.objects.create(name="Historical")
        # Test setup uses the narrow status transition contract, then exercises the public writer.
        published.status = WorkflowStatus.PUBLISHED
        published._allow_immutable_status_save = True
        published.save(update_fields={"status"})
        del published._allow_immutable_status_save

        with pytest.raises(ValidationError, match="immutable"):
            Step.objects.create(workflow=published, key="late", name="Late")


@pytest.mark.django_db(transaction=True)
def test_publication_records_revision_and_protects_lineage_history(workflow_tables: None) -> None:
    """A publication pins its source revision and prevents deletion of its head."""

    del workflow_tables
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
def test_lineage_head_cannot_mark_itself_published(workflow_tables: None) -> None:
    """The public transition accepts only the manager's new snapshot target."""

    del workflow_tables
    with system_context(reason="test publication-only transition"):
        workflow = Workflow.objects.create(name="Incomplete head")
        with pytest.raises(ValidationError, match=r"created by publish\(\)"):
            workflow.mark_published()
        workflow.refresh_from_db()
        assert workflow.status == WorkflowStatus.DRAFT
        assert workflow.version == 0


@pytest.mark.django_db(transaction=True)
def test_publication_inside_changed_batch_records_pending_revision(workflow_tables: None) -> None:
    """A snapshot pins the revision committed by its surrounding write batch."""

    del workflow_tables
    with system_context(reason="test pending publication revision"):
        workflow = Workflow.objects.create(name="Pending publication")
        with Workflow.objects._definition_write((workflow.pk,)):
            Step.objects.create(
                workflow=workflow,
                key="wait",
                name="Wait",
                step_class="wait",
                config={"until": "2030-01-02T03:04:05Z"},
                is_entry=True,
            )
            published = workflow.publish()

        workflow.refresh_from_db()
        published.refresh_from_db()
        assert workflow.draft_revision == 1
        assert published.draft_revision == workflow.draft_revision


@pytest.mark.django_db(transaction=True)
def test_explicit_actor_bound_child_save_and_cascade_delete(workflow_tables: None) -> None:
    """Internal lock reads retain an explicitly bound caller's model policy chain."""

    del workflow_tables
    with system_context(reason="seed explicit definition actor"):
        admin = User.objects.create_superuser(username="definition-admin", password="admin")
        grant(actor=admin, role=app_settings.REBAC_UNIVERSAL_ADMIN_ROLE)
        workflow = Workflow.objects.create(name="Actor-bound")
        source = Step.objects.create(workflow=workflow, key="source", name="Source")
        target = Step.objects.create(workflow=workflow, key="target", name="Target")
        Edge.objects.create(workflow=workflow, source=source, target=target)

    bound = Step.objects.as_user(admin).get(pk=source.pk)
    bound.name = "Renamed"
    bound.save(update_fields={"name"})
    Step.objects.as_user(admin).get(pk=source.pk).delete()

    assert not Edge.objects.as_user(admin).filter(workflow=workflow).exists()


@pytest.mark.django_db(transaction=True)
def test_explicit_actor_bound_publication_carries_policy_to_copies(workflow_tables: None) -> None:
    """Publication retains the caller binding through manager fetches and copied rows."""

    del workflow_tables
    with system_context(reason="seed explicit publication actor"):
        admin = User.objects.create_superuser(username="publication-admin", password="admin")
        grant(actor=admin, role=app_settings.REBAC_UNIVERSAL_ADMIN_ROLE)
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
def test_denied_explicit_actor_cascade_rolls_back_children(workflow_tables: None) -> None:
    """A denied parent delete cannot leak its internally collected edge deletion."""

    del workflow_tables
    with system_context(reason="seed denied definition actor"):
        stranger = User.objects.create_user(username="definition-stranger")
        workflow = Workflow.objects.create(name="Denied cascade")
        source = Step.objects.create(workflow=workflow, key="source", name="Source")
        target = Step.objects.create(workflow=workflow, key="target", name="Target")
        edge = Edge.objects.create(workflow=workflow, source=source, target=target)
        denied = Step.objects.get(pk=source.pk).as_user(stranger)

    with pytest.raises(PermissionDenied):
        denied.delete()

    with system_context(reason="verify denied definition cascade"):
        assert Step.objects.filter(pk=source.pk).exists()
        assert Edge.objects.filter(pk=edge.pk).exists()
