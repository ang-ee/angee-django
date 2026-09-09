"""Add optional legal and payment details without inventing historical values."""

import angee.base.fields
import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def applies(project_state):
    """Upgrade only a retained pre-feature schema."""
    return ("parties", "party") in project_state.models and ("parties", "bankaccount") not in project_state.models


class Migration(migrations.Migration):
    dependencies = []

    operations = [
        migrations.AddField(
            model_name="party",
            name="tax_country",
            field=models.CharField(
                blank=True,
                default="",
                max_length=2,
                validators=[django.core.validators.RegexValidator("^[A-Z]{2}$", "Use a two-letter country code.")],
            ),
        ),
        migrations.AddField(
            model_name="party",
            name="vat",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.CreateModel(
            name="Bank",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("is_archived", models.BooleanField(db_index=True, default=False)),
                ("name", models.CharField(max_length=200)),
                ("bic", models.CharField(blank=True, default="", max_length=11)),
                ("country", models.CharField(blank=True, default="", max_length=2)),
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
                "ordering": ("name", "sqid"),
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="BankAccount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("is_archived", models.BooleanField(db_index=True, default=False)),
                ("holder_name", models.CharField(blank=True, default="", max_length=200)),
                (
                    "number_kind",
                    angee.base.fields.StateField(
                        choices=[("iban", "IBAN"), ("local", "Local account number")],
                        db_index=True,
                        default="iban",
                        max_length=5,
                    ),
                ),
                ("account_number", models.CharField(max_length=64)),
                (
                    "bank",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, related_name="accounts", to="parties.bank"
                    ),
                ),
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
                    "party",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, related_name="bank_accounts", to="parties.party"
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
                "ordering": ("account_number", "sqid"),
                "abstract": False,
                "constraints": [
                    models.UniqueConstraint(
                        fields=("party", "bank", "account_number"), name="uq_party_bank_account_number"
                    )
                ],
            },
        ),
    ]
