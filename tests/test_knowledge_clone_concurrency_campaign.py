"""PostgreSQL arbitrates clones after two callers miss the same receipt."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import close_old_connections, connection, connections
from rebac import actor_context, system_context

from angee.knowledge.models import VaultQuerySet
from tests.conftest import MarkdownPage, Page, Vault
from tests.knowledge_campaign import clone_template as clone_template

pytestmark = pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL clone two-writer contract")


def test_two_same_key_clones_after_validation_commit_one_complete_copy(clone_template, monkeypatch):
    fixture = clone_template
    missed, validated = Barrier(2), Barrier(2)
    lookup = VaultQuerySet.for_creation_key
    full_clean = Vault.full_clean

    def synchronized_miss(queryset, scope, key, fingerprint):
        result = lookup(queryset, scope, key, fingerprint)
        if result is None and key == "race":
            missed.wait(timeout=10)
        return result

    def synchronized_validation(instance, *args, **kwargs):
        result = full_clean(instance, *args, **kwargs)
        if instance.client_creation_key == "race":
            validated.wait(timeout=10)
        return result

    monkeypatch.setattr(VaultQuerySet, "for_creation_key", synchronized_miss)
    monkeypatch.setattr(Vault, "full_clean", synchronized_validation)

    def clone():
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout TO '10s'")
            with actor_context(fixture.actor):
                return Vault.objects.create_from(
                    fixture.source, name="Concurrent", owned=False, client_creation_key="race",
                ).pk
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(clone) for _ in range(2)]
        identities = [future.result(timeout=30) for future in futures]
    assert identities[0] == identities[1]
    with system_context(reason="test.clone.race.result"):
        assert Vault.objects.filter(client_creation_key="race").count() == 1
        assert Page.objects.filter(vault_id=identities[0]).count() == 4
        assert MarkdownPage.objects.filter(page__vault_id=identities[0]).count() == 2
