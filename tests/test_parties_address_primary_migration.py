"""Rows-present proofs for the address invariant and its historical backfill."""

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, models, transaction
from django.db.migrations.state import ModelState, ProjectState
from rebac import PermissionDenied, actor_context, system_context

from angee.parties.runtime_migrations.address_one_primary import normalize_primaries
from tests.test_messaging import Address, Party, messaging_tables  # noqa: F401


@pytest.mark.django_db(transaction=True)
def test_primary_backfill_keeps_lowest_pk_with_historical_ordering() -> None:
    state = ProjectState()
    state.add_model(
        ModelState(
            "parties",
            "Party",
            [("id", models.AutoField(primary_key=True))],
            options={"db_table": "test_historical_party"},
        )
    )
    state.add_model(
        ModelState(
            "parties",
            "Address",
            [
                ("id", models.AutoField(primary_key=True)),
                ("party", models.ForeignKey("parties.Party", on_delete=models.CASCADE)),
                ("label", models.CharField(max_length=64)),
                ("is_primary", models.BooleanField(default=False)),
            ],
            options={"db_table": "test_historical_address", "ordering": ("label",)},
        )
    )
    party_model = state.apps.get_model("parties", "Party")
    address_model = state.apps.get_model("parties", "Address")
    with connection.schema_editor() as editor:
        editor.create_model(party_model)
        editor.create_model(address_model)
    try:
        first_party, second_party = party_model.objects.bulk_create([party_model(), party_model()])
        rows = address_model.objects.bulk_create(
            [
                address_model(party=first_party, label="Z", is_primary=True),
                address_model(party=first_party, label="A", is_primary=True),
                address_model(party=first_party, label="B", is_primary=False),
                address_model(party=second_party, label="B", is_primary=True),
            ]
        )
        with connection.schema_editor() as editor:
            normalize_primaries(state.apps, editor)
            normalize_primaries(state.apps, editor)
        assert list(address_model.objects.filter(is_primary=True).order_by("pk").values_list("pk", flat=True)) == [
            rows[0].pk,
            rows[3].pk,
        ]
        assert address_model.objects.count() == 4
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(address_model)
            editor.delete_model(party_model)


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("messaging_tables")
def test_primary_selection_is_atomic_scoped_and_obeys_update_fields() -> None:
    owner = get_user_model().objects.create_user(username="address-owner")
    stranger = get_user_model().objects.create_user(username="address-stranger")
    with system_context(reason="test address primaries"):
        party = Party.objects.create(display_name="Customer", created_by=owner)
        first = Address.objects.create(party=party, label="Old", is_primary=True, created_by=owner)
        second = Address.objects.create(party=party, label="New", created_by=owner)
        denied = Address.objects.create(party=party, label="Denied", created_by=stranger)

    denied.with_actor(stranger).is_primary = True
    with pytest.raises(PermissionDenied, match="current primary address"):
        denied.save(update_fields=["is_primary"])
    assert Address._base_manager.get(pk=first.pk).is_primary
    assert not Address._base_manager.get(pk=denied.pk).is_primary

    second.with_actor(owner).is_primary = True
    second.label = "Renamed"
    second.save(update_fields=["label"])
    assert Address._base_manager.get(pk=first.pk).is_primary
    assert not Address._base_manager.get(pk=second.pk).is_primary
    # A pinned actor remains authoritative even in another ambient actor's scope.
    with actor_context(stranger):
        second.save(update_fields=["is_primary"])
    assert not Address._base_manager.get(pk=first.pk).is_primary
    assert Address._base_manager.get(pk=second.pk).is_primary

    with pytest.raises(IntegrityError), transaction.atomic():
        Address._base_manager.filter(pk=first.pk).update(is_primary=True)
    assert Address._base_manager.filter(party=party, is_primary=True).count() == 1
