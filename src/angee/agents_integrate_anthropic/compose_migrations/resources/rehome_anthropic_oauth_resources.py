"""Retain ledger and target identity while Anthropic takes ownership of its presets."""

from django.db import migrations

from angee.resources.models import move_seed_ledger


def applies(project_state):
    return ("resources", "resource") in project_state.models


def rehome(apps, schema_editor):
    move_seed_ledger(
        apps, schema_editor, xrefs=("oauth_anthropic_platform", "oauth_anthropic_personal"),
        from_addon="angee.integrate", to_addon="angee.agents_integrate_anthropic",
    )


class Migration(migrations.Migration):
    dependencies = []
    operations = [migrations.RunPython(rehome, migrations.RunPython.noop)]
