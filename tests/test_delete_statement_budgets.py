"""Statement budgets for a native project delete with edge, chatter and run owners."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from rebac import system_context

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
def delete_costs(execution, workflow_step_classes) -> tuple[DeleteCost, DeleteCost]:
    """Measure a system-context queryset delete, excluding actor GraphQL validation.

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
                RecordRefNullableEdge.objects.create(target=record)
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

    return measure(1), measure(10)


@pytest.mark.django_db(transaction=True)
def test_delete_cost_grows_only_with_collected_owner_rows(delete_costs: tuple[DeleteCost, DeleteCost]) -> None:
    """Pin current one/ten ceilings for system-context QuerySet.delete, not GraphQL."""

    one, ten = delete_costs
    # P10b batches run cancellation, edge policy and chatter after L10's
    # collector signal; the current linear ceilings fall when that lands.
    assert ten.statements - one.statements <= 9 * 165
    assert ten.revisions - one.revisions <= 9 * 3
    assert one.audits <= 21 and ten.audits <= 210
    if connection.vendor == "sqlite":
        assert one.statements <= 180 and ten.statements <= 1500
        assert one.total_bytes <= 90_000 and ten.total_bytes <= 800_000
        assert ten.largest_bytes <= 6_000
    else:
        assert one.statements <= 220 and ten.statements <= 1800
        assert one.total_bytes <= 120_000 and ten.total_bytes <= 1_100_000
        assert ten.largest_bytes <= 10_000


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("law", ("revisions", "audits", "statements"))
@pytest.mark.xfail(strict=True, reason="P10b/L10: collector batches must remove per-row delete overhead")
def test_delete_cost_batch_laws(delete_costs: tuple[DeleteCost, DeleteCost], law: str) -> None:
    """Keep the unmet flat revision/audit and owner-bounded statement laws visible."""

    one, ten = delete_costs
    if law == "revisions":
        assert ten.revisions == one.revisions
    elif law == "audits":
        assert ten.audits == one.audits
    else:
        # Six admitting edges plus ten possible host/run/chatter model owners
        # leave at most sixteen statements per added host after batching.
        assert ten.statements - one.statements <= 9 * 16
