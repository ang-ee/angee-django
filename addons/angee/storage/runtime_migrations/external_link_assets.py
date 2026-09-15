"""Add byte-free external links to the existing attachment/claim graph."""

from __future__ import annotations

import django.db.models.deletion
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.validators import URLValidator
from django.db import migrations, models
from django.db.migrations.state import ProjectState


def applies(project_state: ProjectState) -> bool:
    """Apply only to the complete legacy File-only attachment state."""

    attachment = project_state.models.get(("storage", "fileattachment"))
    link = project_state.models.get(("storage", "externallink"))
    if attachment is None:
        if link is not None:
            raise ImproperlyConfigured(
                "angee.storage:external_link_assets found a link without attachments"
            )
        return False
    external_field = attachment.fields.get("external_link")
    file_field = attachment.fields.get("file")
    constraint_names = {
        value.name for value in attachment.options.get("constraints", ())
    }
    complete = (
        link is not None
        and link.options.get("base_manager_name") == "objects"
        and external_field is not None
        and file_field is not None
        and bool(getattr(file_field, "null", False))
        and {
            "ck_storage_attachment_exactly_one_asset",
            "uq_storage_external_link_attachment_edge",
        }
        <= constraint_names
    )
    if complete:
        return False
    legacy = (
        link is None
        and external_field is None
        and file_field is not None
        and not bool(getattr(file_field, "null", False))
        and not {
            "ck_storage_attachment_exactly_one_asset",
            "uq_storage_external_link_attachment_edge",
        }
        & constraint_names
    )
    if legacy:
        return True
    raise ImproperlyConfigured(
        "angee.storage:external_link_assets found a partial asset transition"
    )


class Migration(migrations.Migration):
    """Preserve File edges while adding one exact alternate link asset."""

    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.CreateModel(
            name="ExternalLink",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                (
                    "url",
                    models.URLField(
                        max_length=2048,
                        validators=(URLValidator(schemes=("http", "https")),),
                    ),
                ),
                ("title", models.CharField(blank=True, default="", max_length=512)),
                ("metadata", models.JSONField(blank=True, default=dict)),
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
            ],
            options={
                "ordering": ("-updated_at", "title", "sqid"),
                "base_manager_name": "objects",
                "abstract": False,
            },
        ),
        migrations.AddField(
            model_name="fileattachment",
            name="external_link",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="attachments",
                to="storage.externallink",
            ),
        ),
        migrations.AlterField(
            model_name="fileattachment",
            name="file",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="attachments",
                to="storage.file",
            ),
        ),
        migrations.AddConstraint(
            model_name="fileattachment",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(file__isnull=False, external_link__isnull=True)
                    | models.Q(file__isnull=True, external_link__isnull=False)
                ),
                name="ck_storage_attachment_exactly_one_asset",
            ),
        ),
        migrations.AddConstraint(
            model_name="fileattachment",
            constraint=models.UniqueConstraint(
                fields=("external_link", "content_type", "object_id"),
                name="uq_storage_external_link_attachment_edge",
            ),
        ),
    ]
