"""Separate immutable workflow admission authority from nullable audit attribution."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db import migrations, models
from django.db.migrations.state import ProjectState
from rebac._id import resource_id_attr, subject_id_attr, subject_relation, type_with_prefix
from rebac.conf import app_settings
from rebac.resources import model_resource_type
from rebac.types import SubjectRef


def applies(project_state: ProjectState) -> bool:
    """Add the retained subject only to an existing pre-transition run model."""

    run = project_state.models.get(("workflows", "workflowrun"))
    if run is None:
        return False
    present = "admitted_actor_ref" in run.fields
    created_by_present = "created_by" in run.fields
    if present:
        return False
    if not created_by_present:
        raise ImproperlyConfigured(
            "angee.workflows:workflow_admitted_actor found a run without audit attribution"
        )
    return True


def backfill_known_admission_actors(apps, schema_editor) -> None:
    """Retain only actor identities proven by existing run attribution rows."""

    run = apps.get_model("workflows", "WorkflowRun")
    alias = schema_editor.connection.alias
    rows = models.QuerySet(model=run, using=alias).exclude(
        created_by_id__isnull=True,
    ).select_related("created_by").order_by("pk")
    for row in rows.iterator():
        models.QuerySet(model=run, using=alias).filter(pk=row.pk, admitted_actor_ref="").update(
            admitted_actor_ref=str(_historical_user_subject_ref(row.created_by))
        )


def _historical_user_subject_ref(actor) -> SubjectRef:
    """Apply the active user identity contract to a StateApps user instance.

    Django migration models are distinct classes and deliberately omit the
    custom REBAC Meta options captured by the runtime model metaclass.  Resolve
    the type and id attribute from the active user owner, then read only that
    declared identity field from the historical row.
    """

    user_model = get_user_model()
    resource_type = model_resource_type(user_model)
    if resource_type:
        return SubjectRef.of(
            resource_type,
            str(getattr(actor, resource_id_attr(user_model))),
            subject_relation(user_model),
        )
    return SubjectRef.of(
        type_with_prefix(app_settings.REBAC_USER_TYPE),
        str(getattr(actor, subject_id_attr(user_model))),
    )


class Migration(migrations.Migration):
    """Retain actor identity while allowing the declared audit FK to become null."""

    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.AddField(
            model_name="workflowrun",
            name="admitted_actor_ref",
            field=models.CharField(blank=True, default="", editable=False, max_length=255),
        ),
        migrations.RunPython(backfill_known_admission_actors, migrations.RunPython.noop),
    ]
