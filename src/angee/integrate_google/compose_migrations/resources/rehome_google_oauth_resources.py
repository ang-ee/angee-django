"""Retain ledger and target identity while integrate_google takes ownership of the Gemini client seed."""

from django.db import migrations

REHOMED_XREFS = ("oauth_gemini",)


def applies(project_state):
    return ("resources", "resource") in project_state.models


def rehome(apps, schema_editor):
    resources = apps.get_model("resources", "Resource")._base_manager.using(schema_editor.connection.alias)
    for xref in REHOMED_XREFS:
        old = resources.filter(source_addon="angee.integrate", xref=xref).first()
        if old is None:
            continue
        if resources.filter(source_addon="angee.integrate_google", xref=xref).exists():
            raise RuntimeError(
                f"Both Integrate and integrate_google own {xref}; reconcile the ledger identities "
                "before applying this migration. No resource target was changed."
            )
        resources.filter(pk=old.pk).update(source_addon="angee.integrate_google")


class Migration(migrations.Migration):
    dependencies = []
    operations = [migrations.RunPython(rehome, migrations.RunPython.noop)]
