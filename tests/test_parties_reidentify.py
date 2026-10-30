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


@pytest.mark.usefixtures("composed_tables")
@pytest.mark.parametrize("source_name,target_name", [("", ""), ("Alias", ""), ("", "Kept"), ("Alias", "Kept")])
def test_handle_reidentify_dry_run_uses_kept_display_carry_without_domain_writes(source_name, target_name):
    from django.apps import apps
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from angee.base.signals import pre_merge

    with system_context(reason="test.parties.reidentify_preview"):
        source = Handle.objects.create(platform="email", value="old@example.com", display_name=source_name)
        target = Handle.objects.create(platform="email", value="new@example.com", display_name=target_name)
        events = []
        def observed(sender, **kwargs):
            events.append(sender)
        pre_merge.connect(observed, weak=False)
        try:
            with CaptureQueriesContext(connection) as queries:
                preview = Handle.objects.reidentify(source, expected_value=source.value,
                    value=target.value, display_name="Resolved", dry_run=True)
            assert not events
            writes = [query["sql"] for query in queries
                      if query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
            audit = connection.ops.quote_name(apps.get_model("rebac", "PermissionAuditEvent")._meta.db_table)
            assert writes and all(sql.startswith(f"INSERT INTO {audit}") for sql in writes)
            source.refresh_from_db()
            target.refresh_from_db()
            assert source.merged_into_id is None and source.value == "old@example.com"
            assert target.display_name == target_name
            result = Handle.objects.reidentify(source, expected_value=source.value,
                value=target.value, display_name="Resolved")
            assert preview == result == (True, not bool(source_name or target_name))
        finally:
            pre_merge.disconnect(observed)


def _preview_refusal(record):
    return "The alias is held by its addon guard."


@pytest.mark.usefixtures("composed_tables")
@pytest.mark.parametrize("dry_run", [False, True])
def test_handle_preview_and_fold_share_addon_refusals(settings, dry_run):
    from django.core.exceptions import ValidationError

    settings.ANGEE_MERGE_GUARDS = [__name__ + "._preview_refusal"]
    with system_context(reason="test.parties.preview_guard"):
        source = Handle.objects.create(platform="email", value="old@example.com")
        target = Handle.objects.create(platform="email", value="new@example.com")
        with pytest.raises(ValidationError, match="held by its addon guard"):
            Handle.objects.reidentify(source, expected_value=source.value, value=target.value, dry_run=dry_run)
        source.refresh_from_db()
        assert source.merged_into_id is None and source.value == "old@example.com"
