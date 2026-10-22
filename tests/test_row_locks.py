"""Row-lock intent covers every table that stores a multi-table row."""

from __future__ import annotations

import pytest
from django.apps import apps
from django.db import models
from django.test.utils import isolate_apps

from angee.models.base import AngeeModel
from angee.models.query import SystemQuerysetMixin, row_lock_paths


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


@isolate_apps()
def test_row_lock_paths_cover_mti_without_locking_unrelated_selected_rows() -> None:
    class Ancestor(models.Model):
        class Meta:
            app_label = "lock_contract"

    class Related(models.Model):
        class Meta:
            app_label = "lock_contract"

    class Child(Ancestor):
        related = models.ForeignKey(Related, on_delete=models.CASCADE)

        class Meta:
            app_label = "lock_contract"

    class Grandchild(Child):
        class Meta:
            app_label = "lock_contract"

    paths = row_lock_paths(Grandchild)
    assert paths == ("self", "child_ptr", "child_ptr__ancestor_ptr")
    assert row_lock_paths(Grandchild, ("self", "child_ptr")) == paths
    assert row_lock_paths(Grandchild, ("related",)) == ("related",)
    rows = Grandchild._default_manager.select_related("related").select_for_update(of=paths)
    assert rows.query.select_related == {"related": {}}
    assert rows.query.select_for_update_of == paths
    assert all("related" not in path for path in rows.query.select_for_update_of)


@isolate_apps()
def test_delete_lock_preserves_scope_and_excludes_unrelated_selected_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    class Parent(AngeeModel):
        class Meta(AngeeModel.Meta):
            app_label = "lock_contract"

    class Related(models.Model):
        class Meta:
            app_label = "lock_contract"

    class Child(Parent):
        related = models.ForeignKey(Related, on_delete=models.CASCADE)

        class Meta(Parent.Meta):
            app_label = "lock_contract"

    locked = []

    def first(queryset):
        locked.append(queryset.query)
        return None

    monkeypatch.setattr(models.QuerySet, "first", first)
    rows = models.QuerySet(model=Child).select_related("related").filter(related_id=7)
    assert Child(pk=3).lock_for_delete(queryset=rows) is None
    query = locked.pop()
    assert query.select_related == {"related": {}}
    assert query.select_for_update_of == ("self", "parent_ptr")
    assert len(query.where.children) == 2


@isolate_apps()
def test_subject_lock_excludes_nullable_relations_added_by_the_base_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    class JoinedManager(models.Manager):
        def get_queryset(self):
            return super().get_queryset().select_related("related")

    class Parent(SystemQuerysetMixin):
        class Meta:
            app_label = "lock_contract"

    class Related(models.Model):
        class Meta:
            app_label = "lock_contract"

    class Child(Parent):
        related = models.ForeignKey(Related, null=True, on_delete=models.SET_NULL)
        objects = JoinedManager()

        class Meta:
            app_label = "lock_contract"
            base_manager_name = "objects"

    locked = []

    def get(queryset, **kwargs):
        locked.append(queryset.query)
        return queryset.model(**kwargs)

    monkeypatch.setattr(models.QuerySet, "get", get)
    row = Child(pk=3)
    assert row.lock_subject().pk == row.pk
    query = locked.pop()
    assert query.select_related == {"related": {}}
    assert query.select_for_update_of == ("self", "parent_ptr")
