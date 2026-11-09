"""Typed row references carry a primary key and report their field on refusal."""

import pytest
from django.contrib.auth import get_user_model
from pydantic import BaseModel, ValidationError

from angee.base.validators import SavedRow


class RowReferences(BaseModel):
    owner: SavedRow
    records: tuple[SavedRow, ...] = ()


def test_saved_row_refusal_names_the_field_and_nested_location():
    user = get_user_model()
    with pytest.raises(ValidationError) as error:
        RowReferences(owner=user(pk=1), records=(user(),))
    issue, = error.value.errors()
    assert issue["loc"] == ("records", 0)
    assert "records must name a saved row" in issue["msg"]
    with pytest.raises(ValidationError, match="owner must name a saved row"):
        RowReferences(owner=user())


def test_saved_row_uses_primary_key_identity_without_a_database_read():
    row = get_user_model()(pk=1)
    assert row._state.adding
    assert RowReferences(owner=row).owner is row
    with pytest.raises(ValidationError):
        RowReferences(owner=object())
