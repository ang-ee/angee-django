"""Row-lock intent covers every table that stores a multi-table row."""

from __future__ import annotations

import pytest
from django.apps import apps


def _multi_table_models():
    return sorted(
        (
            model
            for model in apps.get_models()
            if model._meta.get_parent_list() and callable(getattr(model, "system_queryset", None))
        ),
        key=lambda model: model._meta.label,
    )


def test_self_lock_names_every_parent_link() -> None:
    models = _multi_table_models()
    if not models:
        pytest.skip("These test settings compose no multi-table model.")
    for model in models:
        of = model.system_queryset(lock=("self",)).query.select_for_update_of
        parents = {
            ancestor
            for ancestor in model._meta.get_parent_list()
            if model._meta.get_ancestor_link(ancestor) is not None
        }
        assert "self" in of
        assert len(of) == 1 + len(parents), (model._meta.label, of)


def test_explicit_relation_lock_is_left_unchanged() -> None:
    model = next(model for model in apps.get_models() if callable(getattr(model, "system_queryset", None)))
    of = model.system_queryset(lock=("owner",)).query.select_for_update_of
    assert of == ("owner",)
