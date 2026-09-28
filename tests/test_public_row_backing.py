"""The dashboard and rate shared readers follow columns in both local stores."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from rebac import PermissionDenied, system_context, to_object_ref, to_subject_ref
from rebac.backends import LocalBackend, backend, reset_backend
from rebac.backends.local_query import LocalQueryScope
from rebac.models import Relationship, RelationshipRegistry
from rebac.preflight import _check_new_model
from rebac.schema import parse_zed

from tests.conftest import create_platform_admin, create_user
from tests.money_models import Currency, CurrencyRate
from tests.test_dashboards import DashboardTarget


@pytest.fixture(params=("denormalized", "registry"))
def public_policy(request, db, settings):
    """Use the adopters' real policies without a composed host or schema sync."""

    settings.REBAC_LOCAL_BACKEND_STORAGE = request.param
    settings.REBAC_SUPERUSER_BYPASS = False
    settings.REBAC_UNIVERSAL_ADMIN_ROLE = None
    reset_backend()
    active = backend()
    assert isinstance(active, LocalBackend)
    root = Path(__file__).parents[1] / "addons/angee"
    active.set_schema(parse_zed("\n".join(
        (root / addon / "permissions.zed").read_text()
        for addon in ("iam", "dashboards", "money")
    )))
    try:
        yield active
    finally:
        reset_backend()


@pytest.fixture(params=("dashboard", "rate"))
def candidates(request, public_policy):
    """Return valid unsaved matching and nonmatching adopter rows."""

    if request.param == "dashboard":
        owner = create_user("public-row-owner")
        return (
            DashboardTarget(name="Shared", scope="addon", scope_key="shared"),
            DashboardTarget(name="Owned", scope="addon", scope_key="owned", owner=owner),
        )
    with system_context(reason="test.public_row.currencies"):
        currency = Currency.objects.create(code="EUR", name="Euro")
        reference = Currency.objects.create(code="USD", name="US Dollar")
    return (
        CurrencyRate(currency=currency, date=date(2026, 1, 1), rate=Decimal("0.9")),
        CurrencyRate(
            currency=currency, date=date(2026, 1, 2), rate=Decimal("0.8"),
            context_content_type=ContentType.objects.get_for_model(Currency),
            context_object_id=str(reference.pk), reference_currency=reference,
        ),
    )


@pytest.fixture(params=("reader", "anonymous", "administrator"))
def actor(request, public_policy):
    if request.param == "anonymous":
        user = AnonymousUser()
    elif request.param == "administrator":
        user = create_platform_admin("public-row-administrator")
    else:
        user = create_user("public-row-reader")
    return request.param, to_subject_ref(user)


def _relationship_counts():
    return Relationship.objects.count(), RelationshipRegistry.objects.count()


def test_read_checks_and_sql_scope_agree(public_policy, candidates, actor):
    """Anonymous is denied; ordinary readers need a match; admins keep both rows."""

    kind, subject = actor
    model = type(candidates[0])
    resource_type = model._meta.rebac_resource_type
    before = _relationship_counts()
    with system_context(reason="test.public_row.seed"):
        for candidate in candidates:
            candidate.save()
    expected = {
        row.pk for index, row in enumerate(candidates)
        if kind == "administrator" or (kind == "reader" and index == 0)
    }
    with patch.object(public_policy, "accessible", side_effect=AssertionError("enumerated resource IDs")):
        with CaptureQueriesContext(connection) as compilation:
            predicate = LocalQueryScope(public_policy, subject, "default").predicate(model, "read", resource_type)
            query = model._base_manager.filter(predicate).order_by()
            sql, _params = query.query.sql_with_params()
        assert len(compilation) == 0
        assert "IS NULL" in sql
        assert set(query.values_list("pk", flat=True)) == expected
        assert set(model.objects.with_actor(subject).values_list("pk", flat=True)) == expected
        for row in candidates:
            assert public_policy.check_access(
                subject=subject, action="read", resource=to_object_ref(row),
            ).allowed == (row.pk in expected)
            assert row.with_actor(subject).has_access("read") == (row.pk in expected)
    assert _relationship_counts() == before


@pytest.mark.parametrize("read_gated_create", (False, True), ids=("production-create", "read-gated-create"))
def test_create_preflight_matches_persisted_policy(public_policy, candidates, actor, read_gated_create):
    """Native insertion agrees with preflight, including the row-filtered read arm.

    Production create admission is independent of sharing: dashboards allow
    signed-in users; money requires management. The second profile reuses the
    exact production read expression as create's test-only gate so the native
    candidate projection must evaluate the filtered constant before insertion.
    It never checks shared_reader directly or changes a production declaration.
    """

    kind, subject = actor
    model = type(candidates[0])
    resource_type = model._meta.rebac_resource_type
    if read_gated_create:
        schema = public_policy.schema()
        definition = schema.get_definition(resource_type)
        read = schema.get_permission(resource_type, "read")
        updated = replace(definition, permissions=tuple(
            replace(permission, expression=read.expression, raw_text=read.raw_text)
            if permission.name == "create" else permission
            for permission in definition.permissions
        ))
        public_policy.set_schema(replace(schema, definitions=[
            updated if item.resource_type == resource_type else item for item in schema.definitions
        ]))
    before = _relationship_counts()
    inserted = []
    with patch.object(public_policy, "accessible", side_effect=AssertionError("enumerated resource IDs")):
        for index, candidate in enumerate(candidates):
            readable = kind == "administrator" or (kind == "reader" and index == 0)
            allowed = readable if read_gated_create else (
                kind == "administrator" or (kind == "reader" and model is DashboardTarget)
            )
            assert _check_new_model(candidate, subject=subject, using="default").allowed == allowed
            if not allowed:
                with pytest.raises(PermissionDenied), transaction.atomic():
                    model.objects.with_actor(subject).insert(candidate)
                assert candidate.pk is None
                continue
            row = model.objects.with_actor(subject).insert(candidate)
            inserted.append(row)
            assert public_policy.check_access(
                subject=subject, action="create", resource=to_object_ref(row),
            ).allowed
            assert public_policy.check_access(
                subject=subject, action="read", resource=to_object_ref(row),
            ).allowed == readable
            assert model.objects.with_actor(subject).filter(pk=row.pk).exists() == readable
    assert model._base_manager.count() == len(inserted)
    assert _relationship_counts() == before


def test_bulk_creation_and_column_updates_change_reads_without_tuples(public_policy, candidates):
    """Bulk inserts and trusted column updates require no tuple reconciliation."""

    reader = to_subject_ref(create_user("public-row-bulk-reader"))
    administrator = create_platform_admin("public-row-bulk-administrator")
    anonymous = to_subject_ref(AnonymousUser())
    model = type(candidates[0])
    columns = ("owner_id",) if model is DashboardTarget else (
        "context_content_type_id", "context_object_id", "reference_currency_id",
    )
    matching, nonmatching = ({name: getattr(row, name) for name in columns} for row in candidates)
    before = _relationship_counts()
    with patch.object(public_policy, "accessible", side_effect=AssertionError("enumerated resource IDs")):
        shared, private = model.objects.with_actor(administrator).bulk_create(candidates)
        assert set(model.objects.with_actor(reader).values_list("pk", flat=True)) == {shared.pk}
        assert _relationship_counts() == before
        for row, values, expected in (
            (shared, nonmatching, set()),
            (private, matching, {private.pk}),
            (shared, matching, {shared.pk, private.pk}),
            (private, nonmatching, {shared.pk}),
        ):
            # Rate identity remains immutable through its public queryset. A
            # trusted data migration can update columns via Django's base manager;
            # the derived permission must immediately follow those persisted facts.
            assert model._base_manager.filter(pk=row.pk).update(**values) == 1
            assert set(model.objects.with_actor(reader).values_list("pk", flat=True)) == expected
            for persisted in (shared, private):
                assert public_policy.check_access(
                    subject=reader, action="read", resource=to_object_ref(persisted),
                ).allowed == (persisted.pk in expected)
            assert not model.objects.with_actor(anonymous).exists()
            assert set(model.objects.with_actor(administrator).values_list("pk", flat=True)) == {
                shared.pk, private.pk,
            }
            assert _relationship_counts() == before
