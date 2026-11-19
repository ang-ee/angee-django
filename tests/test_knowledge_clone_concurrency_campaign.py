"""PostgreSQL arbitrates knowledge's two writers: clones that miss one receipt, and two requests for one folder."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import close_old_connections, connection, connections
from rebac import actor_context, system_context

from angee.messaging.testing.models import Person
from angee.provenance.context import changeset_model
from tests.conftest import MarkdownPage, Page, RecordBinding, Vault, create_user
from tests.knowledge_campaign import clone_template as clone_template

pytestmark = [
    pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL two-writer contracts"),
    pytest.mark.django_db(transaction=True),
]


def test_two_same_key_claims_commit_one_complete_clone(clone_template, monkeypatch):
    fixture = clone_template
    ready = Barrier(2)
    changesets = changeset_model().objects
    claim = changesets.claim

    def synchronized_claim(scope, key, *, fingerprint):
        if key == "race":
            ready.wait(timeout=10)
        return claim(scope, key, fingerprint=fingerprint)

    monkeypatch.setattr(changesets, "claim", synchronized_claim)

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


def test_two_requests_for_one_persons_folder_commit_one_folder():
    """Both requests start together; the person's row lock makes the second find the first's folder."""

    with system_context(reason="test.folder.race.seed"):
        owner = create_user("folder-race-owner")
        vault = Vault.objects.create(name="People race")
        person = Person.objects.create(display_name="Ada Example", created_by=owner)
    ready = Barrier(2)

    def request():
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout TO '10s'")
            ready.wait(timeout=10)
            with system_context(reason="test.folder.race.import"):
                return RecordBinding.objects.party_folder(person, vault=vault, title="Ada Example (folder)").pk
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(request) for _ in range(2)]
        folders = [future.result(timeout=30) for future in futures]
    assert folders[0] == folders[1]
    with system_context(reason="test.folder.race.result"):
        assert RecordBinding.objects.for_record(person, role=RecordBinding.objects.FOLDER_ROLE).count() == 1
        assert Page.objects.filter(vault=vault).count() == 1
