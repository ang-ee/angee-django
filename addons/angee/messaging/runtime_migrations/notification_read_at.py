"""Give existing notification rows an independent, initially unset acknowledgement."""

from django.db import migrations, models
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    notification = project_state.models.get(("messaging", "threadnotification"))
    return notification is not None and "read_at" not in notification.fields


class Migration(migrations.Migration):
    operations = [
        migrations.AddField(
            "threadnotification",
            "read_at",
            models.DateTimeField(null=True, blank=True),
        ),
    ]
