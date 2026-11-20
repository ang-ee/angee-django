"""Historical import required by projects' materialized messaging migrations."""

from __future__ import annotations

from typing import Any


def default_webform_schema() -> dict[str, Any]:
    """Return the default of the removed ``Channel.form_schema`` column, which those migrations name."""

    return {"type": "object", "properties": {}}
