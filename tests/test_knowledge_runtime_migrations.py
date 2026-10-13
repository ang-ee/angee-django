"""Guards of knowledge's declared history migrations against live schema shapes."""

from __future__ import annotations

from django.db import models
from django.db.migrations.state import ModelState, ProjectState

from angee.knowledge.runtime_migrations import history_changeset

HISTORY_FIELDS = [
    ("history_id", models.AutoField(primary_key=True)),
    ("history_date", models.DateTimeField()),
    ("history_type", models.CharField(max_length=1)),
    ("history_change_reason", models.TextField(null=True)),
    ("history_user", models.IntegerField(null=True)),
]


def _state(*, markdown_history: bool, changeset: bool) -> ProjectState:
    state = ProjectState()
    state.add_model(ModelState("knowledge", "Page", [("id", models.AutoField(primary_key=True))]))
    state.add_model(
        ModelState(
            "knowledge",
            "MarkdownPage",
            [
                (
                    "page_ptr",
                    models.OneToOneField(
                        "knowledge.Page", parent_link=True, primary_key=True, on_delete=models.CASCADE
                    ),
                ),
            ],
            bases=("knowledge.page",),
        )
    )
    names = ["historicalpage", "historicalvault", *(["historicalmarkdownpage"] if markdown_history else [])]
    for name in names:
        fields = [*HISTORY_FIELDS]
        if changeset:
            fields.append(("changeset", history_changeset.Migration.operations[0].field.clone()))
        state.add_model(
            ModelState("knowledge", name.replace("historical", "Historical").title().replace("page", "Page"), fields)
        )
    return state


def test_changeset_guard_waits_for_the_pending_markdown_history_on_the_live_child_shape() -> None:
    """Live databases already hold MarkdownPage as a Page child; the markdown history is still pending."""
    assert not history_changeset.applies(_state(markdown_history=False, changeset=False))
    assert history_changeset.applies(_state(markdown_history=True, changeset=False))
    assert not history_changeset.applies(_state(markdown_history=True, changeset=True))
