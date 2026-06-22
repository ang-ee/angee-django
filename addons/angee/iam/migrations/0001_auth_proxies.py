"""Migration state for IAM's proxy over Django auth groups."""

from __future__ import annotations

from django.db import migrations


class Migration(migrations.Migration):
    """Declare proxy model state without creating IAM auth tables."""

    initial = True

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.CreateModel(
            name="Group",
            fields=[],
            options={
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("auth.group",),
        ),
    ]
