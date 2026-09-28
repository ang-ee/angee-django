"""Ownerless saves, concrete-parent authority, and transfer constraint refusal."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.db.models.signals import post_save, pre_save
from django.test.utils import CaptureQueriesContext
from rebac import SubjectRef, actor_context, system_context
from rebac.errors import PermissionDenied
from rebac.models import PermissionAuditEvent

from angee.base.checks import check_ownership
from tests.conftest import Vault, create_user
from tests.core_persistence import OWNERSHIP_SCHEMA, OwnedRow, OwnershipContainer, ownership_tables  # noqa: F401
from tests.core_seam_models import (
    ConstrainedVaultChild,
    SubjectGuardedLeaf,
    SubjectGuardedRow,
    constrained_vault_tables,  # noqa: F401
    subject_tables,  # noqa: F401
)


@pytest.mark.usefixtures("ownership_tables")
@pytest.mark.parametrize("actor_kind", ["ambient-user", "pinned-user", "service"])
@pytest.mark.parametrize("explicit_author", [False, True])
def test_ownerless_insert_preserves_audit_history_and_native_save_signals(actor_kind, explicit_author):
    actor, author = create_user("actor"), create_user("author")
    row = OwnedRow(created_by=author) if explicit_author else OwnedRow()
    ambient = actor
    if actor_kind == "pinned-user":
        row.with_actor(actor)
        ambient = author
    elif actor_kind == "service":
        row.with_actor(SubjectRef.of("service/account", "worker"))
    saves = []

    def capture(sender, instance, created, **kwargs):
        saves.append((instance.pk, instance.owner_id, instance.created_by_id, created))

    post_save.connect(capture, sender=OwnedRow)
    try:
        with actor_context(ambient):
            row.save(ownerless=True)
    finally:
        post_save.disconnect(capture, sender=OwnedRow)
    expected_author = author.pk if explicit_author else (None if actor_kind == "service" else actor.pk)
    assert saves == [(row.pk, None, expected_author, True)]
    with system_context(reason="test.ownerless.inspect"):
        row.refresh_from_db()
    assert row.owner_id is None
    assert row.created_by_id == expected_author
    [history] = row.history.all()
    assert (history.owner_id, history.created_by_id, history.history_type) == (None, expected_author, "+")


@pytest.mark.usefixtures("ownership_tables")
def test_ownerless_insert_retains_create_authorization():
    row = OwnedRow()
    with actor_context(AnonymousUser()), pytest.raises(PermissionDenied):
        row.save(ownerless=True)
    with system_context(reason="test.ownerless.denied.inspect"):
        assert not OwnedRow.objects.exists()
    assert not OwnedRow.history.exists()


@pytest.mark.usefixtures("ownership_tables")
@pytest.mark.parametrize("existing,has_owner", [(False, True), (True, False), (True, True)])
def test_ownerless_intent_refuses_existing_rows_or_explicit_owners_before_queries(
    existing, has_owner, django_assert_num_queries,
):
    actor = create_user("actor")
    row = OwnedRow(owner=actor if has_owner else None)
    with actor_context(actor):
        if existing:
            row.save(ownerless=not has_owner)
        history_before = list(OwnedRow.history.values())
        with django_assert_num_queries(0), pytest.raises(ValidationError) as caught:
            row.save(ownerless=True)
    assert "owner" in caught.value.message_dict
    assert list(OwnedRow.history.values()) == history_before
    with system_context(reason="test.ownerless.invalid.inspect"):
        assert OwnedRow.objects.count() == int(existing)
        if existing:
            row.refresh_from_db()
    assert row.owner_id == (actor.pk if has_owner else None)


@pytest.mark.usefixtures("ownership_tables")
def test_explicit_ownerless_insert_overrides_author_default_in_a_nonowning_container():
    actor, author = create_user("actor"), create_user("author")
    container = OwnershipContainer.objects.create(owns_items=False)
    with actor_context(actor):
        row = OwnedRow(container=container, created_by=author)
        row.save(ownerless=True)
    assert (row.owner_id, row.created_by_id) == (None, author.pk)


@pytest.mark.usefixtures("subject_tables")
@pytest.mark.parametrize("model", [SubjectGuardedRow, SubjectGuardedLeaf])
def test_child_transfer_uses_owning_parent_identity_permission_and_live_owner(model):
    owner, recipient, outsider = (create_user(name) for name in ("owner", "recipient", "outsider"))
    values = {"id": 11, "child_id": 71}
    if model is SubjectGuardedLeaf:
        values["leaf_id"] = 91
    with actor_context(owner):
        row = model.objects.create(**values)
        stale = model.objects.get(pk=row.pk)
    assert row.pk != row.id
    assert row._meta.get_field("owner").model is OwnedRow
    assert not OwnedRow.objects.with_actor(outsider).filter(pk=row.id).exists()
    with pytest.raises(PermissionDenied):
        row.with_actor(outsider).transfer_ownership(outsider)
    # The child explicitly denies this permission; the leaf allows everybody.
    assert row.with_actor(owner).has_access("child_transfer") is (model is SubjectGuardedLeaf)
    with actor_context(outsider):
        assert row.with_actor(owner).transfer_ownership(recipient) is row
    assert (row.owner_id, row.created_by_id) == (recipient.pk, owner.pk)
    with pytest.raises(PermissionDenied):
        stale.with_actor(owner).transfer_ownership(outsider)
    with system_context(reason="test.parent.transfer.inspect"):
        parent = OwnedRow.objects.get(pk=row.id)
    assert (parent.owner_id, parent.created_by_id) == (recipient.pk, owner.pk)
    assert not row.with_actor(owner).has_access("read")
    assert row.with_actor(recipient).has_access("read")


@pytest.mark.parametrize("parent_valid", [False, True])
def test_ownership_check_skips_children_but_still_checks_the_owning_parent(tmp_path, monkeypatch, parent_valid):
    config = apps.get_app_config("scopedemo")
    path = tmp_path / "ownership.zed"
    schema = OWNERSHIP_SCHEMA if parent_valid else OWNERSHIP_SCHEMA.replace("permission transfer = owner", "")
    path.write_text(schema)
    monkeypatch.setattr(config, "rebac_schema", str(path), raising=False)
    errors = check_ownership([config])
    assert [(error.id, error.obj) for error in errors] == ([] if parent_valid else [("angee.E022", OwnedRow)])


@pytest.mark.usefixtures("constrained_vault_tables")
@pytest.mark.parametrize("model", [Vault, ConstrainedVaultChild])
def test_transfer_refuses_hidden_constraint_conflicts_before_save_and_keeps_transaction_usable(model):
    owner, recipient = create_user("owner"), create_user("recipient")
    with actor_context(recipient):
        conflicting = Vault.objects.create(name="Shared name")
    with actor_context(owner):
        row = model.objects.create(name="Shared name", **({"child_id": 71} if model is ConstrainedVaultChild else {}))
        assert not Vault.objects.filter(pk=conflicting.pk).exists()
    with system_context(reason="test.constraints.snapshot"):
        before = list(model.objects.filter(pk=row.pk).values())
    history_before = list(Vault.history.order_by("history_id").values())
    saves = []

    def capture(sender, instance, **kwargs):
        saves.append(instance.pk)

    pre_save.connect(capture, sender=model)
    post_save.connect(capture, sender=model)
    try:
        with transaction.atomic(), actor_context(owner):
            with CaptureQueriesContext(connection) as queries, pytest.raises(ValidationError):
                row.with_actor(owner).transfer_ownership(recipient)
            assert not transaction.get_rollback()
            assert Vault.objects.filter(pk=row.id).exists()
    finally:
        pre_save.disconnect(capture, sender=model)
        post_save.disconnect(capture, sender=model)
    assert saves == []
    writes = [
        query["sql"] for query in queries
        if query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]
    # Native REBAC records elevation even when the enclosing write is refused.
    audit_insert = f'INSERT INTO "{PermissionAuditEvent._meta.db_table}" '
    assert [sql for sql in writes if not sql.startswith(audit_insert)] == []
    assert list(Vault.history.order_by("history_id").values()) == history_before
    with system_context(reason="test.constraints.inspect"):
        assert list(model.objects.filter(pk=row.pk).values()) == before
    assert (row.owner_id, row.created_by_id) == (owner.pk, owner.pk)


@pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL ownership concrete-parent row locks")
@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("subject_tables")
def test_child_transfer_locks_every_concrete_ancestor_before_subject_validation(monkeypatch):
    owner, recipient = create_user("owner"), create_user("recipient")
    with actor_context(owner):
        row = SubjectGuardedLeaf.objects.create(id=11, child_id=71, leaf_id=91)
    validating, finish = Event(), Event()
    validate = SubjectGuardedLeaf.validate_record_access_subject

    def hold_validation(self, relation, subject):
        validate(self, relation, subject)
        validating.set()
        assert finish.wait(timeout=10), "The lock inspection did not finish."

    monkeypatch.setattr(SubjectGuardedLeaf, "validate_record_access_subject", hold_validation)

    def transfer():
        close_old_connections()
        try:
            with actor_context(owner):
                SubjectGuardedLeaf.objects.get(pk=row.pk).transfer_ownership(recipient)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(transfer)
        try:
            assert validating.wait(timeout=10), "Transfer did not reach subject validation."
            for model, pk in ((OwnedRow, row.id), (SubjectGuardedRow, row.child_id), (SubjectGuardedLeaf, row.pk)):
                with pytest.raises(DatabaseError) as caught, transaction.atomic():
                    with system_context(reason="test.ownership.lock.inspect"):
                        model.objects.select_for_update(nowait=True).get(pk=pk)
                assert caught.value.__cause__.sqlstate == "55P03"
        finally:
            finish.set()
        future.result(timeout=10)
    with system_context(reason="test.ownership.lock.result"):
        row.refresh_from_db()
    assert row.owner_id == recipient.pk
