"""PostgreSQL proposal submission and deletion share round-first row locks."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from rebac import actor_context, system_context
from strawberry import Schema
from strawberry.types import ExecutionResult

from angee.base.exceptions import RecordAccessSubjectRefused
from angee.messaging.testing.models import Person
from angee.projects.testing.models import Project, Task
from tests.lock_waits import submit, wait_for_lock
from tests.proposals_campaign import ProposalCampaign, as_actor
from tests.proposals_campaign import campaign as campaign
from tests.proposals_campaign import campaign_schema as campaign_schema
from tests.proposals_campaign import proposal_delete_schema as proposal_delete_schema
from tests.proposals_models import Proposal, Round

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


@pytest.mark.parametrize("path", ("instance", "queryset", "round_cascade", "task_cascade", "project_cascade"))
def test_proposal_delete_waits_for_round_without_blocking_submission(
    campaign: ProposalCampaign, path: str,
) -> None:
    """A round holder can submit while deletion waits, then deletion retains the receipt."""

    round = campaign.round(target_kind="task" if path == "task_cascade" else "project")
    if path == "project_cascade":
        # Release the milestone's independent PROTECT relation so this exercises
        # the proposal receiver rather than failing during Django collection.
        with system_context(reason="tests.proposals.concurrent_delete.target"):
            round.clarifications_shared_until = None
            round.save(update_fields=("clarifications_shared_until",))
    proposal = campaign.admit(round, "responder")

    def delete() -> None:
        with system_context(reason="tests.proposals.concurrent_delete"):
            if path == "instance":
                Proposal._base_manager.get(pk=proposal.pk).delete()
            else:
                model, pk = {
                    "queryset": (Proposal, proposal.pk),
                    "round_cascade": (Round, round.pk),
                    "task_cascade": (Task, round.task_id),
                    "project_cascade": (Project, round.project_id),
                }[path]
                model.objects.filter(pk=pk).delete()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with system_context(reason="tests.proposals.concurrent_submit"), transaction.atomic():
            Round.system_queryset(lock=("self",)).get(pk=round.pk)
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout TO '5s'")
            deletion, pid = submit(pool, delete)
            wait_for_lock(pid, deletion)
            submitted = Proposal._base_manager.get(pk=proposal.pk).submit()
        with pytest.raises(ValidationError, match="Only an untouched draft proposal can be deleted"):
            deletion.result(timeout=10)

    retained = Proposal._base_manager.get(pk=proposal.pk)
    assert retained.state == "submitted"
    assert retained.submitted_at == submitted.submitted_at == campaign.now
    assert Round._base_manager.filter(pk=round.pk).exists()
    target = Task if path == "task_cascade" else Project
    assert target._base_manager.filter(pk=round.task_id if path == "task_cascade" else round.project_id).exists()


def test_hasura_proposal_delete_waits_for_round_without_blocking_concurrent_submit(
    campaign: ProposalCampaign, proposal_delete_schema: Schema,
) -> None:
    """A Hasura confirmation waits round-first and reports the concurrent submission refusal."""

    round = campaign.round()
    proposal = campaign.admit(round, "responder")
    manager, responder = campaign.person("facilitator"), campaign.person("responder")

    def delete() -> ExecutionResult:
        with actor_context(manager):
            return proposal_delete_schema.execute_sync(
                "mutation($id: String!) { delete_proposals_by_pk(id: $id) { id } }",
                variable_values={"id": proposal.public_id},
                context_value=SimpleNamespace(request=SimpleNamespace(user=manager)),
            )

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            Round.system_queryset(lock=("self",)).get(pk=round.pk)
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout TO '5s'")
            deletion, pid = submit(pool, delete)
            wait_for_lock(pid, deletion)
            with actor_context(responder):
                submitted = as_actor(proposal, responder).submit()
        result = deletion.result(timeout=10)

    assert result.errors is not None and len(result.errors) == 1
    assert result.errors[0].message == "Only an untouched draft proposal can be deleted."
    assert result.errors[0].extensions == {"code": "BAD_USER_INPUT"}
    assert result.data == {"delete_proposals_by_pk": None}
    retained = Proposal._base_manager.get(pk=proposal.pk)
    assert retained.state == "submitted"
    assert retained.submitted_at == submitted.submitted_at == campaign.now
    assert retained.submitted_by_id == responder.pk
    assert Round._base_manager.filter(pk=round.pk).exists()


def test_direct_grant_waits_for_requester_change_and_revalidates_the_fresh_round(campaign: ProposalCampaign) -> None:
    round = campaign.round()
    proposal = campaign.admit(round, "responder", track=True)
    manager, requester = campaign.person("facilitator"), campaign.person("requester")
    with system_context(reason="tests.proposals.concurrent_requester.party"):
        party = Person.objects.for_user(requester)

    def grant() -> None:
        with actor_context(manager):
            as_actor(proposal, manager).grant_record_access("reader", requester)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            locked = Round.system_queryset(lock=("self",)).get(pk=round.pk)
            operation, pid = submit(pool, grant)
            wait_for_lock(pid, operation)
            with system_context(reason="tests.proposals.concurrent_requester.change"):
                locked.requester_party = party
                locked.save(update_fields=("requester_party",))
        with pytest.raises(RecordAccessSubjectRefused):
            operation.result(timeout=10)
