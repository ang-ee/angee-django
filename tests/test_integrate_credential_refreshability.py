"""Existing credentials receive the same public fact as current material writes."""

from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection
from rebac import system_context

from angee.integrate.runtime_migrations.credential_refreshability import applies, backfill_refreshability
from tests.conftest import Credential, make_integration


def test_backfill_waits_for_the_generated_host_column():
    state = SimpleNamespace(models={})
    assert not applies(state)
    state.models[("integrate", "credential")] = SimpleNamespace(fields={"material": object()})
    assert not applies(state)
    state.models[("integrate", "credential")].fields["refreshable"] = object()
    assert applies(state)


@pytest.mark.usefixtures("composed_tables")
def test_historical_backfill_preserves_material_and_respects_provider_refresh_support():
    with system_context(reason="test refreshability backfill"):
        rows = [
            make_integration(
                f"old-grant-{index}", kind="oauth", material={"access_token": "fake", "refresh_token": "fake-refresh"},
            ).credential
            for index in range(2)
        ]
        rows[1].oauth_client.supports_refresh = False
        rows[1].oauth_client.save(update_fields=["supports_refresh"])
        material = [row.material for row in rows]
        Credential.objects.filter(pk__in=[row.pk for row in rows]).update(refreshable=False)
    # Migrate runs without an actor context, before REBAC's audit table may exist.
    backfill_refreshability(apps, SimpleNamespace(connection=connection))
    with system_context(reason="test refreshability result"):
        for row in rows:
            row.refresh_from_db()
        assert [row.refreshable for row in rows] == [True, False]
        assert [row.material for row in rows] == material
