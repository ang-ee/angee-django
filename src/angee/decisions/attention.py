"""Contribute decision attention through GraphQL's generic resource hook."""

from typing import Any

from django.apps import apps
from django.db import models


def resource_filters(model: type[models.Model]) -> dict[str, Any]:
    """Every model resource can filter attention, with no model declaration."""
    return {"has_open_decisions": apps.get_model("decisions", "Decision").objects.attention_expression}
