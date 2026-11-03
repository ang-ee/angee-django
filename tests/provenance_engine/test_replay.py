from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import override_settings

from angee.provenance.engine.context import changeset, current_changeset
from angee.provenance.engine.policy import Policy
from tests.provenance_engine.replay_app.models import Entry, Folder
from tests.provenance_engine.storage.models import Changeset


class AllowPolicy(Policy):
    def authorize(self, actor, action, instance):
        pass

    def history_user(self, actor):
        return None


@pytest.fixture(autouse=True)
def policy():
    with override_settings(PROVENANCE_POLICY="tests.provenance_engine.test_replay.AllowPolicy"):
        yield


def write(callback):
    with changeset(actor="test"):
        result = callback()
        receipt = current_changeset()
    return result, receipt


@pytest.mark.django_db(transaction=True)
def test_replay_and_inverse():
    folder, creation = write(lambda: Folder.objects.create(name="before"))
    _, update = write(lambda: Folder.objects.filter(pk=folder.pk).update(name="after"))
    inverse = update.revert(actor="test")
    folder.refresh_from_db()
    assert folder.name == "before"
    inverse.revert(actor="test")
    folder.refresh_from_db()
    assert folder.name == "after"
    with pytest.raises(ValidationError):
        creation.revert(actor="test")


@pytest.mark.django_db(transaction=True)
def test_restore_deleted_fk_document_and_remove_inverse():
    folder, _ = write(lambda: Folder.objects.create(name="folder"))
    entry, _ = write(lambda: Entry.objects.create(folder=folder, value=Decimal("1.250")))
    folder_pk, entry_pk = folder.pk, entry.pk
    _, deletion = write(folder.delete)
    inverse = deletion.revert(actor="test")
    assert Entry.objects.get(pk=entry_pk).folder_id == folder_pk
    inverse.revert(actor="test")
    assert not Folder.objects.filter(pk=folder_pk).exists()


@pytest.mark.django_db(transaction=True)
def test_document_scope_inverse():
    folder, _ = write(lambda: Folder.objects.create(name="folder"))
    _entry, receipt = write(lambda: Entry.objects.create(folder=folder, value=Decimal("1.250")))
    assert Changeset.objects.touching(folder).filter(pk=receipt.pk).exists()
    assert (Folder, folder.pk) in list(Changeset.objects.filter(pk=receipt.pk).records_touched())


@pytest.mark.django_db(transaction=True)
def test_export_reproducible_and_tagged():
    folder, _ = write(lambda: Folder.objects.create(name="folder"))
    _, receipt = write(lambda: Entry.objects.create(folder=folder, value=Decimal("1.250")))
    first = receipt.document(actor="test")
    assert receipt.document(actor="test") == first
    assert '"$type":"Decimal","value":"1.250"' in first
    assert len(receipt.hash) == 64


@pytest.mark.django_db(transaction=True)
def test_multi_table_delete_replay_restores_native_parent_before_child():
    from tests.provenance_engine.replay_app.models import SpecialFolder

    folder, _ = write(lambda: SpecialFolder.objects.create(name="parent", tag="child"))
    key = folder.pk
    _, deletion = write(folder.delete)
    deletion.revert(actor="test")
    restored = SpecialFolder.objects.get(pk=key)
    assert (restored.name, restored.tag) == ("parent", "child")


class DenyDeletePolicy(AllowPolicy):
    def authorize(self, actor, action, instance):
        if action == "delete":
            raise PermissionDenied("Denied")


@pytest.mark.django_db(transaction=True)
def test_replay_authorizes_all_before_mutation():
    with changeset(actor="test"):
        folder = Folder.objects.create(name="folder")
        Entry.objects.create(folder=folder, value=Decimal("1.250"))
        boundary = current_changeset()
    before = Changeset.objects.count()
    with override_settings(PROVENANCE_POLICY="tests.provenance_engine.test_replay.DenyDeletePolicy"):
        with pytest.raises(PermissionDenied):
            boundary.revert(actor="test")
    assert Folder.objects.filter(pk=folder.pk).exists()
    assert Entry.objects.filter(folder=folder).exists()
    assert Changeset.objects.count() == before


@pytest.mark.django_db(transaction=True)
def test_equal_history_dates_do_not_hide_later_write():
    folder, _ = write(lambda: Folder.objects.create(name="original"))
    _, first = write(lambda: Folder.objects.filter(pk=folder.pk).update(name="first"))
    _, _last = write(lambda: Folder.objects.filter(pk=folder.pk).update(name="last"))
    timestamp = folder.history.first().history_date
    folder.history.all().update(history_date=timestamp)
    with pytest.raises(ValidationError):
        first.revert(actor="test")
    folder.refresh_from_db()
    assert folder.name == "last"


@pytest.mark.django_db(databases=["default", "other"], transaction=True)
def test_separately_routed_history_refuses_cross_database_replay():
    from tests.provenance_engine.capture_app.models import Note

    with changeset(actor="test"):
        Note.objects.create(id=777, title="default unchanged")
    with changeset(actor="test"):
        Note.objects.using("other").create(id=777, title="other before")
    with changeset(actor="test"):
        Note.objects.using("other").filter(pk=777).update(title="other after")
        receipt = current_changeset(using="default")
    assert Note.history.filter(history_changeset_id=receipt.pk).get().source_database == "other"
    with pytest.raises(ValidationError, match="same database"):
        receipt.revert(actor="test")
    assert Note.objects.get(pk=777).title == "default unchanged"
    assert Note.objects.using("other").get(pk=777).title == "other after"


@pytest.mark.django_db(transaction=True)
def test_revert_creation_refuses_later_cascade_dependents():
    folder, creation = write(lambda: Folder.objects.create(name="folder"))
    entry, _ = write(lambda: Entry.objects.create(folder=folder, value=Decimal("1.250")))
    with pytest.raises(ValidationError, match="outside this boundary"):
        creation.revert(actor="test")
    assert Folder.objects.filter(pk=folder.pk).exists()
    assert Entry.objects.filter(pk=entry.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_revert_creation_refuses_set_null_effects_on_later_rows():
    from tests.provenance_engine.replay_app.models import OptionalEntry

    folder, creation = write(lambda: Folder.objects.create(name="folder"))
    entry, _ = write(lambda: OptionalEntry.objects.create(folder=folder, title="retained"))
    with pytest.raises(ValidationError, match="outside this boundary"):
        creation.revert(actor="test")
    entry.refresh_from_db()
    assert entry.folder_id == folder.pk
