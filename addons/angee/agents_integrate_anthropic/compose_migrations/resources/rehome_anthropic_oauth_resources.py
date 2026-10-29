"""Retain ledger and target identity while Anthropic takes ownership of its presets."""

from django.db import migrations


def applies(project_state):
    return ("resources", "resource") in project_state.models


def rehome(apps, schema_editor):
    resources = apps.get_model("resources", "Resource")._base_manager.using(schema_editor.connection.alias)
    for xref in ("oauth_anthropic_platform", "oauth_anthropic_personal"):
        old = resources.filter(source_addon="angee.integrate", xref=xref).first()
        if old is None:
            continue
        if resources.filter(source_addon="angee.agents_integrate_anthropic", xref=xref).exists():
            raise RuntimeError(
                f"Both Integrate and Anthropic own {xref}; reconcile the ledger identities "
                "before applying this migration. No resource target was changed."
            )
        resources.filter(pk=old.pk).update(source_addon="angee.agents_integrate_anthropic")


class Migration(migrations.Migration):
    dependencies = []
    operations = [migrations.RunPython(rehome, migrations.RunPython.noop)]
