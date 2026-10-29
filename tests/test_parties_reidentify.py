"""Canonical handle rewrites and folds retain the shared identity lock owner."""

import pytest
from rebac import PermissionDenied, system_context

from angee.messaging.testing.models import Handle


@pytest.mark.usefixtures("composed_tables")
@pytest.mark.parametrize("target_exists", [False, True])
def test_handle_reidentify_rewrites_or_folds_canonical_identity(target_exists):
    with system_context(reason="test.parties.reidentify"):
        source = Handle.objects.create(platform="email", value="old@example.com")
        target = Handle.objects.create(platform="email", value="new@example.com") if target_exists else None
        result = Handle.objects.reidentify(source, expected_value="old@example.com", value="new@example.com",
                                           display_name="Known person")
        assert result == (target_exists, True)
        source.refresh_from_db()
        if target is None:
            assert source.value == "new@example.com" and source.display_name == "Known person"
        else:
            assert source.merged_into_id == target.pk
            target.refresh_from_db()
            assert target.display_name == "Known person"
        assert Handle.objects.reidentify(source, expected_value="old@example.com", value="else@example.com") is None


@pytest.mark.usefixtures("composed_tables")
def test_handle_reidentify_recovers_a_value_owner_created_after_initial_lookup(monkeypatch):
    with system_context(reason="test.parties.reidentify_race"):
        source = Handle.objects.create(platform="email", value="old@example.com")
        target = Handle.objects.create(platform="email", value="new@example.com")
        manager = type(Handle.objects)
        find_identity = manager.find_identity
        calls = []
        def raced_lookup(self, **kwargs):
            calls.append(kwargs)
            return None if len(calls) == 1 else find_identity(self, **kwargs)
        monkeypatch.setattr(manager, "find_identity", raced_lookup)
        outcome = Handle.objects.reidentify(source, expected_value="old@example.com", value="new@example.com")
        assert outcome == (True, False)
        source.refresh_from_db()
        assert source.merged_into_id == target.pk and len(calls) >= 3


def test_handle_reidentify_cannot_elevate_an_ordinary_caller():
    handle = Handle(platform="email", value="old@example.com")
    with pytest.raises(PermissionDenied, match="admitted system boundary"):
        Handle.objects.reidentify(handle, expected_value="old@example.com", value="new@example.com")
