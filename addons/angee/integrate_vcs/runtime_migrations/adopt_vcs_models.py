"""Adopt the four VCS tables into the integrate_vcs model state."""

from __future__ import annotations

import django.db.models.deletion
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import migrations, models
from django.db.migrations.state import ProjectState

import angee.base.fields
import angee.base.impl

MOVED = ("vcsbridge", "repository", "source", "template")
TABLES = ("integrate_vcsbridge", "integrate_repository", "integrate_source", "integrate_template")


def applies(project_state: ProjectState) -> bool:
    old = {("integrate", name) for name in MOVED}
    new = {("integrate_vcs", name) for name in MOVED}
    present = set(project_state.models)
    if old <= present and not new & present:
        return True
    if new <= present and not old & present:
        return False
    if not old & present and not new & present:
        return False
    raise ImproperlyConfigured("angee.integrate_vcs:adopt_vcs_models found partial old or target state")


def ensure_vcs_tables(apps, schema_editor) -> None:
    existing = set(schema_editor.connection.introspection.table_names())
    present = set(TABLES) & existing
    if present and present != set(TABLES):
        raise ImproperlyConfigured(
            "angee.integrate_vcs:adopt_vcs_models found partial physical tables: " + repr(sorted(present))
        )
    if present:
        return
    for name in MOVED:
        schema_editor.create_model(apps.get_model("integrate", name))


ADOPTED_STATE = [
    migrations.CreateModel(
        name="VcsBridge",
        fields=[
            (
                "backend_class",
                angee.base.impl.ImplClassField(
                    default="local", max_length=100, registry_setting="ANGEE_VCS_BACKEND_CLASSES"
                ),
            ),
            ("config", models.JSONField(blank=True, default=dict)),
            ("cursor", models.JSONField(blank=True, default=dict)),
            (
                "integration_ptr",
                models.OneToOneField(
                    auto_created=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    parent_link=True,
                    primary_key=True,
                    serialize=False,
                    to="integrate.integration",
                ),
            ),
            ("last_sync_completed_at", models.DateTimeField(blank=True, null=True)),
            ("last_sync_items", models.PositiveIntegerField(default=0)),
            ("last_sync_started_at", models.DateTimeField(blank=True, null=True)),
            ("last_sync_status", models.CharField(blank=True, max_length=64)),
            ("last_sync_summary", models.JSONField(blank=True, default=dict)),
            ("next_subscription_refresh_at", models.DateTimeField(blank=True, null=True)),
            ("next_sync_at", models.DateTimeField(blank=True, db_index=True, null=True)),
            ("poll_interval", models.PositiveIntegerField(default=300)),
            ("subscription_state", models.JSONField(blank=True, default=dict)),
            ("sync_error", models.TextField(blank=True, default="")),
            ("sync_progress", models.JSONField(blank=True, default=dict)),
            (
                "sync_stage",
                models.CharField(
                    choices=[
                        ("idle", "Idle"),
                        ("queued", "Queued"),
                        ("discovering", "Discovering"),
                        ("syncing", "Syncing"),
                        ("completed", "Completed"),
                        ("failed", "Failed"),
                    ],
                    db_index=True,
                    default="idle",
                    max_length=32,
                ),
            ),
            ("webhook_secret", angee.base.fields.EncryptedField(blank=True)),
        ],
        options={
            "abstract": False,
            "ordering": ("-updated_at",),
            "db_table": "integrate_vcsbridge",
        },
        bases=("integrate.integration", models.Model),
    ),
    migrations.CreateModel(
        name="Repository",
        fields=[
            ("archived", models.BooleanField(default=False)),
            ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
            ("default_branch", models.CharField(default="main", max_length=255)),
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("name", models.CharField(max_length=255)),
            ("org", models.CharField(db_index=True, max_length=255)),
            ("remote", models.CharField(max_length=512)),
            ("remote_id", models.CharField(blank=True, max_length=128)),
            ("ssh_remote", models.CharField(blank=True, max_length=255)),
            ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
            (
                "visibility",
                angee.base.fields.StateField(
                    choices=[("public", "Public"), ("private", "Private"), ("internal", "Internal")],
                    db_index=True,
                    default="private",
                    max_length=8,
                ),
            ),
            ("web_url", models.URLField(blank=True)),
            (
                "created_by",
                models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to=settings.AUTH_USER_MODEL,
                ),
            ),
            (
                "updated_by",
                models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to=settings.AUTH_USER_MODEL,
                ),
            ),
            (
                "vcs_bridge",
                models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="repositories",
                    to="integrate_vcs.vcsbridge",
                ),
            ),
        ],
        options={
            "abstract": False,
            "ordering": ("org", "name"),
            "db_table": "integrate_repository",
        },
    ),
    migrations.CreateModel(
        name="Source",
        fields=[
            ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("kind", models.CharField(max_length=64)),
            ("last_synced_at", models.DateTimeField(blank=True, null=True)),
            ("path", models.CharField(blank=True, max_length=1024)),
            ("ref", models.CharField(blank=True, max_length=255)),
            ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
            (
                "created_by",
                models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to=settings.AUTH_USER_MODEL,
                ),
            ),
            (
                "repository",
                models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name="sources", to="integrate_vcs.repository"
                ),
            ),
            (
                "updated_by",
                models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to=settings.AUTH_USER_MODEL,
                ),
            ),
        ],
        options={
            "abstract": False,
            "ordering": ("kind", "path"),
            "db_table": "integrate_source",
        },
    ),
    migrations.CreateModel(
        name="Template",
        fields=[
            ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("inputs", models.JSONField(blank=True, default=list)),
            ("kind", models.CharField(blank=True, max_length=64)),
            ("name", models.CharField(blank=True, max_length=255)),
            ("path", models.CharField(blank=True, max_length=1024)),
            ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
            (
                "created_by",
                models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to=settings.AUTH_USER_MODEL,
                ),
            ),
            (
                "source",
                models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name="templates", to="integrate_vcs.source"
                ),
            ),
            (
                "updated_by",
                models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to=settings.AUTH_USER_MODEL,
                ),
            ),
        ],
        options={
            "abstract": False,
            "ordering": ("kind", "name"),
            "db_table": "integrate_template",
            "constraints": [models.UniqueConstraint(fields=("source", "path"), name="uniq_integrate_template_path")],
        },
    ),
    migrations.AddConstraint(
        model_name="repository",
        constraint=models.UniqueConstraint(fields=("vcs_bridge", "name"), name="uniq_integrate_repository_name"),
    ),
]


class Migration(migrations.Migration):
    """Create absent physical tables, then adopt their state without renaming them."""

    dependencies = [("integrate", "__latest__"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunPython(ensure_vcs_tables)],
            state_operations=ADOPTED_STATE,
        )
    ]
