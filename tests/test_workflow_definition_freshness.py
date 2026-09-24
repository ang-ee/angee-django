"""Workflow definition freshness."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from rebac import system_context

from tests.workflows import Edge, Step, Workflow


@pytest.mark.django_db(transaction=True)
def test_step_config_hook_reads_deferred_fields(workflow_tables: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Step config hook reads deferred fields."""
    with system_context(reason="step deferred config setup"):
        workflow = Workflow.objects.create(name="Deferred config")
        step = Step.objects.create(
            workflow=workflow, key="entry", name="Entry", step_class="fixture", config={"retained": 3}
        )
        deferred = Step.objects.only("pk").get(pk=step.pk)
    assert {"config", "step_class"} <= deferred.get_deferred_fields()
    with system_context(reason="step deferred config validation"):
        deferred.validate_impl_configs()
        assert deferred.config == {"retained": 3}


@pytest.mark.django_db(transaction=True)
def test_edge_save_rejects_stale_cached_endpoint_ancestry_on_default(workflow_tables: None) -> None:
    """Edge save rejects stale cached endpoint ancestry on default."""
    del workflow_tables
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
