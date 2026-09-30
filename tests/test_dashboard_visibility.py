"""Dashboard visibility policies use the composed model registry and declared scopes."""

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError

from angee.dashboards.models import widget_visibility_answers
from tests import projects_models  # noqa: F401 -- registers the concrete work.Queue and projects.Task


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
