"""Migrate converts old attribution reads before syncing the dependent-page policy."""

import re
from io import StringIO
from pathlib import Path

import pytest
from django.apps import apps
from django.core.management import call_command
from rebac import actor_context, system_context
from rebac.models import active_relationship_model

from angee.knowledge import models as knowledge_models
from angee.knowledge.signals import connect
from tests.conftest import Page, Vault
from tests.t3_campaign import campaign_access as campaign_access
from tests.t3_campaign import campaign_user as campaign_user
from tests.t3_campaign import messaging_access_schema as messaging_access_schema
from tests.t3_campaign import relationship_snapshot


@pytest.fixture
def old_page_policy(campaign_access, tmp_path, monkeypatch):
    """Sync a historical owner arm through the existing native schema mechanism."""
    config = apps.get_app_config("knowledge")
    current = Path(config.rebac_schema).read_text()
    start = current.index("definition knowledge/page {")
    end = current.index("}", start)
    page = current[start:end]
    historical = page.replace(
        "definition knowledge/page {",
        "definition knowledge/page {\n    relation owner: auth/user // rebac:field=created_by",
    )
    for action in ("read", "write", "delete"):
        # Only this definition gains its historical author arm.
        historical = re.sub(rf"(permission {action}\s*=)", r"\1 owner +", historical)
    path = tmp_path / "historical-knowledge.zed"
    path.write_text(current[:start] + historical + current[end:])
    monkeypatch.setattr(config, "rebac_schema", str(path))
    call_command("rebac", "sync", force_overwrite=True, yes=True, verbosity=0)
    yield path, current


@pytest.fixture
def attributed_pages(old_page_policy, campaign_user):
    author, owner = campaign_user("author"), campaign_user("vault-owner")
    with system_context(reason="tests.t3.legacy_pages"):
        owned = Vault.objects.create(name="Owned vault", owner=owner)
        other = Vault.objects.create(name="Other vault", owner=owner)
        ownerless = Vault.objects.create(name="Ownerless vault", owner=None)
        root = Page.objects.create(vault=owned, title="Author only", created_by=author)
        child = Page.objects.create(vault=owned, parent=root, title="Inherited author read", created_by=author)
        second = Page.objects.create(vault=other, title="Other author only", created_by=author)
        excluded = Page.objects.create(vault=ownerless, title="No grant", created_by=author)
        retained = Page.objects.create(vault=owned, title="Vault owner", created_by=owner)
    return author, owner, (root, child, second, excluded, retained)


def test_post_migrate_converts_once_and_sync_leaves_nothing_pending(attributed_pages, old_page_policy):
    author, owner, (root, child, second, excluded, retained) = attributed_pages
    before = relationship_snapshot()
    inventory = Page.objects.migrate_attribution()
    assert relationship_snapshot() == before
    assert {entry.pk for entry in inventory if entry.grants_viewer} == {root.pk, second.pk}
    assert {entry.pk for entry in inventory if entry.ownerless_vault} == {excluded.pk}
    assert retained.pk not in {entry.pk for entry in inventory}
    connect()
    call_command("migrate", interactive=False, verbosity=0, stdout=StringIO())
    converted = relationship_snapshot()
    assert converted != before
    call_command("migrate", interactive=False, verbosity=0, stdout=StringIO())
    assert relationship_snapshot() == converted
    shares = active_relationship_model().objects.filter(resource_type="knowledge/page", relation="viewer")
    assert set(shares.values_list("resource_id", flat=True)) == {str(root.pk), str(second.pk)}
    path, current = old_page_policy
    path.write_text(current)
    call_command("rebac", "sync", force_overwrite=True, yes=True, verbosity=0)
    assert Page.objects.migrate_attribution(apply=True) == ()
    output = StringIO()
    call_command("migrate_page_attribution", "--check", stdout=output)
    assert output.getvalue().strip() == "migrate_page_attribution: nothing pending."
    call_command("migrate", interactive=False, verbosity=0, stdout=StringIO())
    assert relationship_snapshot() == converted
    for page in (root, child, second):
        assert page.with_actor(author).has_access("read")
        assert not page.with_actor(author).has_access("write")
        assert not page.with_actor(author).has_access("delete")
    assert not excluded.with_actor(author).has_access("read")
    with actor_context(owner):
        Page.objects.get(pk=root.pk).revoke_record_access("viewer", author)
    assert not root.with_actor(author).has_access("read")
    assert not child.with_actor(author).has_access("read")


def test_vault_selection_limits_preview_and_apply_without_duplicate_shares(attributed_pages):
    _, _, (root, child, second, excluded, _) = attributed_pages
    before = relationship_snapshot()
    output = StringIO()
    call_command("migrate_page_attribution", "--check", "--vault", str(root.vault.sqid), stdout=output)
    assert str(root.sqid) in output.getvalue() and str(child.sqid) in output.getvalue()
    assert str(second.sqid) not in output.getvalue() and str(excluded.sqid) not in output.getvalue()
    assert relationship_snapshot() == before
    call_command(
        "migrate_page_attribution",
        "--apply",
        "--vault",
        str(root.vault.sqid),
        "--vault",
        str(root.vault.sqid),
        stdout=StringIO(),
    )
    after = relationship_snapshot()
    assert after != before
    call_command("migrate_page_attribution", "--apply", "--vault", str(root.vault.sqid), stdout=StringIO())
    assert relationship_snapshot() == after
    assert {entry.pk for entry in Page.objects.migrate_attribution() if entry.grants_viewer} == {second.pk}


def test_conversion_failure_aborts_post_migrate_and_rolls_back_all_shares(attributed_pages, monkeypatch):
    before = relationship_snapshot()
    write = knowledge_models.write_relationships

    def fail_after_write(shares):
        write(shares)
        raise RuntimeError("Conversion write failed")

    monkeypatch.setattr(knowledge_models, "write_relationships", fail_after_write)
    connect()
    with pytest.raises(RuntimeError, match="Conversion write failed"):
        call_command("migrate", interactive=False, verbosity=0, stdout=StringIO())
    assert relationship_snapshot() == before
    assert sum(entry.grants_viewer for entry in Page.objects.migrate_attribution()) == 2
