"""Ownership defaults and authorized transfers preserve audit attribution."""

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models.signals import post_save
from rebac import actor_context, system_context
from rebac.errors import PermissionDenied

from tests.conftest import create_user
from tests.core_persistence import OwnedRow, OwnershipContainer, ownership_tables  # noqa: F401

pytestmark = pytest.mark.usefixtures("ownership_tables")


def test_owner_defaults_to_actor_and_explicit_attribution_takes_precedence():
    actor, author = create_user("actor"), create_user("author")
    with actor_context(actor):
        direct = OwnedRow.objects.create()
        delegated = OwnedRow.objects.create(created_by=author)
    assert direct.owner_id == direct.created_by_id == actor.pk
    assert delegated.owner_id == delegated.created_by_id == author.pk


def test_explicit_owner_wins_without_rewriting_attribution():
    actor, owner = create_user("actor"), create_user("owner")
    with actor_context(actor):
        row = OwnedRow.objects.create(owner=owner)
    assert (row.owner_id, row.created_by_id) == (owner.pk, actor.pk)


def test_system_insert_without_actor_leaves_owner_and_attribution_empty(monkeypatch):
    monkeypatch.setattr(OwnedRow, "owner_container", None)
    assert OwnedRow().container_owns_items() is False
    with system_context(reason="test.ownership.unattended"):
        row = OwnedRow.objects.create()
    assert row.owner_id is row.created_by_id is None


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("owns_items", [False, True])
def test_container_flag_controls_only_the_insert_owner_default(cached, owns_items):
    actor = create_user("actor")
    container = OwnershipContainer.objects.create(owns_items=owns_items)
    values = {"container": container} if cached else {"container_id": container.pk}
    with actor_context(actor):
        row = OwnedRow.objects.create(**values)
        assert row.container_owns_items() is owns_items
    assert row.owner_id == (None if owns_items else actor.pk)
    assert row.created_by_id == actor.pk


def test_explicit_owner_wins_in_an_owning_container():
    actor, owner = create_user("actor"), create_user("owner")
    container = OwnershipContainer.objects.create(owns_items=True)
    with actor_context(actor):
        row = OwnedRow.objects.create(container=container, owner=owner)
    assert (row.owner_id, row.created_by_id) == (owner.pk, actor.pk)


def test_flipping_container_flag_changes_later_inserts_only():
    actor = create_user("actor")
    container = OwnershipContainer.objects.create()
    with actor_context(actor):
        first = OwnedRow.objects.create(container=container)
        container.owns_items = True
        container.save()
        second = OwnedRow.objects.create(container=container)
        first.title = "edited"
        first.save()
    with system_context(reason="test.ownership.inspect"):
        first.refresh_from_db()
        second.refresh_from_db()
    assert (first.owner_id, second.owner_id) == (actor.pk, None)
    assert first.created_by_id == second.created_by_id == actor.pk


def test_transfer_and_clear_preserve_author_and_change_live_access():
    author, recipient = create_user("author"), create_user("recipient")
    with actor_context(author):
        row = OwnedRow.objects.create()
        assert row.transfer_ownership(recipient) is row
        assert not OwnedRow.objects.filter(pk=row.pk).exists()
    with actor_context(recipient):
        transferred = OwnedRow.objects.get(pk=row.pk)
        assert (transferred.owner_id, transferred.created_by_id) == (recipient.pk, author.pk)
        transferred.transfer_ownership(None)
        assert not OwnedRow.objects.filter(pk=row.pk).exists()
    with system_context(reason="test.ownership.inspect"):
        row.refresh_from_db()
    assert (row.owner_id, row.created_by_id) == (None, author.pk)


def test_same_owner_transfer_checks_permission_but_emits_no_save():
    owner, outsider = create_user("owner"), create_user("outsider")
    with actor_context(owner):
        row = OwnedRow.objects.create()
    saves = []

    def capture(sender, instance, **kwargs):
        saves.append(instance.pk)

    post_save.connect(capture, sender=OwnedRow)
    try:
        with actor_context(owner):
            row.with_actor(owner).transfer_ownership(owner)
        with actor_context(outsider), pytest.raises(PermissionDenied):
            row.with_actor(outsider).transfer_ownership(owner)
    finally:
        post_save.disconnect(capture, sender=OwnedRow)
    assert saves == []


def test_transfer_authorizes_the_locked_owner_instead_of_stale_instance():
    owner, next_owner, outsider = (create_user(name) for name in ("owner", "next", "outsider"))
    with actor_context(owner):
        row = OwnedRow.objects.create()
        stale = OwnedRow.objects.get(pk=row.pk)
        row.transfer_ownership(next_owner)
        with pytest.raises(PermissionDenied):
            stale.transfer_ownership(outsider)
    with system_context(reason="test.ownership.inspect"):
        row.refresh_from_db()
    assert (row.owner_id, row.created_by_id) == (next_owner.pk, owner.pk)


def test_system_context_without_actor_cannot_transfer_ownership():
    owner = create_user("owner")
    with system_context(reason="test.ownership.unattended"):
        row = OwnedRow.objects.create(owner=owner)
        with pytest.raises(PermissionDenied, match="acting user"):
            row.transfer_ownership(None)
        row.refresh_from_db()
    assert row.owner_id == owner.pk


def test_transfer_requires_saved_row_and_recipient():
    owner = create_user("owner")
    with actor_context(owner):
        with pytest.raises(ValidationError, match="saved row"):
            OwnedRow().transfer_ownership(owner)
        row = OwnedRow.objects.create()
        with pytest.raises(ValidationError, match="saved user"):
            row.transfer_ownership(get_user_model()(username="unsaved"))


def test_release_clears_only_selected_rows_for_the_named_owner_without_saves():
    owner, other = create_user("owner"), create_user("other")
    with system_context(reason="test.ownership.release.setup"):
        selected = OwnedRow.objects.create(owner=owner, created_by=owner)
        outside = OwnedRow.objects.create(owner=owner, created_by=owner)
        other_row = OwnedRow.objects.create(owner=other, created_by=other)
    history_before = list(OwnedRow.history.order_by("history_id").values())
    saves = []

    def capture(sender, instance, **kwargs):
        saves.append(instance.pk)

    post_save.connect(capture, sender=OwnedRow)
    try:
        count = OwnedRow.objects.filter(pk__in=[selected.pk, other_row.pk]).release(owner)
    finally:
        post_save.disconnect(capture, sender=OwnedRow)
    assert count == 1
    assert saves == []
    assert list(OwnedRow.history.order_by("history_id").values()) == history_before
    with system_context(reason="test.ownership.release.inspect"):
        for row in (selected, outside, other_row):
            row.refresh_from_db()
    assert (selected.owner_id, selected.created_by_id) == (None, owner.pk)
    assert (outside.owner_id, outside.created_by_id) == (owner.pk, owner.pk)
    assert (other_row.owner_id, other_row.created_by_id) == (other.pk, other.pk)


def test_release_refuses_an_unsaved_user():
    with pytest.raises(ValidationError, match="saved user"):
        OwnedRow.objects.release(get_user_model()(username="unsaved"))


def test_deleting_owner_clears_owner_and_preserves_other_authors():
    owner, author = create_user("owner"), create_user("author")
    with system_context(reason="test.ownership.delete-user"):
        row = OwnedRow.objects.create(owner=owner, created_by=author)
        owner.delete()
        row.refresh_from_db()
    assert (row.owner_id, row.created_by_id) == (None, author.pk)
