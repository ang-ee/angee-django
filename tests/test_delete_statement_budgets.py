"""Statement budgets for a native project delete with edge, chatter and run owners."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pytest
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import connection, models, transaction
from django.test.utils import CaptureQueriesContext
from rebac import system_context

from angee.base.models import refs as record_refs
from angee.iam.models.base import ProtectedDataModel
from angee.iam.models.refs import ProtectedRecordRefMixin
from angee.iam.testing.queries import is_permission_audit_insert
from angee.projects.testing.models import Project
from angee.workflows.testing.drivers import load_workflow
from angee.workflows.testing.models import WorkflowRun
from tests.recordrefdemo.models import RecordRefNullableEdge
from tests.workflow_steps import document


@dataclass(frozen=True)
class DeleteCost:
    statements: int
    revisions: int
    audits: int
    total_bytes: int
    largest_bytes: int


@pytest.fixture
def measure_delete_cost(execution, workflow_step_classes) -> Callable[[int], DeleteCost]:
    """Measure a system-context queryset delete, excluding actor GraphQL validation.

    The always-installed scopedemo app declares both additional task hierarchies,
    so the collector sees the same model graph regardless of pytest collection.
    Every host carries a SET_NULL record ref, one chatter message and a live run.
    Seed and system-context entry happen outside the capture; the native delete,
    all collectors, receivers and nested bypasses happen inside it.
    """

    actor, _sent = execution
    with system_context(reason="tests.delete_budget.workflow_fixture"):
        workflow = load_workflow(document("entry"), subject_model="projects.Project")

    def measure(size: int) -> DeleteCost:
        with transaction.atomic(), system_context(reason="tests.delete_budget.seed"):
            records = [Project.objects.create(title=f"Delete budget {index}", owner=actor) for index in range(size)]
            for record in records:
                record.message_log("One chatter message")
                RecordRefNullableEdge._default_manager.create(target=record)
                WorkflowRun.objects.start(workflow, actor=actor, subject=record)
            with CaptureQueriesContext(connection) as captured:
                Project._base_manager.filter(pk__in=[record.pk for record in records]).delete()
            statements = [query["sql"] for query in captured]
            sizes = [len(statement.encode("utf-8")) for statement in statements]
            cost = DeleteCost(
                statements=len(statements),
                revisions=sum(
                    statement.lstrip().upper().startswith("SELECT")
                    and "rebac_schemageneration" in statement.lower()
                    for statement in statements
                ),
                audits=sum(is_permission_audit_insert(statement, connection) for statement in statements),
                total_bytes=sum(sizes),
                largest_bytes=max(sizes, default=0),
            )
            transaction.set_rollback(True)
        return cost

    return measure


@pytest.fixture
def delete_costs(measure_delete_cost: Callable[[int], DeleteCost]) -> tuple[DeleteCost, DeleteCost, DeleteCost]:
    return measure_delete_cost(1), measure_delete_cost(5), measure_delete_cost(10)


@pytest.mark.django_db(transaction=True)
def test_delete_cost_grows_only_with_collected_owner_rows(
    delete_costs: tuple[DeleteCost, DeleteCost, DeleteCost],
) -> None:
    """Pin collected-owner ceilings for system-context QuerySet.delete."""

    one, five, ten = delete_costs
    # re-measure after integration
    assert ten.statements - one.statements <= 9 * 16
    assert one.statements <= five.statements <= ten.statements
    assert one.revisions == five.revisions == ten.revisions == 2
    assert one.audits == five.audits == ten.audits == 12
    # Measured on both backends: alone 154/158/168, with two task modules 160/164/174.
    # Headroom is +1 per empty record-edge model per delete scope (three scopes here).
    assert one.statements <= 160 and ten.statements <= 174
    if connection.vendor == "sqlite":
        # re-measure after integration
        assert one.total_bytes <= 105_000 and ten.total_bytes <= 150_000
        assert ten.largest_bytes <= 24_000
    else:
        assert one.total_bytes <= 120_000 and ten.total_bytes <= 160_000
        assert ten.largest_bytes <= 24_000


@pytest.mark.django_db(transaction=True)
def test_empty_registered_record_edge_costs_one_statement_per_delete_scope_without_audit(
    measure_delete_cost: Callable[[int], DeleteCost],
) -> None:
    """An extra concrete edge adds one structural probe in each of three scopes."""
    measure_delete_cost(1)  # Warm Django's content-type and relation caches equally.
    baseline = measure_delete_cost(1)

    class EmptyBudgetEdge(ProtectedRecordRefMixin, ProtectedDataModel, concrete=True):
        sqid_prefix = "ebe_"
        on_target_delete = models.CASCADE
        content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
        object_id = models.PositiveBigIntegerField()
        target = GenericForeignKey("content_type", "object_id")

        class Meta:
            app_label = "scopedemo"
            db_table = "test_empty_budget_edge"
            rebac_resource_type = "scopedemo/empty_budget_edge"

    try:
        with connection.schema_editor() as editor:
            editor.create_model(EmptyBudgetEdge)
        extra = measure_delete_cost(1)
        assert extra.audits == baseline.audits
        assert extra.statements == baseline.statements + 3
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(EmptyBudgetEdge)
        record_refs.apps.all_models["scopedemo"].pop("emptybudgetedge")
        record_refs.apps.clear_cache()
        record_refs._target_edge_index.clear()
