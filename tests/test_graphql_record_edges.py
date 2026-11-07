"""Related-edge counts read each declared edge through its owner, in one statement."""

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import actor_context

from angee.decisions import schema as decision_schema
from angee.decisions.testing.drivers import seed_decision
from angee.graphql import records
from angee.graphql.edges import record_edges
from tests.conftest import Page, addon_schema, create_user, execute_schema, result_data, vault_for

pytestmark = pytest.mark.django_db(transaction=True, reset_sequences=True)

QUERY = """query($model: String!, $id: ID!, $edges: [String!]!) {
  record_edge_counts(model: $model, id: $id, edges: $edges) { edge count }
}"""


@pytest.fixture(autouse=True)
def declared_edges(settings):
    settings.ANGEE_GRAPHQL_RECORD_EDGES = ["angee.decisions.schema.record_edges"]


@pytest.fixture
def schema():
    return addon_schema(decision_schema.schemas, "console", records.schemas)


@pytest.fixture
def referenced(composed_tables):
    """One owner-readable decision on each of two records with colliding numeric IDs."""

    owner, outsider = create_user("edge-owner"), create_user("edge-outsider")
    vault = vault_for(owner)
    with actor_context(owner):
        page = Page.objects.create_in(vault, title="Edges")
    decisions = [
        seed_decision(actor=owner, assignees=(owner,), reference=record, requester=None) for record in (vault, page)
    ]
    return owner, outsider, (vault, page), decisions


def counts(schema, user, record, *edges):
    variables = {"model": record._meta.label, "id": record.sqid, "edges": list(edges)}
    return result_data(execute_schema(schema, QUERY, variables, user=user))["record_edge_counts"]


def test_each_edge_counts_its_readable_rows_on_the_addressed_record(schema, referenced):
    owner, _outsider, (vault, page), decisions = referenced
    assert counts(schema, owner, vault, "decisions.decisions") == [{"edge": "decisions.decisions", "count": 1}]
    assert counts(schema, owner, page, "decisions.decisions") == [{"edge": "decisions.decisions", "count": 1}]
    assert counts(schema, owner, decisions[0], "decisions.concerns", "decisions.decisions") == [
        {"edge": "decisions.concerns", "count": 1},
        {"edge": "decisions.decisions", "count": 0},
    ]


def test_an_edge_for_another_model_counts_zero(schema, referenced):
    owner, _outsider, (vault, _page), _decisions = referenced
    assert counts(schema, owner, vault, "decisions.concerns") == [{"edge": "decisions.concerns", "count": 0}]


def test_an_unreadable_record_has_no_counts(schema, referenced):
    _owner, outsider, (vault, _page), decisions = referenced
    assert counts(schema, outsider, vault, "decisions.decisions") == []
    assert counts(schema, outsider, decisions[0], "decisions.concerns") == []


def test_every_applicable_edge_shares_one_count_statement(schema, referenced):
    owner, _outsider, _records, decisions = referenced
    with CaptureQueriesContext(connection) as one:
        counts(schema, owner, decisions[0], "decisions.concerns")
    with CaptureQueriesContext(connection) as two:
        counts(schema, owner, decisions[0], "decisions.concerns", "decisions.decisions")
    assert len(two) == len(one)


def test_an_unknown_edge_is_a_request_error(schema, referenced):
    owner, _outsider, (vault, _page), _decisions = referenced
    variables = {"model": vault._meta.label, "id": vault.sqid, "edges": ["decisions.unknown"]}
    result = execute_schema(schema, QUERY, variables, user=owner)
    assert result.errors and result.errors[0].extensions["code"] == "VALIDATION"
    assert "decisions.unknown" in result.errors[0].extensions["validationErrors"]["edges"][0]


def test_two_providers_claiming_one_edge_fail_fast(settings):
    settings.ANGEE_GRAPHQL_RECORD_EDGES = ["angee.decisions.schema.record_edges"] * 2
    with pytest.raises(ImproperlyConfigured, match="decisions.decisions"):
        record_edges()
