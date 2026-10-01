"""Only a one-save allowance may change a write-once identity or receipt."""

import pytest
from django.core.exceptions import ValidationError

from tests.conftest import create_user
from tests.core_persistence import ReceiptRow

pytestmark = pytest.mark.django_db


def test_direct_save_cannot_change_an_immutable_field():
    row = ReceiptRow.objects.create(identity="original")
    row.identity = "changed"
    with pytest.raises(ValidationError) as caught:
        row.save()
    assert set(caught.value.message_dict) == {"identity"}
    row.refresh_from_db()
    assert row.identity == "original"


def test_allowance_applies_to_only_one_save():
    row = ReceiptRow.objects.create(identity="original")
    row.allow_immutable_save("identity")
    row.identity = "allowed"
    row.save()
    row.refresh_from_db()
    assert row.identity == "allowed"
    row.identity = "refused"
    with pytest.raises(ValidationError):
        row.save()
    row.refresh_from_db()
    assert row.identity == "allowed"


def test_failed_save_also_consumes_the_allowance():
    recipient = create_user("recipient")
    row = ReceiptRow.objects.create(identity="original")
    row.identity, row.recipient = "allowed", recipient
    row.allow_immutable_save("identity")
    with pytest.raises(ValidationError) as caught:
        row.save()
    assert set(caught.value.message_dict) == {"recipient"}
    row.recipient = None
    with pytest.raises(ValidationError, match="identity"):
        row.save()


def test_foreign_key_allowance_uses_attname_and_retains_other_guards():
    recipient = create_user("recipient")
    row = ReceiptRow.objects.create(identity="original")
    row.recipient = recipient
    row.allow_immutable_save("recipient_id")
    row.save()
    row.refresh_from_db()
    assert row.recipient_id == recipient.pk
    row.recipient = None
    with pytest.raises(ValidationError, match="recipient"):
        row.save()


def test_deferred_row_can_edit_mutable_content_without_changing_receipts():
    row = ReceiptRow.objects.create(identity="original")
    partial = ReceiptRow.objects.only("id", "title").get(pk=row.pk)
    partial.title = "updated"
    partial.save(update_fields=["title"])
    row.refresh_from_db()
    assert (row.identity, row.title) == ("original", "updated")
