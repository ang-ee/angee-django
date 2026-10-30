"""Notion transport and native stream-to-Knowledge outcomes, without external calls."""

from __future__ import annotations

import json
from datetime import timedelta
from uuid import UUID

import httpx2
import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.db import connection
from rebac import actor_context, system_context

from angee.integrate.http import HttpClient
from angee.integrate.states import SyncFailureKind
from angee.integrate.streams import advance_stream, open_stream, sync_bridge
from angee.knowledge_integrate_notion.backend import NotionBackend, NotionResponseError
from angee.knowledge_integrate_notion.schema import schemas
from tests.conftest import (
    Link,
    MarkdownPage,
    NotionConnection,
    Page,
    addon_schema,
    create_user,
    execute_schema,
    make_integration,
)


def node(number, title, *, parent=None, source=False):
    identity = str(UUID(int=number))
    result = {
        "object": "data_source" if source else "page",
        "id": identity,
        "last_edited_time": "2026-10-26T12:00:00Z",
        "parent": {"type": "workspace", "workspace": True} if parent is None else parent,
        "properties": {"Custom title": {"type": "title", "title": [{"plain_text": title}]}},
    }
    if source:
        result["title"] = [{"plain_text": title}]
    return result


class NotionAPI:
    """A paginated provider fixture; the real transport boundary and driver stay in use."""

    def __init__(self):
        self.nodes = {}
        self.bodies = {}
        self.search = []
        self.sources = {}
        self.requests = []
        self.refusals = {}

    def add(self, value, body="", *, searchable=True):
        self.nodes[value["id"]] = value
        self.bodies[value["id"]] = body
        if searchable:
            self.search.append(value["id"])
        return value["id"]

    def request(self, method, url, **kwargs):
        assert not connection.in_atomic_block, "Provider calls must precede the apply transaction"
        assert kwargs["headers"]["Authorization"] == "Bearer x"
        path = url.removeprefix("https://api.notion.com/v1/")
        self.requests.append((method, path))
        if path in self.refusals:
            return self.refusals[path]
        if path == "search" or path.endswith("/query"):
            payload = json.loads(kwargs["body"])
            keys = self.search if path == "search" else self.sources[path.split("/")[1]]
            start, size = int(payload.get("start_cursor", 0)), payload["page_size"]
            end = start + size
            return httpx2.Response(
                200,
                json={
                    "results": [self.nodes[key] for key in keys[start:end]],
                    "has_more": end < len(keys),
                    "next_cursor": str(end) if end < len(keys) else None,
                },
            )
        key = path.split("/")[1]
        if path.endswith("/markdown"):
            return httpx2.Response(
                200,
                json={
                    "object": "page_markdown",
                    "id": key,
                    "markdown": self.bodies[key],
                    "truncated": False,
                    "unknown_block_ids": [],
                },
            )
        return httpx2.Response(200, json=self.nodes[key])


@pytest.fixture
def api(monkeypatch):
    provider = NotionAPI()
    monkeypatch.setattr(HttpClient, "request", lambda self, *args, **kwargs: provider.request(*args, **kwargs))
    return provider


@pytest.fixture
def bridge(composed_tables):
    with system_context(reason="test.notion.sync"):
        yield make_integration("notion-test", model=NotionConnection)


def targets(bridge):
    return {
        link.external_key: Page.objects.select_related("markdown").get(pk=link.target_object_id)
        for link in bridge.sync_streams.get().links.filter(target_object_id__isnull=False)
    }


def test_import_replay_database_rows_and_late_parents(bridge, api):
    parent = node(20, "Parent")
    repeated = "Repeated" * 80
    source_body = '# Body\n\n<span color="red">[[Parent]]</span>'
    child_id = api.add(node(10, repeated, parent={"type": "page_id", "page_id": parent["id"]}), source_body)
    parent_id = api.add(parent, "Root")
    other_id = api.add(node(30, repeated), "Another")
    database_id = api.add(node(40, "Tasks", source=True))
    hidden_id = api.add(
        node(50, "Unindexed", parent={"type": "data_source_id", "data_source_id": database_id}),
        "Database page",
        searchable=False,
    )
    api.sources[database_id] = [hidden_id]
    bridge.config = {"sync_page_bound": 2}
    bridge.save(update_fields=["config"])

    assert sync_bridge(bridge) >= 5
    rows = targets(bridge)
    assert len(rows) == 5
    assert rows[child_id].parent_id == rows[parent_id].pk
    assert rows[hidden_id].parent_id == rows[database_id].pk
    assert rows[database_id].kind == "folder"
    assert rows[child_id].markdown.body == "# Body\n\n[[Parent]]"
    child_link = bridge.sync_streams.get().links.get(external_key=child_id)
    assert child_link.source_payload["content"]["markdown"] == source_body
    assert rows[child_id].title != rows[other_id].title
    assert len(rows[other_id].title) <= Page._meta.get_field("title").max_length
    assert rows[hidden_id].markdown.word_count == 2
    assert bridge.vault.owner_id == bridge.owner_id
    before = {key: (row.pk, row.updated_at, row.parent_id) for key, row in rows.items()}

    api.requests.clear()
    assert sync_bridge(bridge) == 0
    assert not any(path.endswith("/markdown") for _, path in api.requests)
    assert {key: (row.pk, row.updated_at, row.parent_id) for key, row in targets(bridge).items()} == before
    assert bridge.sync_streams.get().cursor == {}


def test_remote_update_uses_body_history_and_local_edits_become_conflicts(bridge, api):
    key = api.add(node(1, "Original"), "First")
    sync_bridge(bridge)
    original = targets(bridge)[key]
    api.nodes[key]["properties"]["Custom title"]["title"][0]["plain_text"] = "Renamed"
    api.bodies[key] = "Second"
    api.nodes[key]["last_edited_time"] = "2026-10-26T13:00:00Z"
    assert sync_bridge(bridge) == 1
    updated = targets(bridge)[key]
    assert updated.pk == original.pk
    assert (updated.title, updated.markdown.body) == ("Renamed", "Second")
    assert MarkdownPage.history.filter(id=updated.pk, body="First").exists()
    with actor_context(bridge.owner):
        MarkdownPage.objects.write_body(updated, "Local edit")
    api.bodies[key] = "Remote edit"
    api.nodes[key]["last_edited_time"] = "2026-10-26T14:00:00Z"
    sync_bridge(bridge)
    assert targets(bridge)[key].markdown.body == "Local edit"
    link = bridge.sync_streams.get().links.get(external_key=key)
    failure = next(failure for failure in link.failures(open_only=True) if failure.kind == SyncFailureKind.CONFLICT)
    failure.resolve_conflict(keep="remote")
    assert targets(bridge)[key].markdown.body == "Remote edit"
    assert not bridge.sync_streams.get().links.get(external_key=key).failures(open_only=True)


def test_link_rewriting_resolves_forward_references_and_remaps_saved_option(bridge, api):
    first_id, second_id = str(UUID(int=1)), str(UUID(int=2))
    first_body = f'[Next **page**](https://notion.so/{second_id})'
    api.add(node(1, "First"), first_body)
    api.add(node(2, "Second # [page]"), f'<mention-page url="https://notion.so/{first_id}">Back</mention-page>')
    bridge.config = {"sync_page_bound": 1}
    bridge.save(update_fields=["config"])
    sync_bridge(bridge)
    rows = targets(bridge)
    identities = {key: row.pk for key, row in rows.items()}
    assert rows[first_id].markdown.body == f'[Next **page**](<{rows[second_id].wikilink_url}>)'
    assert rows[second_id].markdown.body == f'[Back](<{rows[first_id].wikilink_url}>)'
    assert Link.objects.get(source_page=rows[first_id]).target_page_id == rows[second_id].pk
    assert sync_bridge(bridge) == 0

    bridge.rewrite_page_links = False
    bridge.save(update_fields=["rewrite_page_links"])
    assert sync_bridge(bridge) == 2
    assert targets(bridge)[first_id].markdown.body == first_body
    assert sync_bridge(bridge) == 0
    bridge.rewrite_page_links = True
    bridge.save(update_fields=["rewrite_page_links"])
    assert sync_bridge(bridge) == 2
    assert {key: row.pk for key, row in targets(bridge).items()} == identities
    assert sync_bridge(bridge) == 0


def test_late_target_and_rename_refresh_links_without_overwriting_local_edits(bridge, api):
    remote_target = str(UUID(int=2))
    original = f'[Later](https://notion.so/{remote_target})'
    source = api.add(node(1, "Source"), original)
    sync_bridge(bridge)
    assert targets(bridge)[source].markdown.body == original
    api.add(node(2, "Target"), "Body")
    sync_bridge(bridge)
    assert "wikilink:Target" in targets(bridge)[source].markdown.body
    api.nodes[remote_target]["properties"]["Custom title"]["title"][0]["plain_text"] = "Renamed"
    sync_bridge(bridge)
    assert "wikilink:Renamed" in targets(bridge)[source].markdown.body
    assert sync_bridge(bridge) == 0

    with actor_context(bridge.owner):
        MarkdownPage.objects.write_body(targets(bridge)[source], "Local edit")
    bridge.rewrite_page_links = False
    bridge.save(update_fields=["rewrite_page_links"])
    sync_bridge(bridge)
    assert targets(bridge)[source].markdown.body == "Local edit"
    link = bridge.sync_streams.get().links.get(external_key=source)
    assert any(failure.kind == SyncFailureKind.CONFLICT for failure in link.failures(open_only=True))


@pytest.mark.parametrize(("truncated", "markdown", "code"), [
    (True, "partial", "notion_incomplete_markdown"),
    (False, "<custom-block/>", "notion_unsupported_markup"),
])
def test_incomplete_content_is_quarantined_without_overwriting_good_body(bridge, api, truncated, markdown, code):
    key = api.add(node(1, "Complete"), "Keep this")
    sync_bridge(bridge)
    api.nodes[key]["last_edited_time"] = "2026-10-26T13:00:00Z"
    api.refusals[f"pages/{key}/markdown"] = httpx2.Response(
        200,
        json={
            "object": "page_markdown",
            "id": key,
            "markdown": markdown,
            "truncated": truncated,
            "unknown_block_ids": [str(UUID(int=9))] if truncated else [],
        },
    )
    api.add(node(2, "Healthy"), "Arrives")
    sync_bridge(bridge)
    assert targets(bridge)[key].markdown.body == "Keep this"
    assert Page.objects.filter(vault=bridge.vault, title="Healthy").exists()
    link = bridge.sync_streams.get().links.get(external_key=key)
    failure = link.failures(open_only=True)[0]
    assert failure.code == code
    assert failure.source_label == "Complete"
    assert failure.source_url == f"https://www.notion.so/{UUID(key).hex}"
    assert failure.message
    assert bridge.open_failure_count == 1


def test_rate_limit_preserves_cursor_and_honors_retry_after(bridge, api):
    key = api.add(node(1, "Page"), "Body")
    backend = NotionBackend(bridge)
    definition = tuple(backend.streams())[0]
    stream = open_stream(bridge, definition.key, definition.partition, backend, definition=definition)
    api.refusals[f"pages/{key}/markdown"] = httpx2.Response(429, headers={"Retry-After": "12"})
    with pytest.raises(NotionResponseError) as error:
        advance_stream(stream, backend)
    assert error.value.retry_after == timedelta(seconds=12)
    stream.refresh_from_db()
    assert stream.cursor == {}
    assert not stream.links.exists()
    api.refusals.clear()
    assert sync_bridge(bridge) == 1


def test_rate_limit_after_progress_commits_completed_pages(bridge, api):
    first = api.add(node(1, "First"), "Imported once")
    second = api.add(node(2, "Second"), "After retry")
    backend = NotionBackend(bridge)
    definition = tuple(backend.streams())[0]
    stream = open_stream(bridge, definition.key, definition.partition, backend, definition=definition)
    api.refusals[f"pages/{second}/markdown"] = httpx2.Response(429, headers={"Retry-After": "1"})
    assert advance_stream(stream, backend).count == 1
    stream.refresh_from_db()
    assert len(stream.cursor["pending"]) == 1
    with pytest.raises(NotionResponseError):
        advance_stream(stream, backend)
    api.refusals.clear()
    assert sync_bridge(bridge) == 1
    assert api.requests.count(("GET", f"pages/{first}/markdown")) == 1
    assert len(targets(bridge)) == 2


@pytest.mark.parametrize("status", [403, 404])
def test_unavailable_page_does_not_block_neighbors_or_erase_prior_content(bridge, api, status):
    key = api.add(node(1, "Private"), "Keep this")
    sync_bridge(bridge)
    api.nodes[key]["last_edited_time"] = "2026-10-26T13:00:00Z"
    api.refusals[f"pages/{key}/markdown"] = httpx2.Response(status)
    api.add(node(2, "Healthy"), "Arrives")
    sync_bridge(bridge)
    assert targets(bridge)[key].markdown.body == "Keep this"
    assert len(targets(bridge)) == 2
    link = bridge.sync_streams.get().links.get(external_key=key)
    assert link.failures(open_only=True)[0].code == "notion_page_unavailable"
    api.refusals.clear()
    api.bodies[key] = "Available again"
    sync_bridge(bridge)
    assert targets(bridge)[key].markdown.body == "Available again"


def test_unavailable_database_does_not_block_later_databases(bridge, api):
    missing = api.add(node(1, "Lost access", source=True))
    healthy = api.add(node(2, "Available", source=True))
    child = api.add(node(3, "Row"), "Imported", searchable=False)
    api.sources[healthy] = [child]
    api.refusals[f"data_sources/{missing}/query"] = httpx2.Response(404)
    sync_bridge(bridge)
    assert targets(bridge)[child].markdown.body == "Imported"
    link = bridge.sync_streams.get().links.get(external_key=missing)
    assert link.failures(open_only=True)[0].code == "notion_page_unavailable"


def test_disappearing_search_results_do_not_delete_imports(bridge, api):
    key = api.add(node(1, "Retained"), "Still here")
    sync_bridge(bridge)
    api.search.clear()
    assert sync_bridge(bridge) == 0
    assert targets(bridge)[key].markdown.body == "Still here"


def test_known_databases_keep_syncing_when_search_omits_them(bridge, api):
    source = api.add(node(1, "Database", source=True))
    api.sources[source] = []
    sync_bridge(bridge)
    api.search.clear()
    child = api.add(
        node(2, "Unindexed row", parent={"type": "data_source_id", "data_source_id": source}),
        "Found through the known database",
        searchable=False,
    )
    api.sources[source] = [child]
    assert sync_bridge(bridge) == 1
    assert targets(bridge)[child].parent_id == targets(bridge)[source].pk


def test_remote_trash_retains_knowledge_and_can_be_restored(bridge, api):
    key = api.add(node(1, "Retained"), "Keep this")
    sync_bridge(bridge)
    before = targets(bridge)[key]
    api.nodes[key]["in_trash"] = True
    assert sync_bridge(bridge) == 1
    link = bridge.sync_streams.get().links.get(external_key=key)
    assert link.applied_base.source_hash == ""
    assert link.applied_base.tombstoned_at is not None
    assert targets(bridge)[key].markdown.body == "Keep this"
    assert targets(bridge)[key].updated_at == before.updated_at
    assert sync_bridge(bridge) == 0
    api.nodes[key]["in_trash"] = False
    api.bodies[key] = "Restored"
    assert sync_bridge(bridge) == 1
    assert targets(bridge)[key].pk == before.pk
    assert targets(bridge)[key].markdown.body == "Restored"


def test_deadline_keeps_pending_records_even_after_final_listing(bridge, api):
    for number in range(1, 4):
        api.add(node(number, f"Page {number}"), f"Body {number}")
    backend = NotionBackend(bridge)
    definition = tuple(backend.streams())[0]
    stream = open_stream(bridge, definition.key, definition.partition, backend, definition=definition)
    first = advance_stream(stream, backend, deadline=0)
    assert first.count == 1
    assert len(first.stream.cursor["pending"]) == 2
    assert sync_bridge(bridge) == 2
    assert len(targets(bridge)) == 3


def test_vault_is_private_and_a_transferred_connection_cannot_write_it(bridge, api):
    author = {"object": "user", "id": str(UUID(int=99))}
    value = {**node(1, "Private"), "created_by": author, "last_edited_by": author}
    key = api.add(value, "Private content")
    sync_bridge(bridge)
    link = bridge.sync_streams.get().links.get(external_key=key)
    assert link.source_payload["node"]["created_by"]["id"] == author["id"]
    assert link.source_payload["node"]["last_edited_by"]["id"] == author["id"]
    assert targets(bridge)[key].created_by_id == bridge.owner_id
    outsider = create_user("outsider")
    with actor_context(outsider):
        assert not Page.objects.with_actor(outsider).filter(vault_id=bridge.vault_id).exists()
    bridge.owner = outsider
    bridge.save(update_fields=["owner"])
    with pytest.raises((PermissionDenied, ObjectDoesNotExist)):
        sync_bridge(bridge)


def test_sync_action_requires_write_access(composed_tables, monkeypatch):
    with system_context(reason="test.notion.action.setup"):
        connection = make_integration("notion-action", model=NotionConnection)
    schema = addon_schema(schemas, "console")
    queued = []
    monkeypatch.setattr("angee.knowledge_integrate_notion.schema.queue_bridge_sync", queued.append)
    mutation = "mutation Sync($id: ID!) { sync_notion_connection(id: $id) { ok } }"
    variables = {"id": str(connection.sqid)}
    for user in (AnonymousUser(), create_user("notion-outsider")):
        result = execute_schema(schema, mutation, variables, user=user)
        assert result.errors or not result.data["sync_notion_connection"]["ok"]
        assert not queued
    result = execute_schema(schema, mutation, variables, user=connection.owner)
    assert result.errors is None
    assert result.data["sync_notion_connection"]["ok"]
    assert [row.pk for row in queued] == [connection.pk]


def test_rewrite_converts_original_markup_once(bridge, api):
    destination = api.add(node(2, "Destination"), "Target")
    source = api.add(node(1, "Source"),
        f'<unknown url="https://example.com" alt="a &lt;b&gt; c"/> '
        f'<page url="https://notion.so/{destination}">Target</page>')
    sync_bridge(bridge)
    assert bridge.sync_streams.get().cursor == {}
    assert "a <b> c" in targets(bridge)[source].markdown.body
