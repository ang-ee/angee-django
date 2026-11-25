"""``require_save_allowed`` decides a save as the save itself would, before anything validates it."""

from __future__ import annotations

import pytest
from rebac import PermissionDenied, actor_context, system_context

from angee.base.permissions import require_save_allowed
from tests.conftest import create_user
from tests.scopedemo.models import FactoryCompanion, ScopedDoc, ValidatedDoc


@pytest.mark.django_db
def test_a_partial_save_is_decided_on_the_fields_it_writes() -> None:
    """A writer may save the title alone; the full save, which would also write the gated code, is refused."""

    member = create_user("partial-writer")
    with system_context(reason="test.save_decision.stored"):
        stored = ValidatedDoc.objects.create(title="a", code="stored")
    with actor_context(member):
        row = ValidatedDoc.objects.get(pk=stored.pk)
        row.title, row.code = "aa", "changed"
        require_save_allowed(row, update_fields={"title"})
        with pytest.raises(PermissionDenied):
            require_save_allowed(row)


def test_a_model_without_rebac_gates_has_no_save_to_refuse() -> None:
    """A plain Django row is never rehearsed: nothing in its save can refuse a caller."""

    require_save_allowed(FactoryCompanion(document_id=1))


@pytest.mark.django_db
def test_an_unscoped_save_is_not_rehearsed() -> None:
    """Under a bypass nothing can refuse the save, so its own ``save()`` does not run early."""

    with system_context(reason="test.save_decision.unscoped"):
        require_save_allowed(ScopedDoc(title="Unscoped"))
