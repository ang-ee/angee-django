"""Checklists: declared items, recorded results, the read rule and the one status rule.

``tests.checklistdemo`` declares a checklist type (an inspection whose name and
notes the checklist reads) and an evidence type (a report), and their relations
on the checklists definitions, as a consumer addon does. Results are recorded
under the actor that holds the inspection's write and reads the report.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
import strawberry
import strawberry_django
from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, models
from django.db.models import Value
from django.db.models.lookups import Exact
from django.test.utils import CaptureQueriesContext, isolate_apps
from rebac import RelationshipTuple, actor_context, system_context, to_object_ref, to_subject_ref, write_relationships
from rebac.models import SchemaOverride, SchemaPermission
from strawberry import auto

from angee.base.permissions import bind_rebac_schema
from angee.checklists.contracts import Checklist, ChecklistCategory, ChecklistItem, ChecklistStatus
from angee.checklists.managers import (
    ChecklistStatuses,
    checklist_category_status,
    checklist_next_blocking,
    checklist_status,
    read_decomposes,
)
from angee.checklists.models import ChecklistMixin
from angee.checklists.schema import ChecklistNode, ChecklistResultChangeGate, resource_filters
from angee.checklists.testing.models import ChecklistEvidence, ChecklistResult
from angee.checklists.timeline import ChecklistResultKind
from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.decisions.testing.models import Decision
from angee.graphql.node import AngeeNode
from angee.graphql.testing.documents import execute_schema, measure_graphql_document, result_data
from angee.graphql.testing.publishing import capture_broadcasts
from tests.checklistdemo.models import CraneInspection, Inspection, Notice, Report
from tests.conftest import addon_schema, create_platform_admin, create_user
from tests.mtidemo.models import MtiParent

CATEGORIES = ("identity", "content", "archive")
LABELS = {"identity": "Identity", "content": "Content", "archive": "Archive"}


def _grant(record: Any, relation: str, *users: Any) -> None:
    write_relationships([RelationshipTuple(to_object_ref(record), relation, to_subject_ref(user)) for user in users])


@pytest.fixture
def world(db: None) -> Any:
    """A writer who records with the report, a reader without it, a reader with it, and an outsider."""

    del db
    writer, reader, auditor, outsider = (create_user(name) for name in ("writer", "reader", "auditor", "outsider"))
    with system_context(reason="tests.checklists.world"):
        inspection = Inspection.objects.create(name="Harbour crane", notes="Annual survey")
        report = Report.objects.create(name="Load test")
        hidden = Report.objects.create(name="Private survey")
    _grant(inspection, "writer", writer)
    _grant(inspection, "reader", reader, auditor)
    _grant(report, "reader", writer, auditor)
    _grant(hidden, "reader", writer)

    class World:
        pass

    world = World()
    world.__dict__.update(
        writer=writer,
        reader=reader,
        auditor=auditor,
        outsider=outsider,
        inspection=inspection,
        report=report,
        hidden=hidden,
    )
    return world


def _record(world: Any, key: str, status: str, detail: str = "", *, evidence: tuple[Any, ...] = (), actor=None):
    with actor_context(actor or world.writer):
        return world.inspection.checklist_record(key, status, detail or f"{key} {status}", evidence=evidence)


def _assess(world: Any, actor: Any) -> Any:
    with actor_context(actor):
        return world.inspection.checklist_assessment()


def _filters(world: Any, actor: Any) -> dict[str, Any]:
    rows = Inspection.objects.with_actor(actor).filter(pk=world.inspection.pk)
    expressions = {"status": checklist_status(rows)}
    expressions.update({key: checklist_category_status(rows, category=key) for key in CATEGORIES})
    return rows.annotate(**expressions).values(*expressions).get()


def _assert_parity(world: Any, actor: Any) -> Any:
    """The record projection, the filters and the counts read the same statuses."""

    assessed = _assess(world, actor)
    rows = Inspection.objects.with_actor(actor).filter(pk=world.inspection.pk)
    if not rows.exists():
        assert ChecklistResult.objects.counts(rows)[0] == []
        return assessed
    filters = _filters(world, actor)
    assert filters == {"status": assessed.status, **{group.key: group.status for group in assessed.groups}}
    summary, attention = ChecklistResult.objects.counts(rows)
    assert summary == [(assessed.status, 1)]
    assert attention == [
        (key, LABELS[key], status, int(filters[key] == status))
        for key in CATEGORIES
        for status in ("conflict", "risk", "unresolved")
    ]
    return assessed


def _statuses(assessed: Any) -> dict[str, str]:
    return {item.key: item.status for item in assessed.items}


# Declarations and their checks.


def test_a_checklist_keeps_declared_order_through_extension() -> None:
    base = Checklist(categories=(ChecklistCategory("a", "A"),), items=(ChecklistItem("one", "One", category="a"),))
    extended = base.extend(
        categories=(ChecklistCategory("b", "B"),),
        items=(ChecklistItem("two", "Two", category="b", blocking=True),),
    )
    assert extended.keys == ("one", "two")
    assert [category.key for category in extended.categories] == ["a", "b"]
    assert extended.item("two") == ChecklistItem("two", "Two", category="b", blocking=True)
    assert extended.item("retired") is None
    assert extended.category_items("b") == (extended.item("two"),)
    assert extended.problems() == []


def test_declaration_problems_name_duplicates_unknown_and_reserved_categories() -> None:
    checklist = Checklist(
        categories=(ChecklistCategory("a", "A"), ChecklistCategory("a", "Again"), ChecklistCategory("status", "S")),
        items=(
            ChecklistItem("one", "One", category="a"),
            ChecklistItem("one", "Twice"),
            ChecklistItem("two", "Two", category="missing"),
        ),
    )
    assert checklist.problems() == [
        "category 'a' is declared more than once",
        "category 'status' is reserved",
        "item 'one' is declared more than once",
        "item 'two' names the undeclared category 'missing'",
    ]


def _checklist_errors(errors: list[Any], *models_: Any) -> list[Any]:
    return [error for error in errors if error.id.startswith("checklists.") and error.obj in models_]


def test_the_check_accepts_the_declared_checklist_type(db: None) -> None:
    del db
    assert set(ChecklistResult.declared_target_models().values()) == {Inspection, Notice}
    assert [error for error in ChecklistResult.check() if error.id.startswith("checklists.")] == []


def test_the_check_reports_a_checklist_type_without_a_relation() -> None:
    bind_rebac_schema(apps.get_app_config("checklists"), None)
    errors = [error for error in ChecklistResult.check() if error.id.startswith("checklists.")]
    assert sorted((error.id, error.obj.__name__) for error in errors) == [
        ("checklists.E002", "Inspection"),
        ("checklists.E002", "Notice"),
    ]


def test_the_check_reports_placement_bad_declarations_and_a_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    with isolate_apps():

        class MisplacedChild(ChecklistMixin, MtiParent):
            """An MTI child whose results its untyped-for-checklists parent would key."""

            class Meta:
                app_label = "mtidemo"

        class Tokenless(ChecklistMixin, models.Model):
            """A checklist without a content token and with a duplicated item."""

            class Meta:
                app_label = "mtidemo"

            @classmethod
            def checklist(cls) -> Checklist:
                return Checklist(items=(ChecklistItem("one", "One"), ChecklistItem("one", "Again")))

    strays = (MisplacedChild, Tokenless)
    installed = apps.get_models
    monkeypatch.setattr(apps, "get_models", lambda *args, **kwargs: [*installed(*args, **kwargs), *strays])
    errors = _checklist_errors(ChecklistResult.check(), *strays)
    assert sorted((error.id, error.obj.__name__) for error in errors) == [
        ("checklists.E001", "MisplacedChild"),
        ("checklists.E001", "Tokenless"),
        ("checklists.E004", "Tokenless"),
        ("checklists.E005", "Tokenless"),
    ]


# Recording.


def test_recording_stores_the_content_token_and_one_link_per_evidence_record(world: Any) -> None:
    result = _record(world, "owner", "passed", "Owner confirmed.", evidence=(world.report, world.report))
    token = (
        Inspection._base_manager.filter(pk=world.inspection.pk)
        .annotate(
            token=Inspection.checklist_revision_expression(),
        )
        .values_list("token", flat=True)
        .get()
    )
    assert (result.key, result.status, result.detail, result.revision) == ("owner", "passed", "Owner confirmed.", token)
    assert result.created_by_id == world.writer.pk
    links = list(ChecklistEvidence._base_manager.filter(result=result))
    assert [(link.content_type.model_class(), link.object_id) for link in links] == [(Report, world.report.pk)]
    with pytest.raises(ValidationError):
        result.delete()


@pytest.mark.parametrize(
    ("key", "status", "field"),
    [
        pytest.param("retired", "passed", "key", id="undeclared-key"),
        pytest.param("owner", "maybe", "status", id="unknown-status"),
    ],
)
def test_recording_refuses_an_undeclared_key_or_status(world: Any, key: str, status: str, field: str) -> None:
    with pytest.raises(ValidationError) as raised:
        _record(world, key, status)
    assert field in raised.value.message_dict
    assert not ChecklistResult._base_manager.exists()


def test_recording_requires_the_records_write_and_readable_declared_evidence(world: Any) -> None:
    with pytest.raises(PermissionDenied):
        _record(world, "owner", "passed", actor=world.reader)
    with pytest.raises(PermissionDenied):
        _record(world, "owner", "passed", evidence=(world.report,), actor=world.outsider)
    other = create_user("other-writer")
    _grant(world.inspection, "writer", other)
    with pytest.raises(PermissionDenied):
        _record(world, "owner", "passed", evidence=(world.report,), actor=other)
    with pytest.raises(ValidationError):
        _record(world, "owner", "passed", evidence=(world.inspection,))
    with pytest.raises(PermissionDenied):
        world.inspection.checklist_record("owner", "passed")
    assert not ChecklistResult._base_manager.exists()


# The read rule.


def test_a_result_shows_only_when_its_record_and_every_evidence_link_are_readable(world: Any) -> None:
    _record(world, "owner", "passed", evidence=(world.report,))
    _record(world, "signature", "risk", "Signature smudged.", evidence=(world.report, world.hidden))
    _record(world, "totals", "conflict", "Totals differ.")
    by_actor = {
        actor.username: _statuses(_assert_parity(world, actor))
        for actor in (world.writer, world.auditor, world.reader, world.outsider)
    }
    assert by_actor == {
        "writer": {"owner": "passed", "signature": "risk", "totals": "conflict", "marker": "not_checked"},
        "auditor": {"owner": "passed", "signature": "not_checked", "totals": "conflict", "marker": "not_checked"},
        "reader": {"owner": "not_checked", "signature": "not_checked", "totals": "conflict", "marker": "not_checked"},
        "outsider": {
            "owner": "not_checked",
            "signature": "not_checked",
            "totals": "not_checked",
            "marker": "not_checked",
        },
    }
    owner = next(item for item in _assess(world, world.auditor).items if item.key == "owner")
    assert [(ref.model, ref.id) for ref in owner.evidence] == [
        ("checklistdemo.Report", world.report.sqid),
    ]


def test_an_unreadable_latest_result_never_falls_back_to_an_older_readable_one(world: Any) -> None:
    _record(world, "owner", "passed", evidence=(world.report,))
    assert _statuses(_assess(world, world.auditor))["owner"] == "passed"
    _record(world, "owner", "passed", "Rechecked privately.", evidence=(world.hidden,))
    assessed = _assert_parity(world, world.auditor)
    owner = next(item for item in assessed.items if item.key == "owner")
    assert (owner.status, owner.result, owner.detail) == ("not_checked", None, "No check result is available.")


# The status rule.


def test_the_latest_result_per_item_wins(world: Any) -> None:
    _record(world, "owner", "conflict", "Earlier finding.")
    for key in ("owner", "signature", "totals", "marker"):
        _record(world, key, "passed")
    assessed = _assert_parity(world, world.writer)
    assert assessed.status == "passed" and assessed.complete and not assessed.stale
    assert assessed.detail == "4 applicable checks passed."


def test_partial_results_stay_unresolved_and_unrun_categories_not_checked(world: Any) -> None:
    assessed = _assert_parity(world, world.writer)
    assert (assessed.status, assessed.complete) == ("not_checked", False)
    assert assessed.detail == "One or more check results are unavailable."
    assert {group.key: group.status for group in assessed.groups} == {
        "identity": "not_checked",
        "content": "not_checked",
        "archive": "not_applicable",
    }
    _record(world, "owner", "unresolved", "The owner needs a reviewer.")
    assessed = _assert_parity(world, world.writer)
    assert assessed.status == "unresolved" and assessed.detail == "The owner needs a reviewer."
    assert {group.key: group.status for group in assessed.groups}["identity"] == "unresolved"
    _record(world, "owner", "passed")
    assessed = _assert_parity(world, world.writer)
    assert assessed.status == "unresolved" and not assessed.complete
    assert assessed.detail == "The complete current checklist has not been recorded."
    assert {group.key: group.status for group in assessed.groups} == {
        "identity": "not_checked",
        "content": "not_checked",
        "archive": "not_applicable",
    }


def test_a_content_change_makes_passes_unresolved_and_keeps_risks(world: Any) -> None:
    _record(world, "owner", "passed", "Owner confirmed.")
    _record(world, "signature", "not_applicable")
    _record(world, "totals", "risk", "Rounding differs.")
    _record(world, "marker", "passed")
    with system_context(reason="tests.checklists.lifecycle"):
        Inspection.objects.filter(pk=world.inspection.pk).update(state="closed")
    assessed = _assert_parity(world, world.writer)
    assert not assessed.stale and assessed.status == "risk"
    with system_context(reason="tests.checklists.content"):
        Inspection.objects.filter(pk=world.inspection.pk).update(notes="Revised survey")
    assessed = _assert_parity(world, world.writer)
    assert assessed.stale and assessed.status == "risk"
    items = {item.key: item for item in assessed.items}
    assert {key: item.status for key, item in items.items()} == {
        "owner": "unresolved",
        "signature": "unresolved",
        "totals": "risk",
        "marker": "unresolved",
    }
    assert items["owner"].detail == "The record changed after this check. Earlier result: Owner confirmed."
    assert items["totals"].stale and items["totals"].detail == "Rounding differs."
    _record(world, "totals", "passed")
    assert _assert_parity(world, world.writer).status == "unresolved"


def test_categories_take_the_strongest_status_and_all_not_applicable_reads_not_applicable(world: Any) -> None:
    _record(world, "owner", "risk")
    _record(world, "signature", "conflict", "Signature forged.")
    _record(world, "totals", "not_applicable")
    _record(world, "marker", "not_applicable")
    assessed = _assert_parity(world, world.writer)
    groups = {group.key: group for group in assessed.groups}
    assert (groups["identity"].status, groups["identity"].detail) == ("conflict", "Signature forged.")
    assert groups["content"].status == "not_applicable" and groups["content"].detail == "No applicable checks."
    assert groups["identity"].item_keys == ("owner", "signature")
    assert assessed.status == "conflict"


@pytest.mark.parametrize(
    ("status", "summary"),
    [
        pytest.param("risk", "risk", id="risk"),
        pytest.param("conflict", "conflict", id="conflict"),
        pytest.param("unresolved", "unresolved", id="unresolved"),
        pytest.param("passed", "passed", id="retired-pass-ignored"),
        pytest.param("not_applicable", "passed", id="retired-not-applicable-ignored"),
    ],
)
def test_a_retired_key_counts_only_while_it_needs_attention(world: Any, status: str, summary: str) -> None:
    for key in ("owner", "signature", "totals", "marker"):
        _record(world, key, "passed")
    with system_context(reason="tests.checklists.retired"):
        ChecklistResult.objects.create(
            key="retired",
            status=status,
            detail="Historical finding.",
            revision="",
            content_type=ContentType.objects.get_for_model(Inspection),
            object_id=world.inspection.pk,
        )
    assessed = _assert_parity(world, world.writer)
    assert assessed.status == summary
    listed = [item.key for item in assessed.items]
    assert ("retired" in listed) == (status in ("risk", "conflict", "unresolved"))
    assert {group.key: group.status for group in assessed.groups} == {
        "identity": "passed",
        "content": "passed",
        "archive": "not_applicable",
    }


def test_counts_group_every_record_in_one_actor_scoped_statement(world: Any) -> None:
    with system_context(reason="tests.checklists.counts"):
        second = Inspection.objects.create(name="Gantry", notes="")
    _grant(second, "reader", world.writer)
    _record(world, "owner", "conflict")
    rows = Inspection.objects.with_actor(world.writer)
    ChecklistResult.objects.counts(rows)
    with CaptureQueriesContext(connection) as captured:
        summary, attention = ChecklistResult.objects.counts(rows)
    assert summary == [("conflict", 1), ("not_checked", 1)]
    assert ("identity", "Identity", "conflict", 1) in attention and len(attention) == len(CATEGORIES) * 3
    # REBAC 0.34 pins one policy snapshot for the statement's fetch: its one revision guard
    # serves the rows, the read proof and every scope the checklist expressions build as they
    # compile. Then the one aggregate statement; further checklist expressions add nothing.
    assert len(captured) <= 2
    assert len([query for query in captured if "COUNT(" in query["sql"].upper()]) == 1
    assert ChecklistResult.objects.counts(rows.none()) == (
        [],
        [(key, LABELS[key], status, 0) for key in CATEGORIES for status in ("conflict", "risk", "unresolved")],
    )


def test_counts_group_under_primary_key_functional_dependence(world: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """PostgreSQL reduces GROUP BY to selected primary keys; the status expression survives that reduction.

    Every backend compiles the reduction here, so it is not left to the PostgreSQL lane.
    """

    monkeypatch.setattr(connection.features, "allows_group_by_selected_pks", True)
    _record(world, "owner", "conflict")
    rows = Inspection.objects.with_actor(world.writer)
    summary, attention = ChecklistResult.objects.counts(rows)
    assert summary == [("conflict", 1)]
    assert ("identity", "Identity", "conflict", 1) in attention
    grouped = rows.values("name").annotate(status=checklist_status(rows), total=models.Count("pk"))
    assert list(grouped.values_list("status", "total")) == [("conflict", 1)]


# Consumers of the rule.


def test_records_still_blocked_are_one_actor_scoped_query(world: Any) -> None:
    with system_context(reason="tests.checklists.blocked"):
        cleared = Inspection.objects.create(name="Gantry", notes="")
        unseen = Inspection.objects.create(name="Unseen", notes="")
    _grant(cleared, "writer", world.writer)
    with actor_context(world.writer):
        cleared.checklist_record("owner", "passed")
        cleared.checklist_record("totals", "not_applicable")
    _record(world, "owner", "passed")
    rows = Inspection.objects.with_actor(world.writer)
    with CaptureQueriesContext(connection) as captured:
        blocked = list(
            rows.annotate(next_blocking=checklist_next_blocking(rows))
            .filter(
                next_blocking__isnull=False,
            )
            .values_list("name", "next_blocking")
        )
    assert blocked == [("Harbour crane", "totals")]
    # REBAC 0.34: the fetch's one revision guard (above) and the one data statement.
    assert len(captured) <= 2
    assert len([query for query in captured if "test_checklistdemo_inspection" in query["sql"]]) == 1
    assert unseen.pk not in rows.values_list("pk", flat=True)


def test_blocking_items_gate_a_decision_through_a_composed_verdict_policy(
    world: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A test-only verdict policy refuses an approval while a blocking item has not passed."""

    def require_blocking_items(self: Any, chosen: tuple[Any, ...], *, actor: Any) -> None:
        if any(alternative.key == "approve" for alternative in chosen):
            for link in self.records.model.system_queryset().filter(decision_id=self.pk):
                record = link.record
                with actor_context(actor):
                    unmet = record.checklist_assessment().blocking_unmet
                if unmet:
                    raise ValidationError(f"Blocking checks have not passed: {', '.join(i.key for i in unmet)}.")

    monkeypatch.setattr(Decision, "validate_verdict", require_blocking_items)
    request = DecisionRequest(
        kind="inspection_approval",
        records=(world.inspection,),
        assignees=(world.writer,),
        requester=None,
        proposal=DecisionProposal(alternatives=[{"key": "approve", "label": "Approve", "outcome": "approved"}]),
    )
    with system_context(reason="tests.checklists.ask"):
        question = Decision.objects.ask(request, actor=None)
    _record(world, "owner", "passed")
    with pytest.raises(ValidationError, match="totals"):
        question.decide(actor=world.writer, chosen=["approve"])
    _record(world, "totals", "passed")
    assert question.decide(actor=world.writer, chosen=["approve"]).verdict == ["approve"]


def test_deleting_a_record_deletes_its_results_and_their_evidence(world: Any) -> None:
    _record(world, "owner", "passed", evidence=(world.report,))
    with system_context(reason="tests.checklists.delete"):
        Inspection.objects.filter(pk=world.inspection.pk).delete()
    assert not ChecklistResult._base_manager.exists()
    assert not ChecklistEvidence._base_manager.exists()
    assert Report.system_queryset().filter(pk=world.report.pk).exists()


def test_resource_filters_name_the_status_each_category_and_blocking() -> None:
    filters = resource_filters(Inspection)
    assert sorted(filters) == [
        "checklist_archive",
        "checklist_content",
        "checklist_identity",
        "checklist_next_blocking",
        "checklist_status",
    ]
    assert resource_filters(Report) == {}


def test_statuses_build_without_reading_rows() -> None:
    def forbid_query(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("A filter provider must read no rows while it is built.")

    with connection.execute_wrapper(forbid_query):
        expression = ChecklistStatuses(Inspection, actor=None, select="status")
    assert isinstance(Exact(expression, Value(ChecklistStatus.PASSED)), Exact)


# The GraphQL projection.


@strawberry_django.type(Inspection)
class InspectionType(ChecklistNode, AngeeNode):
    """A test node composing the checklist projection."""

    name: auto


@strawberry.type
class InspectionQuery:
    """Actor-scoped inspections for the projection tests."""

    @strawberry_django.field
    def inspections(self) -> list[InspectionType]:
        return cast(list[InspectionType], Inspection.objects.order_by("pk"))


_DOCUMENT = """
    query {
      inspections {
        name
        checklist {
          status detail complete stale
          groups { key status detail item_keys }
          items { key status detail stale evidence { model id } }
        }
      }
    }
"""


def _schema() -> Any:
    return addon_schema({"console": {"query": [InspectionQuery], "types": [InspectionType]}}, "console")


def test_the_node_projection_reads_the_rule_with_readable_evidence(world: Any) -> None:
    _record(world, "owner", "passed", "Owner confirmed.", evidence=(world.report,))
    _record(world, "signature", "risk", "Signature smudged.", evidence=(world.hidden,))
    data = result_data(execute_schema(_schema(), _DOCUMENT, user=world.auditor))
    [row] = data["inspections"]
    assessed = _assess(world, world.auditor)
    checklist = row["checklist"]
    assert (checklist["status"], checklist["detail"]) == (assessed.status, assessed.detail)
    assert [group["status"] for group in checklist["groups"]] == [group.status for group in assessed.groups]
    items = {item["key"]: item for item in checklist["items"]}
    assert items["owner"]["evidence"] == [{"model": "checklistdemo.Report", "id": world.report.sqid}]
    assert (items["signature"]["status"], items["signature"]["evidence"]) == ("not_checked", [])


def test_the_node_projection_reads_a_page_in_a_flat_statement_budget(world: Any) -> None:
    def page(size: int) -> int:
        with system_context(reason="tests.checklists.page"):
            for index in range(size - Inspection.objects.count()):
                extra = Inspection.objects.create(name=f"Crane {index}", notes="")
                _grant(extra, "writer", world.writer)
                with actor_context(world.writer):
                    extra.checklist_record("owner", "passed")
        read = measure_graphql_document(_schema(), _DOCUMENT, {}, actor=world.writer)
        assert len(read.data["inspections"]) == size
        return read.statements

    _record(world, "owner", "passed")
    measure_graphql_document(_schema(), _DOCUMENT, {}, actor=world.writer)
    bare = measure_graphql_document(_schema(), "query { inspections { name } }", {}, actor=world.writer)
    # The status cells compile inside the page's own statement and policy pin; the latest
    # results' prefetch is the one statement the checklist adds.
    assert page(1) == page(25) == bare.statements + 1


def test_the_timeline_lists_results_as_their_reader_reads_them(world: Any) -> None:
    _record(world, "owner", "passed", "Owner confirmed.", evidence=(world.report,))
    kind = ChecklistResultKind()
    assert kind.eligible(Inspection) and not kind.eligible(Report)
    assert kind.occurs(world.inspection)
    rows = list(kind.queryset(world.inspection, world.auditor))
    assert [row.key for row in rows] == ["owner"]
    assert list(kind.queryset(world.inspection, world.reader)) == []
    [presentation] = kind.present(rows).values()
    assert (presentation.title, presentation.detail) == ("Owner known: Passed", "Owner confirmed.")
    assert list(kind.records(rows[0])) == [(Inspection, world.inspection.pk)]


def test_a_child_declares_items_on_the_rows_its_canonical_parent_carries(world: Any) -> None:
    with system_context(reason="tests.checklists.child"):
        crane = CraneInspection.objects.create(name="Tower crane", notes="")
    _grant(crane.inspection_ptr, "writer", world.writer)
    with actor_context(world.writer):
        result = crane.checklist_record("load", "risk", "Rated below the load.", evidence=(world.report,))
        assessed = crane.checklist_assessment()
    assert (result.content_type.model_class(), result.object_id) == (Inspection, crane.pk)
    assert [item.key for item in assessed.items][-1] == "load"
    assert assessed.status == "risk"
    rows = CraneInspection.objects.with_actor(world.writer)
    assert rows.annotate(status=checklist_status(rows)).values_list("status", flat=True).get() == "risk"
    rows = CraneInspection.objects.with_actor(world.writer).filter(pk=crane.pk)
    [row] = rows.prefetch_related(
        models.Prefetch(
            "_checklist_latest", queryset=ChecklistResult.objects.with_actor(world.writer), to_attr="latest"
        ),
    )
    assert [(entry.result.key, entry.result.status) for entry in row.latest] == [("load", "risk")]
    with pytest.raises(ValidationError):
        with actor_context(world.writer):
            world.inspection.checklist_record("load", "passed")


def test_recording_a_result_publishes_a_change_its_record_reads_observe(
    world: Any,
    django_capture_on_commit_callbacks: Any,
) -> None:
    with capture_broadcasts() as events, django_capture_on_commit_callbacks(execute=True):
        result = _record(world, "owner", "passed", evidence=(world.report,))
    [event] = [event for event in events if event["model"] == "checklists.ChecklistResult"]
    assert event["id"] == result.sqid and event["action"] == "create"
    assert ("checklistdemo.Inspection", world.inspection.sqid) in {
        (ref["model"], ref["id"]) for ref in event["related_records"]
    }


def test_deleting_an_evidence_record_deletes_every_result_it_supports(world: Any) -> None:
    """A result that loses any evidence link goes: its read depended on all of them."""

    supported = _record(world, "owner", "passed", evidence=(world.report,))
    shared = _record(world, "signature", "risk", "Two sources.", evidence=(world.report, world.hidden))
    kept = _record(world, "totals", "conflict", "Other source.", evidence=(world.hidden,))
    with system_context(reason="tests.checklists.other"):
        other = Inspection.objects.create(name="Gantry", notes="")
    _grant(other, "writer", world.writer)
    with actor_context(world.writer):
        untouched = other.checklist_record("owner", "passed", evidence=(world.hidden,))
    with system_context(reason="tests.checklists.delete_evidence"):
        Report.objects.filter(pk=world.report.pk).delete()
    remaining = set(ChecklistResult._base_manager.values_list("pk", flat=True))
    assert remaining == {kept.pk, untouched.pk}
    assert supported.pk not in remaining and shared.pk not in remaining
    assert set(ChecklistEvidence._base_manager.values_list("result_id", flat=True)) == {kept.pk, untouched.pk}
    assert _statuses(_assess(world, world.writer)) == {
        "owner": "not_checked",
        "signature": "not_checked",
        "totals": "conflict",
        "marker": "not_checked",
    }


def test_an_anonymous_reader_never_sees_a_result_its_evidence_hides(world: Any) -> None:
    with system_context(reason="tests.checklists.notice"):
        notice = Notice.objects.create(text="Opening hours")
    _grant(notice, "writer", world.writer)
    anonymous = AnonymousUser()
    with actor_context(world.writer):
        notice.checklist_record("wording", "passed", "Reviewed.", evidence=(world.report,))
    with actor_context(anonymous):
        assert [item.status for item in notice.checklist_assessment().items] == ["not_checked"]
    with actor_context(world.writer):
        notice.checklist_record("wording", "risk", "Recorded without evidence.")
    with actor_context(anonymous):
        assert [item.status for item in notice.checklist_assessment().items] == ["risk"]
        rows = Notice.objects.with_actor(anonymous)
        assert rows.annotate(status=checklist_status(rows)).values_list("status", flat=True).get() == "risk"


def test_a_null_content_token_reads_every_pass_stale(world: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _record(world, "owner", "passed")
    _record(world, "totals", "risk")
    monkeypatch.setattr(
        Inspection,
        "checklist_revision_expression",
        classmethod(lambda cls: models.Value(None, output_field=models.CharField())),
    )
    statuses = _statuses(_assess(world, world.writer))
    assert (statuses["owner"], statuses["totals"]) == ("unresolved", "risk")
    assert _assess(world, world.writer).stale


def test_a_result_change_reaches_its_records_readers_without_the_result(
    world: Any,
    django_capture_on_commit_callbacks: Any,
) -> None:
    with capture_broadcasts() as events, django_capture_on_commit_callbacks(execute=True):
        _record(world, "owner", "conflict", "Private finding.", evidence=(world.hidden,))
    [payload] = [event for event in events if event["model"] == "checklists.ChecklistResult"]
    inspection = {"model": "checklistdemo.Inspection", "id": world.inspection.sqid}
    for actor, delivered in ((world.auditor, True), (world.writer, True), (world.outsider, False)):
        event = ChecklistResultChangeGate(ChecklistResult, to_subject_ref(actor)).filter(payload)
        if not delivered:
            assert event is None
            continue
        assert event is not None and event.id == "" and event.changed_values is None
        assert [{"model": ref.model, "id": ref.id} for ref in event.related_records] == [inspection]


def test_the_timeline_is_a_chronology_of_each_readable_result(world: Any) -> None:
    """Unlike the status rule, the chronology keeps an older readable result when the latest is hidden."""

    _record(world, "owner", "passed", "Older green.", evidence=(world.report,))
    _record(world, "owner", "conflict", "Private conflict.", evidence=(world.hidden,))
    assert _statuses(_assess(world, world.auditor))["owner"] == "not_checked"
    rows = list(ChecklistResultKind().queryset(world.inspection, world.auditor))
    assert [(row.key, row.status, row.detail) for row in rows] == [("owner", "passed", "Older green.")]


@pytest.mark.parametrize("path", ["instance", "queryset"])
def test_an_actor_deleting_evidence_deletes_the_results_it_supports(world: Any, path: str) -> None:
    """The owner deletes the results; the actor needs no authority on them."""

    admin = create_platform_admin(f"evidence-admin-{path}")
    supported = _record(world, "owner", "passed", evidence=(world.report,))
    kept = _record(world, "totals", "conflict", "Other source.", evidence=(world.hidden,))
    with actor_context(admin):
        if path == "instance":
            Report.objects.get(pk=world.report.pk).delete()
        else:
            Report.objects.filter(pk=world.report.pk).delete()
    assert set(ChecklistResult._base_manager.values_list("pk", flat=True)) == {kept.pk}
    assert not ChecklistEvidence._base_manager.filter(result_id=supported.pk).exists()


@pytest.mark.parametrize("path", ["instance", "queryset"])
def test_an_actor_deleting_a_checked_record_deletes_its_results_and_no_others(world: Any, path: str) -> None:
    admin = create_platform_admin(f"record-admin-{path}")
    _record(world, "owner", "passed", evidence=(world.report,))
    _record(world, "signature", "risk")
    with system_context(reason="tests.checklists.unrelated"):
        other = Inspection.objects.create(name="Gantry", notes="")
    _grant(other, "writer", world.writer)
    with actor_context(world.writer):
        untouched = other.checklist_record("owner", "passed", evidence=(world.report,))
    with actor_context(admin):
        if path == "instance":
            Inspection.objects.get(pk=world.inspection.pk).delete()
        else:
            Inspection.objects.filter(pk=world.inspection.pk).delete()
    assert set(ChecklistResult._base_manager.values_list("pk", flat=True)) == {untouched.pk}
    assert set(ChecklistEvidence._base_manager.values_list("result_id", flat=True)) == {untouched.pk}


def test_deleting_results_takes_one_read_and_two_deletes(world: Any) -> None:
    for key in ("owner", "signature", "totals"):
        _record(world, key, "passed", evidence=(world.report,))
    with CaptureQueriesContext(connection) as captured:
        assert ChecklistResult.objects.delete_results(models.Q(object_id=world.inspection.pk)) == 3
    checklist = [query["sql"] for query in captured if "test_checklists_" in query["sql"]]
    assert [sql.split()[0].upper() for sql in checklist] == ["SELECT", "DELETE", "DELETE"]
    assert not ChecklistResult._base_manager.exists() and not ChecklistEvidence._base_manager.exists()


def test_a_deleted_results_event_reaches_its_records_readers_without_the_result(
    world: Any,
    django_capture_on_commit_callbacks: Any,
) -> None:
    _record(world, "owner", "conflict", "Private finding.", evidence=(world.hidden,))
    with capture_broadcasts() as events, django_capture_on_commit_callbacks(execute=True):
        with system_context(reason="tests.checklists.delete_event"):
            Report.objects.filter(pk=world.hidden.pk).delete()
    [payload] = [event for event in events if event["model"] == "checklists.ChecklistResult"]
    assert payload["action"] == "delete"
    inspection = {"model": "checklistdemo.Inspection", "id": world.inspection.sqid}
    for actor, delivered in ((world.auditor, True), (world.outsider, False)):
        event = ChecklistResultChangeGate(ChecklistResult, to_subject_ref(actor)).filter(payload)
        if not delivered:
            assert event is None
            continue
        assert event is not None and event.id == "" and event.action == "delete"
        assert [{"model": ref.model, "id": ref.id} for ref in event.related_records] == [inspection]


def _override(permission: str, kind: str, expression: str) -> SchemaOverride:
    target = SchemaPermission.objects.get(definition__resource_type="checklists/result", name=permission)
    return SchemaOverride.objects.create(kind=kind, target=target, expression=expression, reason="tests.checklists")


@pytest.mark.parametrize(
    ("permission", "kind", "expression"),
    [
        pytest.param("read", SchemaOverride.KIND_TIGHTEN, "nil", id="tightened-read"),
        pytest.param(
            "target_read", SchemaOverride.KIND_EXTEND, "evidence->record_read", id="target-read-off-the-record"
        ),
    ],
)
def test_statuses_fall_back_to_read_when_the_policy_no_longer_decomposes_it(
    world: Any,
    permission: str,
    kind: str,
    expression: str,
) -> None:
    _record(world, "owner", "passed", evidence=(world.report,))
    _record(world, "signature", "risk", "Recorded without evidence.")
    assert read_decomposes()
    override = _override(permission, kind, expression)
    try:
        assert not read_decomposes()
        for actor in (world.writer, world.reader):
            readable = set(
                ChecklistResult.objects.with_actor(actor).with_action("read").values_list("key", flat=True),
            )
            statuses = _statuses(_assess(world, actor))
            assert {key for key, status in statuses.items() if status != "not_checked"} == readable, actor
            _assert_parity(world, actor)
    finally:
        override.delete()
    assert read_decomposes()


def test_edges_with_a_teardown_never_block_their_targets_deletion(db: None) -> None:
    del db
    assert not ChecklistEvidence.blocks_target_delete(Report)
    assert not ChecklistResult.blocks_target_delete(Inspection)
