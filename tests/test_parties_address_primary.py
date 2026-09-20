"""Current address primary-selection contracts."""

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from rebac import PermissionDenied, actor_context, system_context

from tests.test_messaging import Address, Party, messaging_tables  # noqa: F401


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
