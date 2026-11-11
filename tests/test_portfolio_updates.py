"""Health reports authorize their target through the portfolio schema."""

from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import (
    ObjectRef,
    PermissionDenied,
    RelationshipTuple,
    actor_context,
    generic_target,
    system_context,
    to_subject_ref,
    write_relationships,
)

from angee.portfolio.testing.models import Initiative, Product, Update
from tests.conftest import create_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def reportable(composed_tables: None) -> dict[str, Any]:
    """A portfolio manager, a signed-in reader, an initiative and a phasal product."""

    del composed_tables
    manager, reader = create_user("report-manager"), create_user("report-reader")
    write_relationships([
        RelationshipTuple(ObjectRef("portfolio/role", "portfolio_admin"), "member", to_subject_ref(manager)),
    ])
    with system_context(reason="test.updates.seed"):
        initiative = Initiative.objects.create(name="Strategic")
        product = Product.objects.create(name="Phasal")
    return {"manager": manager, "reader": reader, "initiative": initiative, "product": product}


def test_report_requires_write_on_the_target_and_denormalizes_health(reportable: dict[str, Any]) -> None:
    manager, reader, initiative = reportable["manager"], reportable["reader"], reportable["initiative"]
    with actor_context(manager):
        report = Update.objects.report(target=initiative, health="at_risk", body="Slipping")
        assert report.actor() is not None
    initiative.refresh_from_db()
    assert (initiative.health, initiative.health_updated_at) == ("at_risk", report.updated_at)
    assert initiative.with_actor(reader).has_access("read")
    with actor_context(reader), pytest.raises(PermissionDenied):
        Update.objects.report(target=initiative, health="on_track")
    with actor_context(reader), CaptureQueriesContext(connection) as queries:
        rows = list(Update.objects.filter(**generic_target(initiative).lookups(Update, "target")))
    assert [row.pk for row in rows] == [report.pk]
    assert sum(Update._meta.db_table in query["sql"] for query in queries.captured_queries) == 1
    with actor_context(reader), pytest.raises(PermissionDenied):
        Update.objects.get(pk=report.pk).delete()
    with actor_context(manager):
        report.body = "Recovered"
        report.save(update_fields=("body", "updated_at"))
        report.target = reportable["product"]
        with pytest.raises(ValidationError, match="may target only"):
            report.save()
        Update.objects.get(pk=report.pk).delete()
    initiative.refresh_from_db()
    assert (initiative.health, initiative.health_updated_at) == (None, None)


def test_report_refuses_phasal_and_undeclared_targets(reportable: dict[str, Any]) -> None:
    manager, product = reportable["manager"], reportable["product"]
    with actor_context(manager):
        with pytest.raises(ValidationError, match="may target only"):
            Update.objects.report(target=product, health="on_track")
        with pytest.raises(ValidationError, match="may target only"):
            Update.objects.create(target=product, health="on_track")
    with system_context(reason="test.updates.system"):
        # System writes still enter declared edges; the phasal product has no arm either way.
        row = Update.objects.create(target=reportable["initiative"], health="on_track", body="Seeded")
        assert row.content_type == generic_target(reportable["initiative"]).content_type
        assert Update.objects.count() == 1


def test_report_and_target_health_share_one_graphql_enum() -> None:
    """A report's health and its target's projection are one vocabulary with one GraphQL name."""

    from angee.portfolio import schema

    report_health = next(
        field for field in schema.PortfolioUpdateType.__strawberry_definition__.fields if field.python_name == "health"
    )
    initiative_health = next(
        field for field in schema.InitiativeType.__strawberry_definition__.fields if field.python_name == "health"
    )
    assert report_health.type is schema.HealthChoice.__strawberry_definition__
    assert initiative_health.type.of_type is schema.HealthChoice.__strawberry_definition__
    assert schema.HealthChoice.__strawberry_definition__.name == "Health"


def test_partial_save_and_collector_delete_project_persisted_health(reportable: dict[str, Any]) -> None:
    """Unsaved health never reaches the target; instance and queryset deletes re-project the survivors."""

    initiative = reportable["initiative"]
    with actor_context(reportable["manager"]):
        first = Update.objects.report(target=initiative, health="on_track")
        second = Update.objects.report(target=initiative, health="at_risk")
        second.health = "off_track"
        second.body = "Body only"
        second.save(update_fields=("body", "updated_at"))
        initiative.refresh_from_db()
        assert (initiative.health, initiative.health_updated_at) == ("at_risk", second.updated_at)
        # A queryset delete goes through the collector's receivers, never Django's fast delete.
        Update.objects.filter(pk=second.pk).delete()
        initiative.refresh_from_db()
        assert (initiative.health, initiative.health_updated_at) == ("on_track", first.updated_at)
        first.delete()
        initiative.refresh_from_db()
        assert (initiative.health, initiative.health_updated_at) == (None, None)


def test_bulk_projection_writes_require_the_report_owner(reportable: dict[str, Any]) -> None:
    with actor_context(reportable["manager"]):
        report = Update.objects.report(target=reportable["initiative"], health="on_track", body="Kept")
        with pytest.raises(ValidationError, match="instance save"):
            Update.objects.filter(pk=report.pk).update(health="off_track")
        with pytest.raises(ValidationError, match="instance save"):
            Update.objects.bulk_create([Update(target=reportable["initiative"], health="off_track")])
        assert Update.objects.filter(pk=report.pk).update(body="Edited") == 1
    reportable["initiative"].refresh_from_db()
    assert reportable["initiative"].health == "on_track"


def test_report_target_is_write_once(reportable: dict[str, Any]) -> None:
    with system_context(reason="test.updates.other_target"):
        moved_to = Initiative.objects.create(name="Other")
    with actor_context(reportable["manager"]):
        report = Update.objects.report(target=reportable["initiative"], health="on_track")
        report.target = moved_to
        with pytest.raises(ValidationError) as refused:
            report.save()
    assert set(refused.value.message_dict) == {"object"}
    assert Update._base_manager.get(pk=report.pk).object_id == reportable["initiative"].pk
