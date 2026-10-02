"""Shared reads follow row columns and multi-table parents in both local stores."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from rebac import PermissionDenied, SubjectRef, system_context, to_object_ref, to_subject_ref
from rebac.backends import LocalBackend, backend, reset_backend
from rebac.models import Relationship, RelationshipRegistry
from rebac.preflight import _check_new_model
from rebac.schema import parse_zed

from angee.projects.testing.models import Queue
from angee.spaces.testing.models import Group
from angee.testing.permissions import install_permission_schema
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
    install_permission_schema(parse_zed("\n".join(
        (root / addon / "permissions.zed").read_text()
        for addon in ("iam", "dashboards", "money", "spaces", "work")
    )), active=active)
    try:
        yield active
    finally:
        reset_backend()


@pytest.fixture(params=("dashboard", "rate", "group", "queue"))
def candidates(request, public_policy):
    """Return valid unsaved matching and nonmatching adopter rows."""

    if request.param == "dashboard":
        owner = create_user("public-row-owner")
        return (
            DashboardTarget(name="Shared", scope="addon", scope_key="shared"),
            DashboardTarget(name="Owned", scope="addon", scope_key="owned", owner=owner),
        )
    if request.param in {"group", "queue"}:
        model = Group if request.param == "group" else Queue
        owner = create_user("public-row-owner") if model is Group else None
        return tuple(
            model(
                name=name, slug=name.lower(), visibility=visibility, owner=owner,
                **({"key": name.upper()} if model is Queue else {}),
            )
            for name, visibility in (("Public", "public"), ("Private", "private"))
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
    if request.param == "service":
        return request.param, SubjectRef.of("service/principal", "public-row-reader")
    if request.param == "anonymous":
        user = AnonymousUser()
    elif request.param == "administrator":
        user = create_platform_admin("public-row-administrator")
    else:
        user = create_user("public-row-reader")
    return request.param, to_subject_ref(user)


def _relationship_counts():
    return Relationship.objects.count(), RelationshipRegistry.objects.count()


@pytest.mark.parametrize("actor", ("reader", "service", "anonymous", "administrator"), indirect=True)
def test_read_checks_and_sql_scope_agree(public_policy, candidates, actor):
    """Anonymous is denied; ordinary readers need a match; admins keep both rows."""

    kind, subject = actor
    model = type(candidates[0])
    before = _relationship_counts()
    with system_context(reason="test.public_row.seed"):
        for candidate in candidates:
            candidate.save()
    expected = {
        row.pk for index, row in enumerate(candidates)
        if kind == "administrator" or (kind in {"reader", "service"} and index == 0)
    }
    with patch.object(public_policy, "accessible", side_effect=AssertionError("enumerated resource IDs")):
        query = model.objects.with_actor(subject).scoped().order_by()
        assert set(query.values_list("pk", flat=True)) == expected
        assert set(model.objects.with_actor(subject).values_list("pk", flat=True)) == expected
        for row in candidates:
            assert public_policy.check_access(
                subject=subject, action="read", resource=to_object_ref(row),
            ).allowed == (row.pk in expected)
            assert row.with_actor(subject).has_access("read") == (row.pk in expected)
            if model is Queue:
                parent = Group._base_manager.get(pk=row.pk)
                assert public_policy.check_access(
                    subject=subject, action="read", resource=to_object_ref(parent),
                ).allowed == (row.pk in expected)
    assert _relationship_counts() == before


@pytest.mark.parametrize(
    ("candidates", "read_gated_create"),
    [(model, gate) for model in ("dashboard", "rate", "group", "queue")
     for gate in ((False,) if model == "queue" else (False, True))],
    indirect=("candidates",),
)
def test_create_preflight_matches_persisted_policy(public_policy, candidates, actor, read_gated_create):
    """Native insertion agrees with preflight, including the row-filtered read arm.

    Production create admission is independent of sharing: dashboards allow
    signed-in users, as do groups and queues; money requires management.
    The second profile applies only to the owners of filtered constants: it reuses the
    exact production read expression as create's test-only gate so the native
    candidate projection must evaluate the filtered constant before insertion.
    It never checks shared_reader directly or changes a production declaration.
    """

    kind, subject = actor
    model = type(candidates[0])
    resource_type = model._meta.rebac_resource_type
    stranger = to_subject_ref(create_user("public-row-stranger"))
    if read_gated_create:
        schema = public_policy.schema()
        definition = schema.get_definition(resource_type)
        read = schema.get_permission(resource_type, "read")
        updated = replace(definition, permissions=tuple(
            replace(permission, expression=read.expression, raw_text=read.raw_text)
            if permission.name == "create" else permission
            for permission in definition.permissions
        ))
        install_permission_schema(replace(schema, definitions=[
            updated if item.resource_type == resource_type else item for item in schema.definitions
        ]), active=public_policy)
    before = _relationship_counts()
    inserted = []
    with patch.object(public_policy, "accessible", side_effect=AssertionError("enumerated resource IDs")):
        for index, candidate in enumerate(candidates):
            readable = kind == "administrator" or (kind == "reader" and (index == 0 or model is Queue))
            allowed = readable if read_gated_create else (
                kind == "administrator" or (kind == "reader" and model is not CurrencyRate)
            )
            assert _check_new_model(candidate, subject=subject, using="default").allowed == allowed
            if not allowed:
                with pytest.raises(PermissionDenied), transaction.atomic():
                    model.objects.with_actor(subject).insert(candidate)
                assert candidate.pk is None
                continue
            row = model.objects.with_actor(subject).insert(candidate)
            inserted.append(row)
            if model is Queue:
                assert str(row.owner_id) == subject.subject_id
            assert public_policy.check_access(
                subject=subject, action="create", resource=to_object_ref(row),
            ).allowed
            assert public_policy.check_access(
                subject=subject, action="read", resource=to_object_ref(row),
            ).allowed == readable
            assert model.objects.with_actor(subject).filter(pk=row.pk).exists() == readable
            assert public_policy.check_access(
                subject=stranger, action="read", resource=to_object_ref(row),
            ).allowed == (index == 0)
            assert model.objects.with_actor(stranger).filter(pk=row.pk).exists() == (index == 0)
    assert model._base_manager.count() == len(inserted)
    assert _relationship_counts() == before


def test_creation_and_column_updates_change_reads_without_tuples(public_policy, candidates):
    """Ordinary hierarchy inserts and bulk reference inserts need no reader tuples."""

    reader = to_subject_ref(create_user("public-row-bulk-reader"))
    administrator = create_platform_admin("public-row-bulk-administrator")
    anonymous = to_subject_ref(AnonymousUser())
    model = type(candidates[0])
    if issubclass(model, Group):
        columns = ("visibility",)
    elif model is DashboardTarget:
        columns = ("owner_id",)
    else:
        columns = ("context_content_type_id", "context_object_id", "reference_currency_id")
    matching, nonmatching = ({name: getattr(row, name) for name in columns} for row in candidates)
    before = _relationship_counts()
    with patch.object(public_policy, "accessible", side_effect=AssertionError("enumerated resource IDs")):
        manager = model.objects.with_actor(administrator)
        # Hierarchy saves derive paths, including the parent path of multi-table children.
        shared, private = (
            [manager.insert(row) for row in candidates] if issubclass(model, Group) else manager.bulk_create(candidates)
        )
        if issubclass(model, Group):
            assert all(row.path for row in (shared, private))
        assert set(model.objects.with_actor(reader).values_list("pk", flat=True)) == {shared.pk}
        assert _relationship_counts() == before
        for row, values, expected in (
            (shared, nonmatching, set()),
            (private, matching, {private.pk}),
            (shared, matching, {shared.pk, private.pk}),
            (private, nonmatching, {shared.pk}),
        ):
            # Rate identity remains immutable through its public queryset. A
            # trusted data migration uses Django's base manager with explicit system
            # authority; derived permissions immediately follow the persisted facts.
            queryset = (
                Group.system_queryset().filter(pk=row.pk)
                if issubclass(model, Group) else model._base_manager.filter(pk=row.pk)
            )
            with system_context(reason="test.public_row.trusted_column_update"):
                assert queryset.update(**values) == 1
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
