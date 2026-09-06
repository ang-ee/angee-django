"""Point agent VCS foreign-key state at the extracted integrate_vcs owner."""

from __future__ import annotations

import django.db.models.deletion
from django.core.exceptions import ImproperlyConfigured
from django.db import migrations, models
from django.db.migrations.state import ProjectState


def _target(project_state: ProjectState, model_name: str, field_name: str) -> str:
    remote = project_state.models["agents", model_name].fields[field_name].remote_field.model
    if isinstance(remote, tuple):
        return ".".join(remote)
    return str(remote).lower()


def applies(project_state: ProjectState) -> bool:
    """Apply only after target adoption and only to the exact old consumer state."""

    if ("agents", "skill") not in project_state.models:
        return False
    observed = {
        _target(project_state, "agent", "workspace_template"),
        _target(project_state, "skill", "source"),
    }
    if observed == {"integrate.template", "integrate.source"}:
        return ("integrate_vcs", "source") in project_state.models
    if observed == {"integrate_vcs.template", "integrate_vcs.source"}:
        return False
    raise ImproperlyConfigured(f"angee.agents:vcs_models_cutover found partial VCS targets: {sorted(observed)}")


class Migration(migrations.Migration):
    """Adopt unchanged physical foreign keys into the new model state."""

    dependencies = [("integrate_vcs", "__latest__")]
    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="agent",
                    name="workspace_template",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="integrate_vcs.template",
                    ),
                ),
                migrations.AlterField(
                    model_name="skill",
                    name="source",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="skills",
                        to="integrate_vcs.source",
                    ),
                ),
            ]
        )
    ]
