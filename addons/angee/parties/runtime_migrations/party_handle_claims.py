"""Give each source its own claim beneath a party-handle link.

Every existing link becomes one claim of its recorded source, confidence and
metadata; the link's metadata then moves off the link, which the claims now hold.
Reversing restores the link's metadata from its claims and drops the table.
"""

import django.core.validators
import django.db.models.deletion
import django.db.models.manager
from django.conf import settings
from django.db import migrations, models

import angee.base.fields
import angee.base.mixins

_BATCH = 2000


def applies(state):
    """Select a link that still carries its metadata and has no claims yet."""

    link = state.models.get(("parties", "partyhandle"))
    if link is None:
        return False
    has_claims = ("parties", "partyhandleclaim") in state.models
    if "metadata" in link.fields and not has_claims:
        return True
    if "metadata" in link.fields or not has_claims:
        raise ValueError("PartyHandle has a partial claim transition; complete or reverse its migration history.")
    return False


def immediate_constraints(schema_editor):
    # The RemoveField that follows alters a table these new rows reference;
    # PostgreSQL must check their foreign keys now or it refuses that DDL.
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def claim_each_link(apps, schema_editor):
    immediate_constraints(schema_editor)
    alias = schema_editor.connection.alias
    links = apps.get_model("parties", "PartyHandle")._base_manager.using(alias).order_by("pk")
    claim_model = apps.get_model("parties", "PartyHandleClaim")
    batch = []
    rows = links.values("pk", "source", "confidence", "metadata", "created_by_id")
    for row in rows.iterator(chunk_size=_BATCH):
        batch.append(
            claim_model(
                link_id=row["pk"],
                source=row["source"],
                confidence=row["confidence"],
                metadata=row["metadata"] or {},
                created_by_id=row["created_by_id"],
            )
        )
        if len(batch) == _BATCH:
            claim_model._base_manager.using(alias).bulk_create(batch)
            batch = []
    if batch:
        claim_model._base_manager.using(alias).bulk_create(batch)


def restore_link_metadata(apps, schema_editor):
    immediate_constraints(schema_editor)
    alias = schema_editor.connection.alias
    link_model = apps.get_model("parties", "PartyHandle")
    claims = apps.get_model("parties", "PartyHandleClaim")._base_manager.using(alias).order_by("link_id", "source")
    merged = {}
    for link_id, metadata in claims.values_list("link_id", "metadata").iterator(chunk_size=_BATCH):
        merged.setdefault(link_id, {}).update(metadata or {})
    for link_id, metadata in merged.items():
        link_model._base_manager.using(alias).filter(pk=link_id).update(metadata=metadata)


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("parties", "__latest__"),
    ]

    operations = [
        migrations.CreateModel(
            name="PartyHandleClaim",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=angee.base.mixins.retained_set_null,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=angee.base.mixins.retained_set_null,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "confidence",
                    models.FloatField(
                        default=1.0,
                        validators=[
                            django.core.validators.MinValueValidator(0.0),
                            django.core.validators.MaxValueValidator(1.0),
                        ],
                    ),
                ),
                (
                    "source",
                    angee.base.fields.StateField(
                        choices=[
                            ("manual", "Manual"),
                            ("import", "Import"),
                            ("email_match", "Email Match"),
                            ("llm", "LLM"),
                            ("oauth", "OAuth"),
                            ("carddav", "CardDAV"),
                            ("community", "Community"),
                            ("rule", "Rule"),
                        ],
                        db_index=True,
                        default="manual",
                        max_length=11,
                    ),
                ),
                (
                    "link",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="claims",
                        to="parties.partyhandle",
                    ),
                ),
                ("metadata", models.JSONField(blank=True, default=dict)),
            ],
            options={
                "ordering": ("link", "source"),
                "abstract": False,
                "base_manager_name": "_rebac_base",
                "default_manager_name": "objects",
                "constraints": [models.UniqueConstraint(fields=("link", "source"), name="uq_party_handle_claim")],
                "indexes": [],
            },
            managers=[
                ("_rebac_base", django.db.models.manager.Manager()),
                ("objects", django.db.models.manager.Manager()),
            ],
        ),
        migrations.RunPython(claim_each_link, restore_link_metadata),
        migrations.RemoveField("partyhandle", "metadata"),
    ]
