"""Migration template: hand the Anthropic OAuth presets' ledger rows to ``agents_integrate_anthropic``.

Copy this module into the host's ``runtime/resources/migrations/`` under the host's
next migration name, then replace the placeholder dependency below with the host's own
explicit migration (never ``__latest__``). Run it before the next ``resources load``.

The presets moved between the addons with their content unchanged; moving their ledger
rows keeps the retained target and hash identities, so the next load neither adopts nor
re-applies them. The reverse hands the rows back. Either direction refuses, before
changing anything, when the receiving addon already ledgers an xref.
"""

from __future__ import annotations

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

from angee.resources.models import move_seed_ledger

XREFS = ("oauth_anthropic_platform", "oauth_anthropic_personal")
PREVIOUS_ADDON = "angee.integrate"
ADDON = "angee.agents_integrate_anthropic"


def rehome(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Hand the presets' ledger rows to ``agents_integrate_anthropic``, which now ships them."""

    move_seed_ledger(apps, schema_editor, xrefs=XREFS, from_addon=PREVIOUS_ADDON, to_addon=ADDON)


def restore(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Hand the presets' ledger rows back to ``integrate``."""

    move_seed_ledger(apps, schema_editor, xrefs=XREFS, from_addon=ADDON, to_addon=PREVIOUS_ADDON)


class Migration(migrations.Migration):
    # Placeholder: the host's migration that creates the ``resources.Resource`` ledger.
    dependencies = [("resources", "HOST_MIGRATION_CREATING_THE_RESOURCE_LEDGER")]
    operations = [migrations.RunPython(rehome, restore)]
