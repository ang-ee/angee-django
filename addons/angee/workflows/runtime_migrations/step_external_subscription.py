"""Retain complete attempt-owned target sets for durable external waits."""

from __future__ import annotations

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    """Create the subscription relation only when the workflows runtime exists."""

    attempt = project_state.models.get(("workflows", "stepattempt"))
    subscription = project_state.models.get(("workflows", "stepexternalsubscription"))
    return attempt is not None and subscription is None


class Migration(migrations.Migration):
    """Add a separate target set without repurposing provider or child bindings."""

    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.CreateModel(
            name="StepExternalSubscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("target_object_id", models.PositiveBigIntegerField(editable=False)),
                ("attempt", models.ForeignKey(
                    editable=False,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="external_subscriptions",
                    to="workflows.stepattempt",
                )),
                ("created_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+", to=settings.AUTH_USER_MODEL,
                )),
                ("target_content_type", models.ForeignKey(
                    editable=False,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="+", to="contenttypes.contenttype",
                )),
                ("updated_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+", to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "ordering": ("attempt_id", "target_content_type_id", "target_object_id"),
                "abstract": False,
                "indexes": [models.Index(
                    fields=("target_content_type", "target_object_id"),
                    name="idx_wes_target",
                )],
                "constraints": [models.UniqueConstraint(
                    fields=("attempt", "target_content_type", "target_object_id"),
                    name="uniq_wes_attempt_target",
                )],
            },
        ),
    ]
