"""Point platform VCS provenance state at the extracted integrate_vcs owner."""

from __future__ import annotations

import django.db.models.deletion
from django.core.exceptions import ImproperlyConfigured
from django.db import migrations, models
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    """Apply only after target adoption and only to the exact old consumer state."""

    model = project_state.models.get(("platform", "addon"))
    if model is None:
        return False
    remote = model.fields["vcs_source"].remote_field.model
    target = ".".join(remote) if isinstance(remote, tuple) else str(remote).lower()
    if target == "integrate.source":
        return ("integrate_vcs", "source") in project_state.models
    if target == "integrate_vcs.source":
        return False
    raise ImproperlyConfigured(f"angee.platform_integrate_vcs:vcs_models_cutover found target {target!r}")


class Migration(migrations.Migration):
    """Adopt the unchanged physical foreign key into the new model state."""

    dependencies = [("integrate_vcs", "__latest__")]
    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="addon",
                    name="vcs_source",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="catalog_addons",
                        to="integrate_vcs.source",
                    ),
                )
            ]
        )
    ]
