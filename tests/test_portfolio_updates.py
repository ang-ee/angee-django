"""Health reports authorize their target through the portfolio schema."""

from typing import Any

import pytest
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models.deletion import Collector
from django.db.models.signals import pre_delete
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
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

from angee.base.models.refs import delete_record_refs
from angee.portfolio.testing.models import Initiative, Product, Update
from tests.conftest import create_user

pytestmark = pytest.mark.django_db(transaction=True)


def test_portfolio_hooks_leave_an_unrelated_session_fast_deletable() -> None:
    """The portfolio sender scope permits native fast delete once base policy is absent."""

    # The source-test app's definition-less record edges make base's receiver
    # apply to Session until P10b; isolate portfolio's own binding here.
    uid = "angee.record_refs.target_delete.sessions.session"
    assert pre_delete.disconnect(sender=Session, dispatch_uid=uid)
    try:
        assert Collector(using=connection.alias).can_fast_delete(Session)
        session = Session.objects.create(
            session_key="portfolio-fast-delete", session_data="", expire_date=timezone.now(),
        )
        Session.objects.filter(pk=session.pk).delete()
        assert not Session.objects.filter(pk=session.pk).exists()
    finally:
        pre_delete.connect(delete_record_refs, sender=Session, dispatch_uid=uid)


@pytest.fixture
def reportable(composed_permissions: None) -> dict[str, Any]:
    """A portfolio manager, a signed-in reader, an initiative and a phasal product."""

    del composed_permissions
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


def test_partial_save_and_collector_delete_project_persisted_health(reportable: dict[str, Any]) -> None:
    """Unsaved health never leaks into the target; queryset deletes repair it."""

    initiative = reportable["initiative"]
    with actor_context(reportable["manager"]):
        first = Update.objects.report(target=initiative, health="on_track")
        second = Update.objects.report(target=initiative, health="at_risk")
        second.health = "off_track"
        second.body = "Body only"
        second.save(update_fields=("body",))
        initiative.refresh_from_db()
        assert initiative.health == "at_risk"
        assert initiative.health_updated_at == second.updated_at
        Update.objects.filter(pk=second.pk).delete()
        initiative.refresh_from_db()
        assert (initiative.health, initiative.health_updated_at) == ("on_track", first.updated_at)
        Update.objects.filter(pk=first.pk).delete()
        initiative.refresh_from_db()
        assert (initiative.health, initiative.health_updated_at) == (None, None)


@pytest.mark.parametrize("size", [1, 10, 100])
def test_report_batch_delete_refreshes_health_once(reportable: dict[str, Any], size: int) -> None:
    """One target projection covers every report removed by the collector."""

    initiative = reportable["initiative"]
    with system_context(reason="portfolio.update.batch_seed"):
        rows = [Update.objects.create(target=initiative, health="at_risk") for _ in range(size)]
    with system_context(reason="portfolio.update.batch_delete"), CaptureQueriesContext(connection) as queries:
        Update.objects.filter(pk__in=[row.pk for row in rows]).delete()
    initiative.refresh_from_db()
    assert (initiative.health, initiative.health_updated_at) == (None, None)
    assert len(queries) <= 50


def test_report_bulk_projection_mutations_require_the_row_owner(reportable: dict[str, Any]) -> None:
    with actor_context(reportable["manager"]):
        report = Update.objects.report(target=reportable["initiative"], health="on_track")
        with pytest.raises(ValidationError, match="instance save"):
            Update.objects.filter(pk=report.pk).update(health="off_track")
        with pytest.raises(ValidationError, match="instance save"):
            Update.objects.bulk_create([Update(target=reportable["initiative"], health="off_track")])


def test_health_projection_preserves_graphql_enum_names_and_required_report_health() -> None:
    """Consolidating domain choices keeps both historical transport contracts."""

    from angee.portfolio import schema

    report_health = next(field for field in schema.PortfolioUpdateType.__strawberry_definition__.fields
                         if field.python_name == "health")
    initiative_health = next(field for field in schema.InitiativeType.__strawberry_definition__.fields
                             if field.python_name == "health")
    assert report_health.type is schema.UpdateHealthChoice.__strawberry_definition__
    assert initiative_health.type.of_type is schema.InitiativeHealthChoice.__strawberry_definition__
    assert schema.UpdateHealthChoice.__strawberry_definition__.name == "UpdateHealth"
    assert schema.InitiativeHealthChoice.__strawberry_definition__.name == "InitiativeHealth"
