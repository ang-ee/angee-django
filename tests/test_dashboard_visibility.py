"""Dashboard visibility policies use the composed model registry and declared scopes."""

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError

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

    class ReadableQueues:
        def filter(self, **lookup):
            assert lookup == {"slug__in": ["incoming"]}
            return self

        def values_list(self, key, flat):
            assert (key, flat) == ("slug", True)
            return ["incoming"]

    def readable(model, actor):
        seen.append((model, actor))
        return ReadableQueues()

    monkeypatch.setattr("angee.dashboards.models.read_scoped_queryset", readable)
    actor = object()
    assert widget_visibility_answers(
        [{"resource": "projects.Task", "key": "queue__slug", "value": "incoming"}], actor
    ) == [True]
    assert seen == [(projects_models.Queue, actor)]


def test_declared_task_queue_scope_validates_in_a_widget_snapshot(monkeypatch):
    monkeypatch.setattr(
        "angee.dashboards.models.read_scoped_queryset", lambda model, _actor: model._base_manager.none(),
    )
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
