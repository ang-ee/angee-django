"""Scope retained local bank account numbers to their bank."""

from django.db import migrations, models


def applies(project_state):
    """Upgrade only the original party-wide account-number constraint."""
    account = project_state.models.get(("parties", "bankaccount"))
    return account is not None and any(
        constraint.name == "uq_party_bank_account_number" and constraint.fields == ("party", "account_number")
        for constraint in account.options.get("constraints", [])
    )


class Migration(migrations.Migration):
    """Preserve rows while allowing equal local numbers at different banks."""

    dependencies = []
    operations = [
        migrations.RemoveConstraint(model_name="bankaccount", name="uq_party_bank_account_number"),
        migrations.AddConstraint(
            model_name="bankaccount",
            constraint=models.UniqueConstraint(
                fields=("party", "bank", "account_number"), name="uq_party_bank_account_number",
            ),
        ),
    ]
