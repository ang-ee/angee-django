"""Each public-row predicate is live for non-user subjects and compound filters."""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.db.models import Count
from rebac import SubjectRef, system_context, to_object_ref

from tests.money_models import Currency, CurrencyRate
from tests.t3_campaign import relationship_snapshot
from tests.test_public_row_backing import candidates as candidates
from tests.test_public_row_backing import public_policy as public_policy


@pytest.mark.parametrize("subject_type", ("service/principal", "auth/group"))
def test_non_user_public_read_scopes_filter_aggregates_without_enumeration(
    public_policy,
    candidates,
    subject_type,
    monkeypatch,
):
    subject = SubjectRef.of(subject_type, "t3-filtered-reader")
    with system_context(reason="tests.t3.public_rows"):
        for candidate in candidates:
            candidate.save()
    matching, hidden = candidates
    before = relationship_snapshot()
    monkeypatch.setattr(public_policy, "accessible", lambda **kwargs: pytest.fail("Read scope enumerated IDs"))
    model = type(matching)
    rows = model.objects.with_actor(subject).filter(pk__in=(matching.pk, hidden.pk))
    assert rows.aggregate(total=Count("pk")) == {"total": 1}
    assert list(rows.values("pk").annotate(total=Count("pk"))) == [{"pk": matching.pk, "total": 1}]
    for row, allowed in ((matching, True), (hidden, False)):
        assert (
            public_policy.check_access(subject=subject, action="read", resource=to_object_ref(row)).allowed is allowed
        )
    assert relationship_snapshot() == before


@pytest.mark.parametrize("private_column", ("context_content_type_id", "context_object_id", "reference_currency_id"))
def test_partial_currency_context_is_refused_and_complete_context_revokes_global_read(public_policy, private_column):
    with system_context(reason="tests.t3.currency_columns"):
        currency = Currency.objects.create(code="USD", name="Dollar")
        reference = Currency.objects.create(code="EUR", name="Euro")
        row = CurrencyRate.objects.create(currency=currency, date=date(2026, 9, 1), rate=Decimal("1.2"))
    subject = SubjectRef.of("service/principal", "t3-currency-reader")
    assert CurrencyRate.objects.with_actor(subject).filter(pk=row.pk).exists()
    before = relationship_snapshot()
    values = {
        "context_content_type_id": ContentType.objects.get_for_model(Currency).pk,
        "context_object_id": str(reference.pk),
        "reference_currency_id": reference.pk,
    }
    # Each partial context is invalid at the native database constraint.
    with pytest.raises(IntegrityError, match="money_rate_context_complete"), transaction.atomic():
        CurrencyRate._base_manager.filter(pk=row.pk).update(**{private_column: values[private_column]})
    assert CurrencyRate.objects.with_actor(subject).filter(pk=row.pk).exists()
    CurrencyRate._base_manager.filter(pk=row.pk).update(**values)
    assert not CurrencyRate.objects.with_actor(subject).filter(pk=row.pk).exists()
    CurrencyRate._base_manager.filter(pk=row.pk).update(
        context_content_type_id=None,
        context_object_id="",
        reference_currency_id=None,
    )
    assert CurrencyRate.objects.with_actor(subject).filter(pk=row.pk).exists()
    assert relationship_snapshot() == before
