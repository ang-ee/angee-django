"""PostgreSQL arbitrates clones after two callers miss the same receipt."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import close_old_connections, connection, connections
from rebac import actor_context, system_context

from angee.base.changesets import changeset_model
from tests.conftest import MarkdownPage, Page, Vault
from tests.knowledge_campaign import clone_template as clone_template

pytestmark = pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL clone two-writer contract")


def test_two_same_key_claims_commit_one_complete_clone(clone_template, monkeypatch):
    fixture = clone_template
    ready = Barrier(2)
    manager_class = type(changeset_model().objects)
    claim = manager_class.claim

    def synchronized_claim(manager, scope, key, *, fingerprint):
        if key == "race":
            ready.wait(timeout=10)
        return claim(manager, scope, key, fingerprint=fingerprint)

    monkeypatch.setattr(manager_class, "claim", synchronized_claim)

    def clone():
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout TO '10s'")
            with actor_context(fixture.actor):
                return Vault.objects.create_from(
                    fixture.source, name="Concurrent", owned=False, idempotency_key="race",
                ).pk
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(clone) for _ in range(2)]
        identities = [future.result(timeout=30) for future in futures]
    assert identities[0] == identities[1]
    with system_context(reason="test.clone.race.result"):
        claim_row = changeset_model()._base_manager.get(
            scope=f"knowledge.vault.clone:{fixture.actor.pk}", key="race",
        )
        assert claim_row.created_row.pk == identities[0]
        assert Page.objects.filter(vault_id=identities[0]).count() == 4
        assert MarkdownPage.objects.filter(vault_id=identities[0]).count() == 2
