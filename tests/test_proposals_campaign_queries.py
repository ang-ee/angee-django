"""Native list projections keep bounded query counts at five and fifty rows."""

from __future__ import annotations

from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, system_context, to_subject_ref
from rebac.backends import backend
from rebac.backends.local import LocalBackend
from rebac.backends.local_query import LocalQueryScope

from angee.graphql.capabilities import held_permissions, permission_annotations
from tests.projects_models import Task
from tests.proposals_campaign import ProposalCampaign, as_actor
from tests.proposals_models import Proposal, Round

pytest_plugins = ("tests.proposals_campaign",)


def test_proposal_track_status_list_stays_within_eleven_queries_at_five_and_fifty_rows(
    campaign: ProposalCampaign,
) -> None:
    c = campaign
    manager = c.person("facilitator")
    round = c.round()
    for index in range(50):
        c.admit(round, f"responder-{index}")

    def read(limit: int) -> list[Any]:
        return list(
            Proposal.objects.as_user(manager)
            .order_by("pk")
            .annotate(
                _track_status=Proposal.track_status_expression(manager),
            )[:limit]
        )

    with actor_context(manager):
        read(5)  # Warm schema/ContentType caches, never the row result.
        counts = []
        for limit in (5, 50):
            with CaptureQueriesContext(connection) as queries:
                rows = read(limit)
            assert len(rows) == limit
            assert all(row._track_status is None for row in rows)
            counts.append(len(queries))
        assert counts[0] == counts[1], counts
        assert max(counts) <= 11, counts


@pytest.mark.parametrize("roster,budget", ((False, 13), (True, 22)), ids=("capabilities", "roster"))
def test_round_capability_and_roster_lists_keep_the_reviewed_query_budgets(
    campaign: ProposalCampaign,
    roster: bool,
    budget: int,
) -> None:
    c = campaign
    manager = c.person("facilitator")
    responder = c.person("responder")
    for _ in range(50):
        round = c.round(roster_visibility="named")
        c.admit(round, "responder")

    def read(limit: int, actor: Any, roster: bool) -> list[Any]:
        rows = Round.objects.as_user(actor).order_by("pk")
        if roster:
            rows = rows.annotate(**permission_annotations(Round, ("see_roster", "roster_status")))
            rows = rows.prefetch_related(Round.roster_prefetch())
        else:
            rows = rows.annotate(
                _can_open=Round.can_open_expression(actor),
                _can_admit=Round.can_admit_expression(actor),
            )
        result = list(rows[:limit])
        if roster:
            for row in result:
                held = held_permissions(row, ("see_roster", "roster_status"))
                entries = row.roster(permitted="see_roster" in held, status="roster_status" in held)
                assert [entry.user_id for entry in entries] == [responder.pk]
                assert entries[0].track_status is None
        else:
            assert all(row._can_open and row._can_admit for row in result)
        return result

    actor = responder if roster else manager
    with actor_context(actor):
        read(5, actor, roster)
        counts = []
        for limit in (5, 50):
            with CaptureQueriesContext(connection) as queries:
                rows = read(limit, actor, roster)
            assert len(rows) == limit
            counts.append(len(queries))
        assert counts[0] == counts[1], counts
        assert max(counts) <= budget, counts


def test_round_and_proposal_read_scopes_compile_to_sql_for_non_admins(campaign: ProposalCampaign) -> None:
    actor = campaign.person("outsider")
    local = backend()
    assert isinstance(local, LocalBackend)
    for model in (Round, Proposal):
        # A recursive role must fail this proof rather than silently enumerate ids.
        predicate = LocalQueryScope(local, to_subject_ref(actor), "default").predicate(
            model,
            "read",
            model._meta.rebac_resource_type,
        )
        query = model._base_manager.filter(predicate)
        sql, _params = query.query.sql_with_params()
        assert "SELECT" in sql.upper()


def test_waiting_list_is_one_query_per_page_and_private_to_each_recipient(campaign: ProposalCampaign) -> None:
    c = campaign
    round = c.round()
    for seat in ("responder", "peer", "third"):
        c.admit(round, seat)
    manager = c.person("facilitator")
    with actor_context(manager):
        questions = [
            as_actor(round, manager).ask(f"Question {index}", "Reply", recipient="responders") for index in range(50)
        ]
    with actor_context(c.person("responder")):
        as_actor(questions[0], c.person("responder")).message_post("My answer")

    for actor, expected in (
        (manager, {c.person("peer").pk, c.person("third").pk}),
        (c.person("peer"), {c.person("peer").pk}),
        (c.person("responder"), set()),
    ):
        with actor_context(actor):
            # Schema setup is outside the budget: the page executes one SQL projection.
            expression = Task.clarification_waiting_expression(to_subject_ref(actor))
            for limit in (5, 50):
                rows = (
                    Task.objects.as_user(actor)
                    .scoped()
                    .filter(clarification_round=round)
                    .order_by("pk")
                    .annotate(
                        _clarification_waiting=expression,
                    )
                    .values_list("_clarification_waiting", flat=True)[:limit]
                )
                with CaptureQueriesContext(connection) as queries:
                    waiting = list(rows)
                assert len(queries) == 1, [query["sql"] for query in queries]
                assert len(waiting) == limit
                assert {entry["id"] for entry in waiting[0]} == expected
                all_waiting = {c.person("responder").pk, c.person("peer").pk, c.person("third").pk}
                assert all(
                    {entry["id"] for entry in row} == (all_waiting if actor == manager else {actor.pk})
                    for row in waiting[1:]
                )
    with actor_context(manager):
        as_actor(round, manager).remove_responder(c.person("third"))
        waiting = as_actor(questions[0], manager).clarification_waiting()
    assert {entry["id"] for entry in waiting} == {c.person("peer").pk}
    assert Task._base_manager.filter(clarification_round=round).count() == 50


def test_single_assignee_stops_waiting_after_posting_and_other_threads_do_not_count(campaign: ProposalCampaign) -> None:
    c = campaign
    round = c.round()
    c.admit(round, "responder")
    manager, recipient = c.person("facilitator"), c.person("recipient")
    with actor_context(manager):
        question = as_actor(round, manager).ask("Assigned", "Reply", recipient=recipient)
        other = as_actor(round, manager).ask("Another", "Reply", recipient=recipient)
    with actor_context(recipient):
        as_actor(other, recipient).message_post("Other answer")
    with actor_context(manager):
        assert {row["id"] for row in as_actor(question, manager).clarification_waiting()} == {recipient.pk}
    with actor_context(recipient):
        as_actor(question, recipient).message_post("This answer")
    with actor_context(manager):
        assert as_actor(question, manager).clarification_waiting() == []


@pytest.mark.skipif(connection.vendor != "postgresql", reason="Native PostgreSQL JSON aggregation is required.")
def test_postgresql_waiting_projection_executes_with_empty_and_populated_recipients(campaign: ProposalCampaign) -> None:
    c = campaign
    round = c.round()
    c.admit(round, "responder")
    manager = c.person("facilitator")
    with actor_context(manager):
        task = as_actor(round, manager).ask("All recipients", "Reply", recipient="responders")
        expression = Task.clarification_waiting_expression(to_subject_ref(manager))
    with system_context(reason="tests.proposals.campaign.postgresql"):
        rows = Task.objects.filter(pk=task.pk).annotate(waiting=expression).values_list("waiting", flat=True)
        with CaptureQueriesContext(connection) as queries:
            assert list(rows) == [[{"id": c.person("responder").pk, "name": c.person("responder").username}]]
        assert len(queries) == 1
    with actor_context(c.person("responder")):
        as_actor(task, c.person("responder")).message_post("Answered")
    with actor_context(manager):
        assert as_actor(task, manager).clarification_waiting() == []


def test_active_project_round_uses_live_targets_and_tracks(campaign: ProposalCampaign) -> None:
    c = campaign
    round = c.round()
    proposal = c.admit(round, "responder", track=True)
    manager = c.person("facilitator")
    with actor_context(manager), system_context(reason="tests.round.targets"):
        assert Round.objects.active_for_project(round.project).get().pk == round.pk
        assert Round.objects.active_for_project(proposal.track).get().pk == round.pk
        Round._base_manager.filter(pk=round.pk).update(status="cancelled")
        assert not Round.objects.active_for_project(round.project).exists()


def test_task_audience_projection_shares_publication_constraints(campaign: ProposalCampaign) -> None:
    c = campaign
    round = c.round()
    c.admit(round, "responder")
    question = c.ask(round)
    manager = c.person("facilitator")

    def choices(actor: Any) -> list[str]:
        row = Task._base_manager.filter(pk=question.pk).annotate(**{
            f"allowed_{value}": Task.visibility_allowed_expression(actor, value)
            for value in Task.TaskVisibility.values
        }).get()
        return [value for value in Task.TaskVisibility.values if getattr(row, f"allowed_{value}")]

    with actor_context(manager):
        assert set(choices(manager)) == {"inherited", "restricted"}
        assert choices(c.person("outsider")) == []
        as_actor(round, manager).pass_clarification(question, c.person("recipient"))
        assert choices(manager) == ["inherited"]


def test_answer_audience_choices_match_the_locked_verb(campaign: ProposalCampaign) -> None:
    c = campaign
    round = c.round()
    proposal = c.admit(round, "responder")
    answer = c.answer(proposal)
    responder, manager = c.person("responder"), c.person("facilitator")
    with actor_context(responder):
        row = as_actor(answer, responder)
        row.set_visibility("responder")
        assert row.allowed_visibility() == ["responder", "sealed"]
    with actor_context(manager):
        assert set(as_actor(answer, manager).allowed_visibility()) == {"round", "responder", "sealed"}


def test_task_audience_list_projection_has_constant_query_count(campaign: ProposalCampaign) -> None:
    c = campaign
    round = c.round()
    manager = c.person("project-owner")
    with system_context(reason="tests.task.audiences"):
        for index in range(50):
            Task.objects.create(project=round.project, title=f"Item {index}")
    def read(limit: int) -> list[Any]:
        return list(Task.objects.as_user(manager).annotate(**{
            f"allowed_{value}": Task.visibility_allowed_expression(manager, value)
            for value in Task.TaskVisibility.values
        }).order_by("pk")[:limit])
    with actor_context(manager):
        read(5)
        counts = []
        for limit in (5, 50):
            with CaptureQueriesContext(connection) as queries:
                rows = read(limit)
            assert len(rows) == limit
            counts.append(len(queries))
        assert counts[0] == counts[1], counts
