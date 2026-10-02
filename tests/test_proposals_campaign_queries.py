"""Native list projections keep bounded query counts at five and fifty rows."""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, system_context, to_subject_ref
from rebac.backends import backend
from rebac.backends.local import LocalBackend

from angee.graphql.capabilities import held_permissions, permission_annotations
from angee.projects.testing.models import Task
from tests.proposals_campaign import ProposalCampaign, as_actor
from tests.proposals_models import Proposal, Round
from tests.queries import is_rebac_revision_read

pytest_plugins = ("tests.proposals_campaign",)

# django-zed-rebac owns these fixed scope-compilation reads. Application row
# reads and roster expansion have separate exact guards below.
# Projection guards run only on a fetch, sharing one schema operation across
# selected columns; reading a filled result cache adds no guard statements.
REBAC_TRACK_STATUS_QUERY_CEILING = 149
REBAC_ROUND_QUERY_CEILING = 43


def _list_statement_categories(queries, model, *, roster=False, projection=None, audits=None) -> Counter[str]:
    """Separate native list projections from the permission compiler's reads."""

    categories: Counter[str] = Counter()
    quote = connection.ops.quote_name
    row_prefix = f"SELECT {quote(model._meta.db_table)}.{quote(model._meta.pk.column)},"
    roster_prefix = f"SELECT {quote(Proposal._meta.db_table)}.{quote(Proposal._meta.pk.column)},"
    for query in queries:
        statement = query["sql"]
        # Django records streamed PostgreSQL reads as DECLARE ... FOR SELECT.
        # Classify their SELECT payload while counting each captured statement.
        sql = statement.partition(" FOR ")[2] if statement.startswith("DECLARE ") else statement
        if sql.startswith(row_prefix) or (
            projection and f" AS {quote(projection)} FROM {quote(model._meta.db_table)}" in sql
        ):
            category = "application"
        elif roster and sql.startswith(roster_prefix):
            category = "roster"
        elif sql.startswith('INSERT INTO "rebac_permissionauditevent"'):
            category = "bypass_audit"
        elif is_rebac_revision_read(statement):
            category = "schema_revision"
        elif sql.startswith('SELECT 1 AS "a" FROM "rebac_schemageneration"'):
            category = "constant_decision"
        elif sql.startswith("SELECT DISTINCT ") and '"rebac_' in sql.partition(" WHERE ")[0]:
            category = "actor_sets"
        elif sql.startswith("SELECT ") and sql.partition(" FROM ")[0].endswith(' AS "pk"'):
            category = "decided_rows"
        else:
            pytest.fail(f"Unexpected statement in list projection: {statement}")
        categories[category] += 1
    assert categories["application"] == 1, categories
    assert categories["roster"] == int(roster), categories
    assert categories["bypass_audit"] == (int(roster) if audits is None else audits), categories
    return categories


def test_proposal_track_status_uses_one_application_select_and_constant_policy_reads(
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
        categories = []
        for limit in (5, 50):
            with CaptureQueriesContext(connection) as queries:
                rows = read(limit)
            assert len(rows) == limit
            assert all(row._track_status is None for row in rows)
            categories.append(_list_statement_categories(queries, Proposal))
        assert categories[0] == categories[1], categories
        assert categories[0].total() - 1 <= REBAC_TRACK_STATUS_QUERY_CEILING, categories


@pytest.mark.parametrize("roster", (False, True), ids=("capabilities", "roster"))
def test_round_lists_use_one_application_select_and_constant_policy_and_roster_reads(
    campaign: ProposalCampaign,
    roster: bool,
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
        categories = []
        for limit in (5, 50):
            with CaptureQueriesContext(connection) as queries:
                rows = read(limit, actor, roster)
            assert len(rows) == limit
            categories.append(_list_statement_categories(queries, Round, roster=roster))
        assert categories[0] == categories[1], categories
        assert categories[0].total() - 1 - 2 * roster <= REBAC_ROUND_QUERY_CEILING, categories


def test_round_and_proposal_read_scopes_compile_to_sql_for_non_admins(campaign: ProposalCampaign) -> None:
    actor = campaign.person("outsider")
    local = backend()
    assert isinstance(local, LocalBackend)
    for model in (Round, Proposal):
        query = model.objects.with_actor(actor).scoped()
        sql, _params = query.query.sql_with_params()
        assert "SELECT" in sql.upper()


def test_waiting_list_has_constant_query_count_and_is_private_to_each_recipient(campaign: ProposalCampaign) -> None:
    c = campaign
    # Django probes SQLite JSON support on first use; that connection setup is
    # outside the per-page projection budget.
    assert connection.features.supports_json_field
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
            # The compiled scope prepares bounded actor/arrow facts at SQL
            # execution, then one row projection.
            expression = Task.clarification_waiting_expression(to_subject_ref(actor))
            categories = []
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
                categories.append(_list_statement_categories(
                    queries, Task, projection="_clarification_waiting", audits=2,
                ))
                assert len(waiting) == limit
                assert {entry["id"] for entry in waiting[0]} == expected
                all_waiting = {c.person("responder").pk, c.person("peer").pk, c.person("third").pk}
                assert all(
                    {entry["id"] for entry in row} == (all_waiting if actor == manager else {actor.pk})
                    for row in waiting[1:]
                )
            assert categories[0] == categories[1], categories
            assert categories[0].total() <= 19, categories
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
        # SQL execution prepares the actor/arrow facts and emits the two native
        # bypass audits; the recipient payload still uses one application SELECT.
        assert _list_statement_categories(queries, Task, projection="waiting", audits=2) == Counter(
            application=1,
            bypass_audit=2,
            schema_revision=1,
            actor_sets=1,
            decided_rows=1,
            constant_decision=1,
        )
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
        Round._base_manager.filter(pk=round.pk).update(status="canceled")
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
        assert counts == [44, 44], counts
