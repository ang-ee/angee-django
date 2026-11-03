from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connections, transaction

from angee.provenance.context import changeset, current_changeset
from tests.provenance_engine.replay_app.models import Folder
from tests.provenance_engine.storage.models import Changeset

pytestmark = [pytest.mark.postgresql, pytest.mark.django_db(transaction=True)]


def test_concurrent_creation_claim_returns_one_persisted_result():
    ready = Barrier(2, timeout=10)

    def create():
        close_old_connections()
        try:
            ready.wait()
            with transaction.atomic(), changeset(fresh=True):
                receipt = Changeset.objects.claim("folders", "one", fingerprint="same-content")
                if receipt is not None:
                    return False, receipt.created_row.pk
                result = Folder.objects.create(name="one")
                current_changeset().record_created(result)
                return True, result.pk
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(), range(2)))
    assert sum(won for won, _ in results) == 1
    assert len({key for _, key in results}) == 1
    assert Folder.objects.count() == 1
    assert Changeset.objects.filter(scope="folders", key="one").count() == 1
    assert Folder.history.count() == 1


def test_concurrent_revert_refuses_second_replay_after_waiting_for_lock(actor):
    with changeset(actor=actor):
        folder = Folder.objects.create(name="before")
    with changeset(actor=actor):
        folder.name = "after"
        folder.save()
        boundary_id = current_changeset().pk
    ready = Barrier(2, timeout=10)

    def revert():
        close_old_connections()
        try:
            boundary = Changeset.objects.get(pk=boundary_id)
            ready.wait()
            try:
                boundary.revert(actor=actor)
            except ValidationError:
                return "refused"
            return "reverted"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: revert(), range(2)))
    assert sorted(outcomes) == ["refused", "reverted"]
    folder.refresh_from_db()
    assert folder.name == "before"
    assert folder.history.count() == 3
