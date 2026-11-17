"""Migration template: hand the ``oauth_gemini`` seed's ledger row from ``integrate`` to ``integrate_google``.

Copy this module into the host's ``runtime/resources/migrations/`` under the host's
next migration name, then replace the placeholder dependency below with the host's own
explicit migration (never ``__latest__``). Run it before the next ``resources load``.

The seed moved between the addons with its content unchanged; moving its ledger row
keeps the next load from adopting and re-applying the released row. The reverse hands
the row back. Either direction refuses, before changing anything, when the receiving
addon already ledgers the xref.
"""

from __future__ import annotations

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

from angee.resources.models import move_seed_ledger

XREFS = ("oauth_gemini",)
PREVIOUS_ADDON = "angee.integrate"
ADDON = "angee.integrate_google"


def rehome_gemini_seed(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Hand the seed's ledger row to ``integrate_google``, which now ships it."""

    move_seed_ledger(apps, schema_editor, xrefs=XREFS, from_addon=PREVIOUS_ADDON, to_addon=ADDON)


def restore_gemini_seed(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Hand the seed's ledger row back to ``integrate``."""

    move_seed_ledger(apps, schema_editor, xrefs=XREFS, from_addon=ADDON, to_addon=PREVIOUS_ADDON)


class Migration(migrations.Migration):
    # Placeholder: the host's migration that creates the ``resources.Resource`` ledger.
    dependencies = [("resources", "HOST_MIGRATION_CREATING_THE_RESOURCE_LEDGER")]
    operations = [migrations.RunPython(rehome_gemini_seed, restore_gemini_seed)]
