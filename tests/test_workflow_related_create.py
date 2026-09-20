"""Create-time authorization for workflow-owned rows."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import override_settings
from rebac import PermissionDenied, actor_context, system_context

from tests.workflows import Edge, Step, Trigger, Workflow

User = get_user_model()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("storage", ("denormalized", "registry"))
def test_workflow_children_preflight_the_proposed_parent_relation(
    workflow_tables: None,
    storage: str,
) -> None:
    """The workflow writer may create children; an unrelated actor may not."""

    del workflow_tables
    with override_settings(REBAC_LOCAL_BACKEND_STORAGE=storage):
        call_command("rebac", "sync", verbosity=0)
        writer = User.objects.create_user(username=f"workflow-writer-{storage}")
        outsider = User.objects.create_user(username=f"workflow-outsider-{storage}")
        with system_context(reason="test workflow related-create owner"):
            workflow = Workflow.objects.create(
                name=f"Related create {storage}",
                created_by=writer,
                updated_by=writer,
            )

        with actor_context(writer):
            source = Step.objects.create(
                workflow=workflow, key="source", name="Source", step_class="fixture", is_entry=True
            )
            target = Step.objects.create(workflow=workflow, key="target", name="Target", step_class="fixture")
            edge = Edge.objects.create(workflow=workflow, source=source, target=target)
            trigger = Trigger.objects.create(workflow=workflow)

        assert {source.created_by_id, target.created_by_id, edge.created_by_id, trigger.created_by_id} == {writer.pk}

        with actor_context(outsider):
            with pytest.raises(PermissionDenied):
                Step.objects.create(workflow=workflow, key="denied", name="Denied", step_class="fixture")
            with pytest.raises(PermissionDenied):
                Edge.objects.create(workflow=workflow, source=source, target=target)
            with pytest.raises(PermissionDenied):
                Trigger.objects.create(workflow=workflow)
