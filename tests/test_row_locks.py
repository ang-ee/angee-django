"""Row-lock intent covers every table that stores a multi-table row."""

from __future__ import annotations

import pytest
from django.apps import apps

from angee.base.scoping import lock_if_supported


def _multi_table_models():
    return sorted(
        (model for model in apps.get_models() if model._meta.get_parent_list()),
        key=lambda model: model._meta.label,
    )


def test_self_lock_names_every_parent_link() -> None:
    models = _multi_table_models()
    if not models:
        pytest.skip("These test settings compose no multi-table model.")
    for model in models:
        of = lock_if_supported(model._base_manager.all()).query.select_for_update_of
        parents = {
            ancestor
            for ancestor in model._meta.get_parent_list()
            if model._meta.get_ancestor_link(ancestor) is not None
        }
        assert "self" in of
        assert len(of) == 1 + len(parents), (model._meta.label, of)


def test_explicit_relation_lock_is_left_unchanged() -> None:
    model = next(iter(apps.get_models()))
    of = lock_if_supported(model._base_manager.all(), of=("owner",)).query.select_for_update_of
    assert of == ("owner",)
