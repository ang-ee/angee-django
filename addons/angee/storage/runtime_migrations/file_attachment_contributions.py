"""Retain direct and independently removable contributor attachment membership."""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import migrations, models
from django.db.migrations.state import ProjectState


def _field_shape(field: models.Field) -> tuple[object, ...]:
    """Return the declaration-owned field shape, excluding its bound name."""

    _name, path, args, kwargs = field.deconstruct()
    return path, tuple(args), tuple(sorted(kwargs.items()))


def applies(project_state: ProjectState) -> bool:
    """Apply only across the complete legacy-to-contributor transition."""

    attachment = project_state.models.get(("storage", "fileattachment"))
    claim = project_state.models.get(("storage", "fileattachmentclaim"))
    if attachment is None:
        if claim is not None:
            raise ImproperlyConfigured(
                "angee.storage:file_attachment_contributions found a claim without its edge"
            )
        return False
    membership = attachment.fields.get("direct_membership")
    if membership is None and claim is None:
        return True
    expected_membership = Migration.operations[0].field
    expected_claim = dict(Migration.operations[1].fields)
    membership_valid = bool(
        membership is not None
        and _field_shape(membership) == _field_shape(expected_membership)
    )
    claim_fields = {} if claim is None else claim.fields
    fields_valid = set(claim_fields) == set(expected_claim) and all(
        _field_shape(claim_fields[name]) == _field_shape(field)
        for name, field in expected_claim.items()
    )
    expected_constraints = tuple(Migration.operations[1].options["constraints"])
    expected_indexes = tuple(Migration.operations[1].options["indexes"])
    if (
        membership_valid
        and fields_valid
        and claim is not None
        and tuple(claim.options.get("ordering", ()))
        == tuple(Migration.operations[1].options["ordering"])
        and tuple(
            value.deconstruct() for value in claim.options.get("constraints", ())
        )
        == tuple(value.deconstruct() for value in expected_constraints)
        and tuple(value.deconstruct() for value in claim.options.get("indexes", ()))
        == tuple(value.deconstruct() for value in expected_indexes)
    ):
        return False
    raise ImproperlyConfigured(
        "angee.storage:file_attachment_contributions found a partial membership transition"
    )


class Migration(migrations.Migration):
    """Backfill every existing edge as direct before accepting source claims."""

    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.AddField(
            model_name="fileattachment",
            name="direct_membership",
            field=models.BooleanField(default=True, editable=False),
        ),
        migrations.CreateModel(
            name="FileAttachmentClaim",
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
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, db_index=True),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, db_index=True),
                ),
                ("contributor_object_id", models.CharField(max_length=255)),
                (
                    "attachment",
                    models.ForeignKey(
                        on_delete=models.PROTECT,
                        related_name="contributions",
                        to="storage.fileattachment",
                    ),
                ),
                (
                    "contributor_content_type",
                    models.ForeignKey(
                        on_delete=models.PROTECT,
                        related_name="+",
                        to="contenttypes.contenttype",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": (
                    "attachment",
                    "contributor_content_type",
                    "contributor_object_id",
                ),
                "constraints": (
                    models.UniqueConstraint(
                        fields=(
                            "attachment",
                            "contributor_content_type",
                            "contributor_object_id",
                        ),
                        name="uq_storage_file_attachment_contributor",
                    ),
                ),
                "indexes": (
                    models.Index(
                        fields=(
                            "contributor_content_type",
                            "contributor_object_id",
                        ),
                        name="storage_fil_contrib_idx",
                    ),
                ),
            },
        ),
    ]
