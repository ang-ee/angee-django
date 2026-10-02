"""Stored history values survive actor redaction and transactional deletion."""

import pytest
from django.db import transaction
from rebac import actor_context
from rebac.backends import reset_backend
from rebac.schema import parse_zed

from angee.testing.permissions import install_permission_schema
from tests.conftest import create_user
from tests.core_persistence import OwnedRow


@pytest.fixture(params=("registry", "denormalized"))
def history_permissions(db, settings, request):
    """Use the existing persistence probe with a deliberately unreadable title."""

    settings.REBAC_LOCAL_BACKEND_STORAGE = request.param
    install_permission_schema(parse_zed("""
        definition auth/user {}
        definition scopedemo/owned_row {
            relation owner: auth/user // rebac:field=owner
            relation title_reader: auth/user
            permission create = authenticated
            permission read = owner
            permission write = owner
            permission delete = owner
            permission read__title = title_reader
        }
    """))
    try:
        yield
    finally:
        reset_backend()


@pytest.mark.parametrize("queryset_delete", (False, True))
def test_delete_snapshot_retains_stored_value_and_rolls_back(history_permissions, queryset_delete):
    actor = create_user("history-owner")
    with actor_context(actor):
        row = OwnedRow.objects.create(title="Stored secret")
        row_pk = row.pk
        history = row.history.model.objects.filter(id=row_pk)
        visible = OwnedRow.objects.get(pk=row_pk)
        assert visible.title is None
        with pytest.raises(RuntimeError, match="rollback"), transaction.atomic():
            if queryset_delete:
                OwnedRow.objects.filter(pk=row_pk).delete()
            else:
                visible.delete()
            assert history.get(history_type="-").title == "Stored secret"
            raise RuntimeError("rollback")
        assert not history.filter(history_type="-").exists()
        visible = OwnedRow.objects.get(pk=row_pk)
        if queryset_delete:
            OwnedRow.objects.filter(pk=row_pk).delete()
        else:
            visible.delete()
        assert visible.title is None
    assert history.get(history_type="-").title == "Stored secret"
    assert not history.model._meta.get_field("title").null
    assert not OwnedRow._base_manager.filter(pk=row_pk).exists()
