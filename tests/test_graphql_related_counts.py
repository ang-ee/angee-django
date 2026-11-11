"""Related counts read each declared group through its owner, in one statement."""

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, system_context

import angee.decisions.testing.models  # noqa: F401 -- register related models before native database setup
import angee.integrate.testing.models  # noqa: F401 -- register related models before native database setup
import angee.messaging.testing.models  # noqa: F401 -- register related models before native database setup
import angee.nexus.testing.models  # noqa: F401 -- register related models before native database setup
import angee.workflows.testing.models  # noqa: F401 -- register related models before native database setup
from angee.decisions import schema as decision_schema
from angee.decisions.testing.drivers import seed_decision
from angee.graphql import records
from angee.graphql.related_counts import RelatedCount, check_related_counts, count_related, declared_related_counts
from angee.graphql.testing.documents import execute_schema, result_data
from tests.conftest import Backend, Drive, File, Page, addon_schema, create_user, vault_for

pytestmark = pytest.mark.django_db

QUERY = """query($model: String!, $id: ID!, $groups: [String!]!) {
  related_counts(model: $model, id: $id, groups: $groups) { group count }
}"""

DECISION_COUNTS = ["angee.decisions.schema.related_counts"]


def unreadable_rows(record):
    return [record]


def misdeclared_target():
    return (RelatedCount("tests.misdeclared", lambda record: record, target="missing.Model"),)


def plain_rows():
    return (RelatedCount("tests.plain", unreadable_rows),)


@pytest.fixture
def decision_counts(settings):
    settings.ANGEE_GRAPHQL_RELATED_COUNTS = DECISION_COUNTS


@pytest.fixture
def schema():
    return addon_schema(decision_schema.schemas, "console", records.schemas)


@pytest.fixture
def referenced(db, decision_counts):
    """One owner-readable decision on each of two records with colliding numeric IDs."""

    owner, outsider = create_user("count-owner"), create_user("count-outsider")
    vault = vault_for(owner)
    with actor_context(owner):
        page = Page.objects.create_in(vault, pk=vault.pk, title="Counts")
    decisions = [
        seed_decision(actor=owner, assignees=(owner,), reference=record, requester=None) for record in (vault, page)
    ]
    return owner, outsider, (vault, page), decisions


def counts(schema, user, record, *groups):
    variables = {"model": record._meta.label, "id": record.sqid, "groups": list(groups)}
    return result_data(execute_schema(schema, QUERY, variables, user=user))["related_counts"]


def test_each_group_counts_its_readable_rows_on_the_addressed_record(schema, referenced):
    owner, _outsider, (vault, page), decisions = referenced
    assert counts(schema, owner, vault, "decisions.decisions") == [{"group": "decisions.decisions", "count": 1}]
    assert counts(schema, owner, page, "decisions.decisions") == [{"group": "decisions.decisions", "count": 1}]
    assert counts(schema, owner, decisions[0], "decisions.concerns", "decisions.decisions") == [
        {"group": "decisions.concerns", "count": 1},
        {"group": "decisions.decisions", "count": 0},
    ]


def test_a_group_for_another_model_counts_zero(schema, referenced):
    owner, _outsider, (vault, _page), _decisions = referenced
    assert counts(schema, owner, vault, "decisions.concerns") == [{"group": "decisions.concerns", "count": 0}]


def test_an_unreadable_record_has_no_counts(schema, referenced):
    _owner, outsider, (vault, _page), decisions = referenced
    assert counts(schema, outsider, vault, "decisions.decisions") == []
    assert counts(schema, outsider, decisions[0], "decisions.concerns") == []


def count_and_reads(captured):
    """A read's statements that count, and the others."""
    statements = [query["sql"] for query in captured.captured_queries]
    return [sql for sql in statements if "COUNT(" in sql], [sql for sql in statements if "COUNT(" not in sql]


@pytest.mark.django_db(transaction=True)
def test_every_applicable_group_shares_one_count_statement(schema, referenced):
    owner, _outsider, _records, decisions = referenced
    # Measured warm, as document budgets are: Django caches each content type on first use.
    counts(schema, owner, decisions[0], "decisions.concerns", "decisions.decisions")
    with CaptureQueriesContext(connection) as one:
        counts(schema, owner, decisions[0], "decisions.concerns")
    with CaptureQueriesContext(connection) as two:
        counts(schema, owner, decisions[0], "decisions.concerns", "decisions.decisions")
    (one_count,), one_reads = count_and_reads(one)
    (two_count,), two_reads = count_and_reads(two)
    assert (one_count.count("COUNT("), two_count.count("COUNT(")) == (1, 2)
    # The second group reads nothing the first did not, and nothing twice.
    # 0.31 (<= 1 -> 0 repeated reads): no decided rows remain for a scope to revalidate.
    assert set(two_reads) == set(one_reads)
    assert len(two_reads) == len(one_reads)


def test_an_unknown_group_is_a_request_error(schema, referenced):
    owner, _outsider, (vault, _page), _decisions = referenced
    variables = {"model": vault._meta.label, "id": vault.sqid, "groups": ["decisions.unknown"]}
    result = execute_schema(schema, QUERY, variables, user=owner)
    assert result.errors and result.errors[0].extensions["code"] == "VALIDATION"
    assert "decisions.unknown" in result.errors[0].extensions["validationErrors"]["groups"][0]


def test_rows_that_are_not_an_angee_queryset_are_a_configuration_error(settings, referenced):
    owner, _outsider, (vault, _page), _decisions = referenced
    settings.ANGEE_GRAPHQL_RELATED_COUNTS = ["tests.test_graphql_related_counts.plain_rows"]
    with actor_context(owner), pytest.raises(ImproperlyConfigured, match="tests.plain"):
        count_related(vault, list(declared_related_counts().values()))


def test_two_providers_claiming_one_group_fail_manage_py_check(settings):
    settings.ANGEE_GRAPHQL_RELATED_COUNTS = DECISION_COUNTS * 2
    with pytest.raises(ImproperlyConfigured, match="decisions.decisions"):
        declared_related_counts()
    [error] = check_related_counts()
    assert error.id == "angee.graphql.E004"
    assert "decisions.decisions" in error.msg


def test_an_unknown_target_model_fails_manage_py_check(settings):
    settings.ANGEE_GRAPHQL_RELATED_COUNTS = ["tests.test_graphql_related_counts.misdeclared_target"]
    [error] = check_related_counts()
    assert error.id == "angee.graphql.E004"
    assert "missing.Model" in error.msg


def test_every_declared_count_agrees_with_the_rows_its_group_lists():
    """Each registered declaration counts exactly the rows its read lists, bounded like its group.

    The count statement reads under the aggregate scope, the rows under the row
    scope; they must agree on every record a declaration applies to. A
    declaration whose target this fixture does not seed is named here, so a new
    declaration fails until it is exercised.
    """

    owner = create_user("agreement-owner")
    vault = vault_for(owner)
    with actor_context(owner):
        page = Page.objects.create_in(vault, title="Agreement")
    decision = seed_decision(actor=owner, assignees=(owner,), reference=page, requester=None)
    with system_context(reason="tests.related_counts.storage"):
        backend = Backend.objects.create(slug="counts", label="Files", backend_class="local")
        drive = Drive.objects.create(backend=backend, slug="counts", name="Files", owner=owner)
        file = File.objects.create(
            drive=drive, filename="agreement.txt", content_hash="a" * 64, storage_path="agreement.txt",
            owner=owner, upload_state="ready",
        )
    seeded = (vault, page, decision, file)
    declared = declared_related_counts()
    exercised = set()
    with actor_context(owner):
        for declaration in declared.values():
            for record in seeded:
                if not declaration.applies_to(record):
                    continue
                exercised.add(declaration.id)
                listed = declaration.rows(record).count()
                expected = listed if declaration.limit is None else min(listed, declaration.limit)
                assert count_related(record, [declaration]) == {declaration.id: expected}, (declaration.id, record)
    assert set(declared) - exercised == {"workflows.decisions"}
