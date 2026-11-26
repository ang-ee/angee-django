"""Only a one-save allowance may change a write-once identity or receipt."""

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.forms import modelform_factory
from django.test.utils import CaptureQueriesContext

from tests.conftest import create_user
from tests.core_persistence import ReceiptRow

pytestmark = pytest.mark.django_db

REFUSAL = "This identity or receipt is immutable."


@pytest.mark.parametrize(
    ("attname", "key"),
    [
        pytest.param("recipient_id", "recipient", id="a-relation-under-its-name"),
        pytest.param("external_id", "external_id", id="a-plain-field-named-like-an-attname-under-its-own"),
    ],
)
def test_a_refusal_is_keyed_by_the_field_a_form_binds(attname, key):
    """A relation changed through its attname is refused under the relation; a plain ``..._id`` field under itself."""

    row = ReceiptRow.objects.create(identity="original")
    setattr(row, attname, create_user("receipt-recipient").pk if attname == "recipient_id" else "another")
    with pytest.raises(ValidationError) as caught:
        row.save()
    assert caught.value.message_dict == {key: [REFUSAL]}
    form = modelform_factory(ReceiptRow, fields=("identity", "recipient", "external_id"))(data={"identity": "original"})
    form.is_valid()
    form.add_error(None, caught.value)  # Raises for a key that names no field of the form.
    assert REFUSAL in form.errors[key]


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


def test_a_partial_save_checks_only_the_immutable_fields_it_writes():
    recipient = create_user("recipient")
    row = ReceiptRow.objects.create(identity="original")
    row.identity, row.title = "unsaved", "updated"
    with CaptureQueriesContext(connection) as queries:
        row.save(update_fields=["title"])
    assert [query["sql"] for query in queries.captured_queries if query["sql"].startswith("SELECT")] == []
    row.recipient = recipient
    with pytest.raises(ValidationError, match="recipient"):
        row.save(update_fields=["recipient"])
    row.refresh_from_db()
    assert (row.identity, row.title, row.recipient_id) == ("original", "updated", None)
