"""Delete retired integrate VCS model state after every present consumer moves."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.db import migrations
from django.db.migrations.state import ProjectState

MOVED = ("vcsbridge", "repository", "source", "template")


def _target(remote: object) -> tuple[str, str] | None:
    if isinstance(remote, tuple) and len(remote) == 2:
        return str(remote[0]).lower(), str(remote[1]).lower()
    if isinstance(remote, str) and "." in remote:
        app_label, model_name = remote.lower().split(".", 1)
        return app_label, model_name
    return None


def applies(project_state: ProjectState) -> bool:
    """Apply only when target state is complete and no external field targets old VCS state."""

    old = {("integrate", name) for name in MOVED}
    new = {("integrate_vcs", name) for name in MOVED}
    models = set(project_state.models)
    if old <= models and not new & models:
        return False
    if not old & models:
        if new <= models or not new & models:
            return False
        raise ImproperlyConfigured("angee.integrate_vcs:delete_integrate_vcs_state found partial target state")
    if not old <= models or not new <= models:
        raise ImproperlyConfigured("angee.integrate_vcs:delete_integrate_vcs_state found partial VCS adoption")
    for key, model in project_state.models.items():
        if key in old:
            continue
        for field in model.fields.values():
            remote = getattr(field, "remote_field", None)
            if remote is not None and _target(remote.model) in old:
                return False
    return True


class Migration(migrations.Migration):
    """Remove only old model state; adopted models retain the physical tables."""

    dependencies = [("integrate", "__latest__"), ("integrate_vcs", "__latest__")]
    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name="Template"),
                migrations.DeleteModel(name="Source"),
                migrations.DeleteModel(name="Repository"),
                migrations.DeleteModel(name="VcsBridge"),
            ]
        )
    ]
