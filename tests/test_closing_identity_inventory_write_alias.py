"""Closing write owners retain aliases through validation, transactions and hooks."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import router
from rebac import system_context

from angee.iam import roles
from angee.iam_integrate_oidc import identity
from angee.integrate import models as integrate_models
from angee.messaging_integrate_imap.models import ImapChannelSampling
from tests.conftest import ExternalAccount, OAuthClient, Quota, Source
from tests.iam_models import Group
from tests.test_integrate_vcs import BLOBS, REPOS, TREE, Repository, Template, _vcs_bridge
from tests.test_integrate_vcs import vcs_tables as vcs_tables
from tests.test_posts import _feed
from tests.test_posts import posts_tables as posts_tables
from tests.test_transitions import TransitionRouter


@pytest.mark.django_db(transaction=True)
def test_preferences_explicit_alias_wins_over_stale_instance(
    database_alias: Callable[[str], AbstractContextManager[str]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The private preference transaction and save target the requested connection."""

    user = get_user_model().objects.create_user(username="closing-preferences")
    with database_alias("preferences_writer") as using:
        routing = TransitionRouter("wrong_writer")
        monkeypatch.setattr(router, "routers", [routing])
        user._state.db = "stale_instance"
        user.update_preferences({"theme": "dark"}, using=using)
        user.refresh_from_db(using=using)
        assert user.preferences == {"theme": "dark"}
        assert routing.writes == []


@pytest.mark.parametrize("operation", ["add_member", "remove_member"])
def test_group_membership_rejects_nondefault_before_access_or_tuple_work(
    monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    """No unbound upstream subject lookup or relationship write can run elsewhere."""

    permission = Mock(side_effect=AssertionError("Alias admission must precede access queries."))
    monkeypatch.setattr(Group, "has_access", permission)
    with pytest.raises(ValidationError, match="default authorization database"):
        getattr(Group(name="Routed group"), operation)("auth/user:1", using="writer")
    permission.assert_not_called()


@pytest.mark.parametrize("operation", ["grant_role", "revoke_role"])
def test_role_grants_reject_nondefault_before_resolving_subjects(
    monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    """An explicit alias cannot be silently ignored by the tuple library."""

    validate = Mock(side_effect=AssertionError("The REBAC boundary must reject first."))
    monkeypatch.setattr(roles, "validate_subject", validate)
    with pytest.raises(ValidationError, match="default authorization database"):
        getattr(roles, operation)(subject="auth/user:1", role="platform/role:admin", using="writer")
    validate.assert_not_called()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("selection", ["bound", "router"])
def test_quota_open_lock_and_debit_share_one_alias(
    posts_tables: None,
    database_alias: Callable[[str], AbstractContextManager[str]],
    monkeypatch: pytest.MonkeyPatch,
    selection: str,
) -> None:
    """Opening a quota period cannot escape to a read or independently routed write."""

    feed = _feed(f"quota-{selection}")
    with database_alias("quota_writer") as using, system_context(reason="test.closing.quota"):
        routing = TransitionRouter(using if selection == "router" else "wrong_writer")
        manager = Quota.objects if selection == "router" else Quota.objects.db_manager(using)
        monkeypatch.setattr(router, "routers", [routing])
        assert manager.consume(integration=feed, units=3, limit=5) is True
        assert manager.consume(integration=feed, units=3, limit=5) is False
        row = Quota._base_manager.using(using).get(integration_id=feed.pk)
        assert row.quota_used == 3
        assert routing.writes == ([None, None] if selection == "router" else [])


def test_source_refresh_pins_legacy_manager_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    """Source-kind overrides retain their old signature and selected manager binding."""

    source = Source(kind="template")
    source._state.adding = False
    source._state.db = "stale_instance"

    class LegacyManager:
        def db_manager(self, using: str) -> LegacyManager:
            assert using == "writer"
            return self

        def sync_from_source(self, target: Any) -> int:
            assert target is source
            assert target._state.db == "writer"
            return 7

    target = SimpleNamespace(objects=LegacyManager())
    monkeypatch.setattr(Source, "target_for_kind", classmethod(lambda cls, kind: target))
    assert source.refresh(using="writer") == 7


def test_oidc_resolution_pins_legacy_resolver_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    """Login resolution preserves custom hook signatures while pinning the OAuth row."""

    client = OAuthClient(login_enabled=True)
    client._state.adding = False
    client._state.db = "stale_instance"
    expected = object()

    def resolve(self: Any, *, sub: str, email: str | None, claims: dict[str, Any]) -> Any:
        assert self.oauth_client._state.db == "writer"
        return expected

    monkeypatch.setattr(identity.OidcIdentityResolver, "resolve", resolve)
    assert identity.resolve(client, sub="subject", email=None, claims={}, using="writer") is expected


@pytest.mark.django_db(transaction=True)
def test_template_refresh_reloads_source_relations_on_selected_writer(
    vcs_tables: None,
    database_alias: Callable[[str], AbstractContextManager[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inventory persistence and source timestamps share the explicit alias after FK reloads."""

    bridge = _vcs_bridge("closing-template", config={"stub_repos": REPOS, "stub_tree": TREE, "stub_blobs": BLOBS})
    bridge.discover_repositories()
    with system_context(reason="test.closing.inventory"):
        repository = Repository.objects.get(name="acme/widgets")
        source = Source.objects.create(repository_id=repository.pk, kind="template", path="templates")
        with database_alias("inventory_writer") as using:
            routing = TransitionRouter("wrong_writer")
            monkeypatch.setattr(router, "routers", [routing])
            source._state.db = "stale_instance"
            assert source.refresh(using=using) == 1
            template = Template._base_manager.using(using).get(source_id=source.pk)
            source.refresh_from_db(using=using)
            assert template.name == "Dev"
            assert source.last_synced_at is not None
            assert routing.writes == []


@pytest.mark.parametrize("operation", ["link", "grant_owner", "revoke_owner"])
def test_external_account_ownership_rejects_nondefault_before_tuple_work(
    monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    """Linking cannot leave account rows committed with grants on another database."""

    write = Mock(side_effect=AssertionError("No tuple operation may run before admission."))
    monkeypatch.setattr(integrate_models, "write_relationships", write)
    monkeypatch.setattr(integrate_models, "delete_relationship", write)
    with pytest.raises(ValidationError, match="default authorization database"):
        if operation == "link":
            ExternalAccount.objects.link(OAuthClient(pk=1), "subject", owner=object(), using="writer")
        else:
            getattr(ExternalAccount.objects, operation)(ExternalAccount(pk=1), object(), using="writer")
    write.assert_not_called()


def test_imap_mailbox_mutation_rejects_nondefault_before_channel_lookup() -> None:
    """The actor access check has no alias-aware upstream form, so admission fails first."""

    with pytest.raises(ValidationError, match="default authorization database"):
        ImapChannelSampling.prepare_imap_new_mail(SimpleNamespace(), actor=object(), using="writer")
