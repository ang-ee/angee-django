"""Record the queued state in the outbound message delivery lifecycle."""

from __future__ import annotations

from angee.base.fields import StateField
from django.core.exceptions import ImproperlyConfigured
from django.db import migrations
from django.db.migrations.state import ProjectState

LEGACY_CHOICES = (
    ("draft", "Draft"),
    ("sent", "Sent"),
    ("synced", "Synced"),
    ("edited", "Edited"),
    ("hidden", "Hidden"),
    ("removed", "Removed"),
    ("failed", "Failed"),
)
CURRENT_CHOICES = (LEGACY_CHOICES[0], ("queued", "Queued"), *LEGACY_CHOICES[1:])
LEGACY_VALUES = frozenset(value for value, _label in LEGACY_CHOICES)
CURRENT_VALUES = frozenset(value for value, _label in CURRENT_CHOICES)


def applies(project_state: ProjectState) -> bool:
    """Return whether Message.status still has exactly the pre-queue choices."""

    model = project_state.models.get(("messaging", "message"))
    if model is None:
        return False
    field = model.fields.get("status")
    if field is None:
        raise ImproperlyConfigured("angee.messaging:alter_message_status found Message without status")
    values = frozenset(value for value, _label in field.choices)
    if CURRENT_VALUES <= values:
        return False
    if values == LEGACY_VALUES:
        return True
    raise ImproperlyConfigured(
        f"angee.messaging:alter_message_status found a partial Message status transition: {sorted(values)}"
    )


class Migration(migrations.Migration):
    """Record queued in Message.status without changing the column width."""

    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.AlterField(
            model_name="message",
            name="status",
            field=StateField(
                choices=CURRENT_CHOICES,
                db_index=True,
                default="synced",
                max_length=7,
            ),
        ),
    ]
