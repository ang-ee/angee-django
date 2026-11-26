"""A compatibility name for the migrations projects retain, and nothing else.

A channel held a public form in columns of its own until web forms took forms
over. ``Channel.form_schema`` named :func:`default_webform_schema` as its
default, so every initial messaging migration authored while those columns
existed imports it by this dotted path, at every ``migrate``. It stays, with
what it returns, for as long as such a migration can be in a project's history.
"""

from __future__ import annotations

from typing import Any


def default_webform_schema() -> dict[str, Any]:
    """Return the default of the removed ``Channel.form_schema`` column, which retained migrations name."""

    return {"type": "object", "properties": {}}
