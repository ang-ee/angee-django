"""Tests for the REBAC-aware aggregate seam."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Group
from django.core.exceptions import ImproperlyConfigured

import angee.graphql.aggregates as aggregates
from angee.graphql.aggregates import rebac_aggregate_builder


def test_rebac_aggregate_builder_rejects_gated_group_by_axis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A field-gated read column may not be an aggregate group-by axis.

    Group-by axes become dict-row bucket keys that field-read redaction cannot
    touch, so exposing a gated column would leak owner-only values. The builder
    refuses it at construction time rather than relying on author discipline.
    """

    monkeypatch.setattr(aggregates, "gated_read_fields", lambda model: {"secret"})

    with pytest.raises(ImproperlyConfigured, match="field-gated"):
        rebac_aggregate_builder(model=Group, group_by_fields=["name", "secret"])
