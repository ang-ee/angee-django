"""Tests for the REBAC-aware aggregate seam."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Group
from django.core.exceptions import ImproperlyConfigured

import angee.graphql.access as access
from angee.graphql.aggregates import rebac_aggregate_builder


def test_rebac_aggregate_builder_rejects_gated_group_by_axis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A field-gated read column may not be an aggregate group-by axis.

    Group-by axes become dict-row bucket keys that field-read redaction cannot
    touch, so exposing a gated column would leak owner-only values. The builder
    refuses it at construction time rather than relying on author discipline.
    """

    monkeypatch.setattr(access, "gated_read_fields", lambda model: {"secret"})

    with pytest.raises(ImproperlyConfigured, match="field-gated"):
        rebac_aggregate_builder(model=Group, group_by_fields=["name", "secret"])


def test_rebac_aggregate_builder_gate_walks_relation_leaf_axes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A relation-leaf axis (``content_type__model``) is gate-checked at its leaf.

    A dotted axis is never a field on the base model, so a same-model check is
    blind to it — yet its value comes from the joined model's row, which row scope
    never touches. The gate must walk the relation to the leaf model so a gated
    read reached through a relation is refused exactly as a direct one is.
    """

    from django.contrib.auth.models import Permission
    from django.contrib.contenttypes.models import ContentType

    monkeypatch.setattr(
        access,
        "gated_read_fields",
        lambda model: {"model"} if model is ContentType else set(),
    )

    with pytest.raises(ImproperlyConfigured, match="field-gated"):
        rebac_aggregate_builder(model=Permission, group_by_fields=["content_type__model"])

    # A non-gated leaf on the same relation passes (the leaf, not the path, is gated).
    rebac_aggregate_builder(model=Permission, group_by_fields=["content_type__app_label"])
