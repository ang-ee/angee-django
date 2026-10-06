"""A tone field's values are frontend tone names, rendered by the ``tone`` widget."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from angee.base.fields import StateField, ToneField
from angee.base.stages import Stage, StageTone
from angee.data.field_classification import resource_field_widget
from angee.data.metadata import DataResourceFieldMetadata
from angee.graphql.data.resource_fields import _validate_resource_field


def test_tone_field_declares_the_tone_widget() -> None:
    """The field owns its widget; a plain state field keeps the select."""

    assert resource_field_widget(ToneField(choices_enum=StageTone), "enum") == "tone"
    assert resource_field_widget(StateField(choices_enum=StageTone), "enum") == "select"


def test_stage_tone_is_a_tone_field() -> None:
    """Every stage's tone renders in its own colour, wherever the primitive is composed."""

    assert isinstance(Stage._meta.get_field("tone"), ToneField)


@pytest.mark.parametrize("widget", [None, "select", "tone"])
def test_enum_metadata_admits_value_picking_widgets(widget: str | None) -> None:
    """``tone`` picks one of the enum's own values, like ``select``."""

    _validate_resource_field("demo.Stage", DataResourceFieldMetadata(name="tone", kind="enum", widget=widget))


def test_enum_metadata_still_refuses_other_widgets() -> None:
    with pytest.raises(ImproperlyConfigured, match="for enum fields"):
        _validate_resource_field("demo.Stage", DataResourceFieldMetadata(name="tone", kind="enum", widget="switch"))
