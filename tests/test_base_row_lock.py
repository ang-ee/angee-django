"""System row locks apply equally to native collection persistence."""

import pytest
from django.core.exceptions import ValidationError
from django.db import models
from django.test.utils import isolate_apps
from rebac import actor_context, system_context

from angee.base.models.locks import RowLockMixin
from tests.conftest import create_user
from tests.linesdemo.models import Document, PinnedLine


@pytest.mark.django_db(transaction=True)
def test_collection_writes_cannot_mutate_or_create_system_rows(composed_tables: None) -> None:
    user = create_user("row-lock-writer")
    with system_context(reason="test.row_lock.seed"):
        parent = Document.objects.create(title="Document")
        row = PinnedLine.objects.create(document=parent, label="System", pinned=True)
    with actor_context(user):
        with pytest.raises(ValidationError):
            PinnedLine.objects.filter(pk=row.pk).update(label="Changed")
        with pytest.raises(ValidationError):
            PinnedLine.objects.filter(pk=row.pk).delete()
        with pytest.raises(ValidationError):
            PinnedLine.objects.bulk_create([PinnedLine(document=parent, label="New system", pinned=True)])
        PinnedLine.objects.filter(pk=row.pk).update(quantity=models.F("quantity") + 1)
    row.refresh_from_db()
    assert (row.label, row.quantity) == ("System", 2)


@isolate_apps()
def test_row_lock_model_requires_collection_guard_on_default_and_base_managers() -> None:
    class Unguarded(RowLockMixin, models.Model):
        objects = models.Manager()
        _angee_base = models.Manager()

        class Meta:
            app_label = "base"
            base_manager_name = "_angee_base"

    errors = {error.id for error in Unguarded.check()}
    assert {"angee.E048", "angee.E049"} <= errors
