"""Dashboard visibility policies use the composed model registry and declared scopes."""

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from rebac import system_context

from angee.dashboards.models import canonical_dashboard_snapshot, widget_visibility_answers
from angee.projects.testing import (
    models as projects_models,  # noqa: F401 -- registers the concrete work.Queue and projects.Task
)
from tests.conftest import create_user


@pytest.mark.parametrize("key", ["slug", "queue.slug"])
def test_direct_or_dotted_undeclared_scope_is_rejected(key):
    assert apps.get_model("work.Queue") is projects_models.Queue
    resource = "work.Queue" if key == "slug" else "projects.Task"
    with pytest.raises(ValidationError, match="Use a declared container scope key"):
        widget_visibility_answers([{"resource": resource, "key": key, "value": "incoming"}], actor=None)


@pytest.mark.django_db
def test_declared_related_scope_resolves_real_queue_field():
    """``queue__slug`` reads the real Queue rows, scoped to the asking actor."""

    assert apps.get_model("projects.Task") is projects_models.Task
    assert apps.get_model("work.Queue") is projects_models.Queue
    owner, outsider = create_user("visibility-queue-owner"), create_user("visibility-outsider")
    with system_context(reason="test dashboard visibility queue"):
        projects_models.Queue.objects.create(name="Incoming", slug="incoming", key="INC", owner=owner)
    rule = [{"resource": "projects.Task", "key": "queue__slug", "value": "incoming"}]

    assert widget_visibility_answers(rule, owner) == [True]
    assert widget_visibility_answers(rule, outsider) == [False]
    assert widget_visibility_answers([{**rule[0], "value": "absent"}], owner) == [False]


@pytest.mark.django_db
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


def test_packing_refuses_a_layout_whose_canonical_bottom_exceeds_the_limit():
    def widget(index):
        return {"schemaVersion": 1, "id": str(index), "kind": "value", "kindVersion": 1,
                "title": "Metric", "data": {"shape": "value", "source": {"resource": "projects.Task"}},
                "options": {}, "x": 0, "y": 0, "w": 12, "h": 100, "isArchived": False}

    admitted = {"schemaVersion": 1, "columns": 12, "widgets": [widget(index) for index in range(10)]}
    canonical = canonical_dashboard_snapshot(admitted)
    assert canonical_dashboard_snapshot(canonical) == canonical
    assert canonical["widgets"][-1]["y"] + canonical["widgets"][-1]["h"] == 1000
    with pytest.raises(ValidationError, match="Packed widgets exceed"):
        canonical_dashboard_snapshot({**admitted, "widgets": [*admitted["widgets"], widget(10)]})
