"""Dashboard visibility policies use the composed model registry and declared scopes."""

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from rebac import anonymous_actor

from angee.dashboards.models import canonical_dashboard_snapshot, widget_visibility_answers
from angee.projects.testing import (
    models as projects_models,  # noqa: F401 -- registers the concrete work.Queue and projects.Task
)


@pytest.mark.parametrize("key", ["slug", "queue.slug"])
def test_direct_or_dotted_undeclared_scope_is_rejected(key):
    assert apps.get_model("work.Queue") is projects_models.Queue
    resource = "work.Queue" if key == "slug" else "projects.Task"
    with pytest.raises(ValidationError, match="Use a declared container scope key"):
        widget_visibility_answers([{"resource": resource, "key": key, "value": "incoming"}], actor=None)


def test_declared_related_scope_resolves_real_queue_field(monkeypatch):
    assert apps.get_model("projects.Task") is projects_models.Task
    assert apps.get_model("work.Queue") is projects_models.Queue
    seen = []

    queryset_class = type(projects_models.Queue._default_manager.get_queryset())
    native_with_actor = queryset_class.with_actor

    def readable(queryset, actor):
        seen.append((queryset.model, actor))
        return native_with_actor(queryset, actor)

    def slugs(queryset, key, flat):
        assert (key, flat) == ("slug", True)
        assert queryset.model is projects_models.Queue
        assert any(
            lookup.lookup_name == "in" and lookup.lhs.target.name == "slug" and lookup.rhs == ["incoming"]
            for lookup in queryset.query.where.children
        )
        return ["incoming"]

    monkeypatch.setattr(queryset_class, "with_actor", readable)
    monkeypatch.setattr(queryset_class, "values_list", slugs)
    actor = anonymous_actor()
    assert widget_visibility_answers(
        [{"resource": "projects.Task", "key": "queue__slug", "value": "incoming"}], actor
    ) == [True]
    assert seen == [(projects_models.Queue, actor)]


def test_declared_task_queue_scope_validates_in_a_widget_snapshot():
    snapshot = canonical_dashboard_snapshot({
        "schemaVersion": 1,
        "columns": 12,
        "widgets": [{
            "schemaVersion": 1,
            "id": "inbox",
            "kind": "table",
            "kindVersion": 1,
            "title": "Inbox",
            "visibility": {"resource": "projects.Task", "key": "queue__slug", "value": "incoming"},
            "data": {"shape": "rows", "source": {"resource": "projects.Task"}},
            "options": {},
            "x": 0, "y": 0, "w": 6, "h": 4, "isArchived": False,
        }],
    })
    assert snapshot["widgets"][0]["visibility"] == {
        "resource": "projects.Task", "key": "queue__slug", "value": "incoming",
    }
