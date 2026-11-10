"""Typed row references require Django's persisted state and report their field on refusal."""

import pytest
from django.contrib.auth import get_user_model
from pydantic import BaseModel, ValidationError

from angee.base.validators import SavedRow, is_saved_row
from angee.decisions.contracts import DecisionRequest


class RowReferences(BaseModel):
    owner: SavedRow
    records: tuple[SavedRow, ...] = ()


def test_saved_row_refusal_names_the_field_and_nested_location():
    user = get_user_model()
    with pytest.raises(ValidationError) as error:
        RowReferences(owner=user.from_db("default", ["id"], [1]), records=(user(),))
    issue, = error.value.errors()
    assert issue["loc"] == ("records", 0)
    assert "records must name a saved row" in issue["msg"]
    with pytest.raises(ValidationError, match="owner must name a saved row"):
        RowReferences(owner=user())


def test_saved_row_refuses_a_constructed_primary_key_without_a_database_read():
    row = get_user_model()(pk=1)
    assert row._state.adding
    assert not is_saved_row(row)
    with pytest.raises(ValidationError, match="owner must name a saved row"):
        RowReferences(owner=row)


def test_saved_row_accepts_loaded_rows_and_refuses_deleted_identity():
    row = get_user_model().from_db("default", ["id"], [1])
    assert is_saved_row(row)
    assert RowReferences(owner=row).owner is row
    row.pk = None
    assert not is_saved_row(row)
    with pytest.raises(ValidationError):
        RowReferences(owner=object())


def test_decision_request_refuses_a_constructed_record_with_a_primary_key():
    with pytest.raises(ValidationError, match="records must name a saved row"):
        DecisionRequest(
            kind="review", records=(get_user_model()(pk=41),),
            proposal={"alternatives": [{"key": "keep", "label": "Keep", "outcome": "done"}]},
        )
