"""Populate public refreshability after the host generates its metadata column."""

import json
from itertools import islice

from django.db import migrations


def applies(project_state):
    credential = project_state.models.get(("integrate", "credential"))
    return credential is not None and "refreshable" in credential.fields


def backfill_refreshability(apps, schema_editor):
    """Snapshot OAuth's refresh-grant policy onto existing historical credentials.

    Historical models have no kind-handler methods. Keep this released policy
    snapshot here; normal writes derive the fact through the current handler.
    Material is decrypted by its native field and never printed or logged.
    """

    # Historical base managers carry no REBAC scope; an actor context would write
    # audit rows before a fresh database has the audit table.
    rows = apps.get_model("integrate", "Credential")._base_manager.using(schema_editor.connection.alias)
    rows.update(refreshable=False)
    candidates = rows.filter(kind="oauth", oauth_client__supports_refresh=True).only("pk", "material")
    iterator = candidates.iterator(chunk_size=500)
    while chunk := list(islice(iterator, 500)):
        refreshable = [row.pk for row in chunk if json.loads(row.material or "{}").get("refresh_token")]
        if refreshable:
            rows.filter(pk__in=refreshable).update(refreshable=True)


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = []
    operations = [migrations.RunPython(backfill_refreshability, migrations.RunPython.noop)]
