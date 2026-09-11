"""Allow many source conversations while retaining one chatter thread per target."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.db import migrations, models
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    model = project_state.models.get(("messaging", "threadattachment"))
    if model is None:
        return False
    names = {getattr(item, "name", "") for item in model.options.get("constraints", ())}
    legacy = "uq_thread_attachment_target_role" in names
    current = {
        "uq_thread_attachment_target_chatter",
        "uq_thread_attachment_source_edge",
    }.intersection(names)
    if legacy and not current:
        return True
    if not legacy and len(current) == 2:
        return False
    raise ImproperlyConfigured(
        "angee.messaging:thread_attachment_source_edges found a partial constraint transition"
    )


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.RemoveConstraint(
            model_name="threadattachment",
            name="uq_thread_attachment_target_role",
        ),
        migrations.AddConstraint(
            model_name="threadattachment",
            constraint=models.UniqueConstraint(
                fields=("content_type", "object_id"),
                condition=models.Q(role="chatter"),
                name="uq_thread_attachment_target_chatter",
            ),
        ),
        migrations.AddConstraint(
            model_name="threadattachment",
            constraint=models.UniqueConstraint(
                fields=("thread", "content_type", "object_id", "role"),
                condition=models.Q(role="source"),
                name="uq_thread_attachment_source_edge",
            ),
        ),
    ]
