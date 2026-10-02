"""Vault copies preserve content, attribution, replay receipts and bounded work."""

import pytest
import reversion
from django.contrib.auth.models import AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import PermissionDenied, actor_context, system_context
from rebac.models import active_relationship_model

from angee.base.mixins import CreationKeyConflict
from angee.knowledge.models import VaultQuerySet
from tests.conftest import Link, MarkdownPage, Page, RecordBinding, Vault, create_platform_admin, create_user
from tests.knowledge_campaign import clone_template as clone_template
from tests.test_knowledge import _grant


@pytest.mark.parametrize("owned", [True, False], ids=["owned", "ownerless"])
def test_clone_copies_complete_independent_tree_bodies_links_and_attribution(clone_template, owned):
    fixture = clone_template
    with system_context(reason="test.template.binding"):
        binding = RecordBinding(page=fixture.note, content_type=ContentType.objects.get_for_model(Vault),
                                object_id=fixture.source.pk)
        binding.save()
        RecordBinding(
            vault=fixture.source, content_type=ContentType.objects.get_for_model(Vault), object_id=fixture.source.pk,
        ).save()
    with actor_context(fixture.actor), reversion.create_revision():
        clone = Vault.objects.create_from(fixture.source, name="Copy", owned=owned)
    assert clone.owner_id == (fixture.actor.pk if owned else None)
    assert clone.pk != fixture.source.pk
    for field in ("description", "icon", "accent", "retrieval_class"):
        assert getattr(clone, field) == getattr(fixture.source, field)
    assert clone.created_by_id == clone.updated_by_id == fixture.actor.pk
    with system_context(reason="test.clone.content"):
        assert clone.history.count() == 1
        source_pages = {page.title: page for page in Page.objects.filter(vault=fixture.source)}
        copies = {page.title: page for page in Page.objects.filter(vault=clone)}
        assert copies.keys() == source_pages.keys()
        source_by_id = {page.pk: page for page in source_pages.values()}
        for title, page in copies.items():
            original = source_pages[title]
            assert page.pk != original.pk
            assert page.sqid != original.sqid
            assert (page.kind, page.icon) == (original.kind, original.icon)
            assert page.created_by_id == page.updated_by_id == fixture.actor.pk
            assert page.history.count() == 1
            assert page.created_at is not None and page.updated_at is not None
            assert page.parent_id == (
                copies[source_by_id[original.parent_id].title].pk if original.parent_id else None
            )
        originals = {body.pk: body for body in MarkdownPage.objects.filter(vault=fixture.source)}
        bodies = list(MarkdownPage.objects.filter(vault=clone))
        assert len(bodies) == 2
        for body in bodies:
            original = originals[source_pages[body.title].pk]
            assert body.pk != original.pk
            assert (body.body, body.body_hash, body.word_count) == (
                original.body, original.body_hash, original.word_count,
            )
            assert body.created_by_id == body.updated_by_id == fixture.actor.pk
            assert body.revisions.count() == 1
        links = {link.target_text: link for link in Link.objects.filter(source_page=copies["Guide"])}
        assert links.keys() == {"Outline", "Guide", "Missing"}
        assert links["Outline"].target_page_id == copies["Outline"].pk
        # The shared indexer deliberately leaves self references unresolved.
        assert links["Guide"].target_page_id is None
        assert not links["Guide"].is_resolved
        assert links["Missing"].target_page_id is None
        assert not links["Missing"].is_resolved
        assert not RecordBinding.objects.filter(vault=clone).exists()
        assert not RecordBinding.objects.filter(page__vault=clone).exists()
        assert RecordBinding.objects.count() == 2
        relationships = active_relationship_model().objects
        assert not relationships.filter(resource_type="knowledge/vault", resource_id=str(clone.pk)).exists()
        assert not relationships.filter(
            resource_type="knowledge/page", resource_id__in=[str(page.pk) for page in copies.values()],
        ).exists()
        MarkdownPage.objects.write_body(copies["Guide"], "Changed copy")
        fixture.note.refresh_from_db()
        assert MarkdownPage.objects.get(pk=fixture.note.pk).body == originals[fixture.note.pk].body
        MarkdownPage.objects.write_body(fixture.note, "Changed template")
        assert MarkdownPage.objects.get(pk=copies["Guide"].pk).body == "Changed copy"
    assert not Vault.objects.as_user(fixture.reader).filter(pk=clone.pk).exists()


def test_ownerless_clone_is_private_until_a_grant_reaches_it(clone_template):
    fixture = clone_template
    admin = create_platform_admin("clone-admin")
    with actor_context(fixture.actor):
        clone = Vault.objects.create_from(fixture.source, name="Ownerless", owned=False)
    assert not Vault.objects.as_user(fixture.actor).filter(pk=clone.pk).exists()
    assert not Vault.objects.as_user(fixture.reader).filter(pk=clone.pk).exists()
    assert Vault.objects.as_user(admin).filter(pk=clone.pk).exists()
    with actor_context(fixture.actor), pytest.raises(PermissionDenied):
        clone.save(update_fields=("name",))
    _grant(clone, "viewer", fixture.reader)
    assert Vault.objects.as_user(fixture.reader).filter(pk=clone.pk).exists()


def test_transfer_and_clear_preserve_creator_but_end_former_owner_access(clone_template):
    fixture = clone_template
    with actor_context(fixture.actor):
        clone = Vault.objects.create_from(fixture.source, name="Transferable")
        clone.transfer_ownership(fixture.reader)
    assert clone.owner_id == fixture.reader.pk
    assert clone.created_by_id == fixture.actor.pk
    assert not Vault.objects.as_user(fixture.actor).filter(pk=clone.pk).exists()
    with actor_context(fixture.actor), pytest.raises(PermissionDenied):
        clone.transfer_ownership(None)
    with actor_context(fixture.reader):
        clone.as_user(fixture.reader).transfer_ownership(None)
    clone.refresh_from_db()
    assert clone.owner_id is None
    assert clone.created_by_id == fixture.actor.pk
    assert not Vault.objects.as_user(fixture.reader).filter(pk=clone.pk).exists()


def test_deleting_owner_retains_the_vault_and_clears_ownership(clone_template):
    fixture = clone_template
    with actor_context(fixture.actor):
        clone = Vault.objects.create_from(fixture.source, name="Surviving vault")
    with system_context(reason="test.owner_deletion"):
        fixture.actor.delete()
        stored = Vault.objects.get(pk=clone.pk)
        assert stored.owner_id is None
        assert Page.objects.filter(vault=stored).count() == 4


@pytest.mark.parametrize("owned", [True, False])
@pytest.mark.parametrize("unavailable", ["unshared", "deleted"])
def test_exact_replay_survives_unreadable_or_deleted_template_and_changed_ownership(
    clone_template, owned, unavailable,
):
    fixture = clone_template
    with actor_context(fixture.actor):
        clone = Vault.objects.create_from(fixture.source, name="Replay", owned=owned, client_creation_key="receipt")
        if owned:
            clone.transfer_ownership(fixture.reader)
    if owned:
        with actor_context(fixture.reader):
            clone.as_user(fixture.reader).transfer_ownership(None)
    template_identity = Vault(pk=fixture.source.pk)
    with system_context(reason="test.template.unavailable"):
        if unavailable == "deleted":
            fixture.source.delete()
        else:
            active_relationship_model().objects.filter(
                resource_type="knowledge/vault", resource_id=str(fixture.source.pk), relation="viewer",
            ).delete()
        before = Vault.objects.count(), Page.objects.count(), MarkdownPage.objects.count()
    with actor_context(fixture.actor):
        replay = Vault.objects.create_from(template_identity, name="Replay", owned=owned, client_creation_key="receipt")
        assert replay.pk == clone.pk
        assert replay.owner_id is None
        with pytest.raises(Vault.DoesNotExist):
            Vault.objects.create_from(template_identity, name="New", owned=owned, client_creation_key="new")
    with system_context(reason="test.replay.counts"):
        assert (Vault.objects.count(), Page.objects.count(), MarkdownPage.objects.count()) == before


@pytest.mark.parametrize("changed", ["name", "template", "owned"])
def test_reusing_key_with_changed_request_conflicts_even_after_template_deletion(clone_template, changed):
    fixture = clone_template
    with actor_context(fixture.actor):
        clone = Vault.objects.create_from(fixture.source, name="Replay", client_creation_key="receipt")
    identity = Vault(pk=fixture.source.pk)
    with system_context(reason="test.template.delete"):
        fixture.source.delete()
    request = {"template": identity, "name": "Replay", "owned": True, "client_creation_key": "receipt"}
    request[changed] = {"name": "Changed", "template": Vault(pk=clone.pk), "owned": False}[changed]
    with actor_context(fixture.actor), pytest.raises(CreationKeyConflict):
        Vault.objects.create_from(**request)
    assert Vault.objects.as_user(fixture.actor).count() == 1


def test_template_edits_do_not_change_completed_replay_content(clone_template):
    fixture = clone_template
    with actor_context(fixture.actor):
        clone = Vault.objects.create_from(fixture.source, name="Replay", client_creation_key="receipt")
    with actor_context(fixture.author):
        MarkdownPage.objects.write_body(fixture.note, "A later version")
    with actor_context(fixture.actor):
        assert Vault.objects.create_from(fixture.source, name="Replay", client_creation_key="receipt").pk == clone.pk
        assert MarkdownPage.objects.get(vault=clone, title="Guide").body.startswith("# Guide")


def test_creation_key_is_private_to_creating_actor(clone_template):
    fixture = clone_template
    with actor_context(fixture.actor):
        first = Vault.objects.create_from(fixture.source, name="Replay", client_creation_key="receipt")
    with actor_context(fixture.reader):
        second = Vault.objects.create_from(fixture.source, name="Replay", client_creation_key="receipt")
    assert second.pk != first.pk
    assert second.created_by_id == fixture.reader.pk
    stranger = create_user("no-template-access")
    with actor_context(stranger), pytest.raises(Vault.DoesNotExist):
        Vault.objects.create_from(fixture.source, name="Replay", client_creation_key="receipt")


@pytest.mark.parametrize("constraint_path", ["validation", "database"])
def test_forced_replay_miss_converges_on_committed_winner(clone_template, monkeypatch, constraint_path):
    fixture = clone_template
    with actor_context(fixture.actor):
        winner = Vault.objects.create_from(fixture.source, name="Race", client_creation_key="race")
    original = VaultQuerySet.for_creation_key
    lookups = []

    def miss_once(queryset, scope, key, fingerprint):
        lookups.append(key)
        return None if len(lookups) == 1 else original(queryset, scope, key, fingerprint)

    monkeypatch.setattr(VaultQuerySet, "for_creation_key", miss_once)
    if constraint_path == "database":
        # Model validation already succeeded before a concurrent winner committed.
        # Leave the actual insert and database uniqueness constraint intact.
        clean = Vault.full_clean

        def already_validated(instance, *args, **kwargs):
            return clean(instance, *args, **kwargs, validate_unique=False, validate_constraints=False)

        monkeypatch.setattr(Vault, "full_clean", already_validated)
    with actor_context(fixture.actor):
        replay = Vault.objects.create_from(fixture.source, name="Race", client_creation_key="race")
    assert replay.pk == winner.pk
    assert lookups == ["race", "race"]
    with system_context(reason="test.race.counts"):
        assert Vault.objects.filter(client_creation_key="race").count() == 1
        assert Page.objects.filter(vault=winner).count() == 4
        assert MarkdownPage.objects.filter(vault=winner).count() == 2


@pytest.mark.parametrize("invalid", ["kind", "cycle", "foreign-parent"])
def test_invalid_template_rolls_back_clone_and_its_receipt(clone_template, invalid):
    fixture = clone_template
    with system_context(reason="test.invalid_template"):
        if invalid == "kind":
            # Simulate stored corruption; the closed enum rejects this value
            # before SQL through every ordinary write path.
            table = connection.ops.quote_name(MarkdownPage._meta.db_table)
            column = connection.ops.quote_name(MarkdownPage._meta.pk.column)
            with connection.cursor() as cursor:
                cursor.execute(f"UPDATE {table} SET kind = %s WHERE {column} = %s", ["external", fixture.note.pk])
        elif invalid == "cycle":
            Page.objects.filter(pk=fixture.note.pk).update(parent=fixture.note)
        else:
            foreign = Vault.objects.create(name="Foreign", owner=None)
            parent = Page.objects.create(vault=foreign, title="Foreign parent")
            Page.objects.filter(pk=fixture.note.pk).update(parent=parent)
        before = Vault.objects.count(), Page.objects.count(), MarkdownPage.objects.count()
    with actor_context(fixture.actor), pytest.raises(ValidationError):
        Vault.objects.create_from(fixture.source, name="Invalid", client_creation_key="invalid")
    with system_context(reason="test.invalid_clone.rollback"):
        assert (Vault.objects.count(), Page.objects.count(), MarkdownPage.objects.count()) == before
        assert not Vault.objects.filter(client_creation_key="invalid").exists()


def test_anonymous_actor_cannot_clone(clone_template):
    with actor_context(AnonymousUser()), pytest.raises(PermissionDenied):
        Vault.objects.create_from(clone_template.source, name="Anonymous")


def test_empty_creation_key_fails_before_a_clone_is_written(clone_template):
    fixture = clone_template
    with actor_context(fixture.actor), pytest.raises(ValidationError) as raised:
        Vault.objects.create_from(fixture.source, name="Empty key", client_creation_key="")
    assert "client_creation_key" in raised.value.message_dict
    with system_context(reason="test.clone.empty_key"):
        assert not Vault.objects.filter(name="Empty key").exists()


def test_clone_query_growth_is_bounded_for_five_and_sixty_pages(composed_tables, record_property):
    """Cloning issues one child insert per markdown page and no other per-page statement.

    Django's ``bulk_create`` refuses multi-table children, so each markdown body is its
    own INSERT; everything else may only cross a few native batch boundaries.
    """

    author, actor = create_user("budget-author"), create_user("budget-actor")
    child_insert = f"INSERT INTO {connection.ops.quote_name(MarkdownPage._meta.db_table)}"
    counts = []
    for size in (5, 60):
        with actor_context(author), system_context(reason="test.clone.budget.template_setup"):
            source = Vault.objects.create_for(author, name=f"Template {size}")
            pages = []
            for index in range(size):
                parent = pages[(index - 1) // 3] if index else None
                page = Page.objects.create_in(source, title=f"Page {index}", parent=parent)
                MarkdownPage.objects.write_body(page, f"[[Page 0]] [[Page {index}]] [[Missing]]")
                pages.append(page)
        _grant(source, "viewer", actor)
        with actor_context(actor), CaptureQueriesContext(connection) as captured, reversion.create_revision():
            clone = Vault.objects.create_from(source, name=f"Copy {size}")
        child_inserts = sum(query["sql"].startswith(child_insert) for query in captured.captured_queries)
        assert child_inserts == size
        counts.append(len(captured) - child_inserts)
        record_property(f"clone_queries_{size}", len(captured))
        with system_context(reason="test.clone.budget.contents"):
            assert Page.objects.filter(vault=clone).count() == size
            assert MarkdownPage.objects.filter(vault=clone).count() == size
            assert Page.history.filter(vault_id=clone.pk).count() == size
    assert counts[1] - counts[0] <= 6, counts


def test_clone_page_tree_queries_do_not_grow_with_depth(composed_tables, record_property):
    """The same page rows cost the same in a shallow tree and a deep chain."""

    actor = create_user("tree-depth-owner")
    counts = []
    for depth in (2, 30):
        with actor_context(actor), system_context(reason="test.clone.depth.setup"):
            source = Vault.objects.create_for(actor, name=f"Depth {depth}")
            pages = []
            for index in range(30):
                parent = pages[-1 if depth == 30 else 0] if pages else None
                pages.append(Page.objects.create_in(
                    source, title=f"Folder {index}", parent=parent, kind=Page.PageKind.FOLDER,
                ))
        with actor_context(actor), CaptureQueriesContext(connection) as captured:
            clone = Vault.objects.create_from(source, name=f"Copied depth {depth}")
        counts.append(len(captured))
        record_property(f"clone_queries_depth_{depth}", len(captured))
        with system_context(reason="test.clone.depth.contents"):
            copies = {page.title: page for page in Page.objects.filter(vault=clone)}
            history = {row.id: row for row in Page.history.filter(vault_id=clone.pk)}
            assert len(copies) == len(history) == 30
            for original in pages:
                copied = copies[original.title]
                parent = next((page for page in pages if page.pk == original.parent_id), None)
                assert copied.parent_id == (copies[parent.title].pk if parent else None)
                assert history[copied.pk].parent_id == copied.parent_id
    assert counts[0] == counts[1], counts
