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

from angee.portfolio.testing.models import Initiative, ProductRow, Update
from tests.conftest import create_user

pytestmark = pytest.mark.django_db(transaction=True)


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
        product = ProductRow.objects.create(name="Phasal")
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
