import pytest
from django.db import transaction

from angee.provenance.context import changeset, current_changeset
from angee.provenance.engine.exceptions import CreationKeyConflict
from angee.provenance.engine.managers import ChangesetManager
from tests.provenance_engine.replay_app.models import Folder
from tests.provenance_engine.storage.models import Changeset


@pytest.mark.django_db(transaction=True)
def test_claim_requires_outer_atomic():
    with changeset(fresh=True), pytest.raises(RuntimeError):
        Changeset.objects.claim("folder", "key", fingerprint="abc")


@pytest.mark.django_db(transaction=True)
def test_claim_receipt_and_mismatched_retry():
    with transaction.atomic(), changeset(fresh=True):
        assert Changeset.objects.claim("folder", "key", fingerprint="abc") is None
        folder = Folder.objects.create(name="claimed")
        current_changeset().record_created(folder)
    with transaction.atomic(), changeset(fresh=True):
        receipt = Changeset.objects.claim("folder", "key", fingerprint="abc")
        assert receipt.created_row == folder
    with transaction.atomic(), changeset(fresh=True), pytest.raises(CreationKeyConflict):
        Changeset.objects.claim("folder", "key", fingerprint="different")


@pytest.mark.django_db(transaction=True)
def test_host_claim_encoding_and_conflict_preserve_one_native_receipt(monkeypatch):
    class HostConflict(ValueError):
        pass

    monkeypatch.setattr(ChangesetManager, "creation_fingerprint", staticmethod(lambda values: "host"))
    monkeypatch.setattr(ChangesetManager, "creation_conflict", HostConflict)
    with transaction.atomic(), changeset(fresh=True):
        assert Changeset.objects.claim_created("folder", "host-key", values=object(), model=Folder) is None
        folder = Folder.objects.create(name="host encoded")
        current_changeset().record_created(folder)
    with transaction.atomic(), changeset(fresh=True):
        assert Changeset.objects.claim_created("folder", "host-key", values=object(), model=Folder) == folder
    with transaction.atomic(), changeset(fresh=True), pytest.raises(HostConflict):
        Changeset.objects.claim("folder", "host-key", fingerprint="changed")
