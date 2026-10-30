"""Notion's paginated, read-only replica over the shared stream protocol."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from time import monotonic
from typing import Any, Literal, cast
from uuid import UUID

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist
from pydantic import BaseModel, Field, ValidationError
from rebac import actor_context

from angee.base.serialization import canonical_json_sha256
from angee.integrate.credentials import CredentialKind
from angee.integrate.discovery import ConnectionDiscovery
from angee.integrate.errors import IntegrationError
from angee.integrate.http import HttpClientMixin
from angee.integrate.impl import BridgeImpl
from angee.integrate.states import StreamDefinition, StreamKind, SyncFailureKind
from angee.integrate.streams import ApplyResult, CursorInvalid, RecordChange, SemanticError, StreamPage
from angee.knowledge.models import Page
from angee.knowledge_integrate_notion.markdown import NotionMarkdownConverter


class NotionUser(BaseModel):
    """The public identity returned by Notion's GET /v1/users/me."""

    object: Literal["user"]
    id: UUID
    name: str | None = None


class NotionText(BaseModel):
    """The text projection provided by Notion's rich-text owner."""

    plain_text: str


class NotionParent(BaseModel):
    """Only page and data-source parents have imported knowledge counterparts."""

    type: str
    page_id: UUID | None = None
    data_source_id: UUID | None = None

    @property
    def key(self) -> str:
        """Return the provider identity of the containing knowledge node."""
        return str(self.page_id or self.data_source_id or "")


class NotionNode(BaseModel):
    """Provider-owned page identity; data sources become knowledge folders."""

    object: Literal["page", "data_source"]
    id: UUID
    last_edited_time: datetime
    created_by: NotionUser | None = None
    last_edited_by: NotionUser | None = None
    parent: NotionParent
    database_parent: NotionParent | None = None
    title: list[NotionText] = Field(default_factory=list)
    properties: dict[str, dict[str, Any]] = Field(default_factory=dict)
    icon: dict[str, Any] | None = None
    archived: bool = False
    in_trash: bool = False

    @property
    def is_trashed(self) -> bool:
        """Normalize Notion's current and legacy deletion flags once."""
        return self.archived or self.in_trash

    @property
    def label(self) -> str:
        """Read the declared title property without assuming its user-chosen name."""
        title = self.title
        if self.object == "page":
            title = [
                NotionText.model_validate(text)
                for value in self.properties.values()
                if value.get("type") == "title"
                for text in value.get("title", [])
            ]
        return "".join(text.plain_text for text in title).strip() or "Untitled"

    @property
    def parent_key(self) -> str:
        """Data sources inherit their database's containing page."""
        parent = self.database_parent if self.object == "data_source" else self.parent
        return parent.key if parent is not None else ""


class NotionListing(BaseModel):
    """Native pagination shared by search and data-source queries."""

    results: list[NotionNode]
    has_more: bool
    next_cursor: str | None


class NotionMarkdown(BaseModel):
    """Preserve the native Markdown export and its completeness evidence."""

    object: Literal["page_markdown"]
    id: UUID
    markdown: str
    truncated: bool
    unknown_block_ids: list[UUID]


class NotionSnapshot(BaseModel):
    """One source observation retained by RecordLink, including database properties."""

    node: NotionNode
    content: NotionMarkdown | None = None
    unavailable: bool = False

    @property
    def failure(self) -> SemanticError | None:
        """Incomplete or inaccessible observations must never replace a good body."""
        if self.unavailable:
            return SemanticError(
                "notion_page_unavailable",
                details={"message": "Notion could not return this page. Check that it is shared with the connection."},
            )
        if self.content is not None and (self.content.truncated or self.content.unknown_block_ids):
            return SemanticError(
                "notion_incomplete_markdown",
                details={
                    "message": "Notion returned incomplete Markdown. Check unsupported blocks or source access.",
                    "truncated": self.content.truncated,
                    "unknown_blocks": [str(value) for value in self.content.unknown_block_ids],
                },
            )
        return None


class NotionCursor(BaseModel):
    """Resume a bounded inventory, then resolve parents and links between known identities."""

    phase: Literal["search", "sources", "hierarchy"] = "search"
    start_cursor: str | None = None
    after_source: str = ""
    pending: list[NotionNode] = Field(default_factory=list)
    after: str = ""


class NotionResponseError(IntegrationError):
    """A provider status with a safe message, never the provider's response body."""

    def __init__(self, status: int, *, retry_after: timedelta | None = None) -> None:
        self.status = status
        messages = {
            401: "Notion rejected this API token. Check that it is valid and has not been revoked.",
            403: "The Notion connection needs read-content capability and access to the source pages.",
            404: "The Notion page is unavailable or is no longer shared with this connection.",
        }
        super().__init__(
            messages.get(status, f"Notion request failed (HTTP {status})."),
            transient=status == 429 or status >= 500,
            retry_after=retry_after,
        )


class NotionBackend(BridgeImpl, HttpClientMixin):
    """Map source pages into Knowledge; Integrate owns identity, retries and conflicts."""

    registry_setting = "ANGEE_NOTION_BACKEND_CLASSES"
    key = "notion"
    label = "Notion"
    icon = "integration"
    oauth_client = ""
    defaults = {"vendor": "notion"}
    requires_connection_discovery = True
    supports_identity_reads = True
    sync_parallelism = 1

    def _request[T: BaseModel](
        self,
        model: type[T],
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        credential: Any = None,
    ) -> T:
        credential = credential or self.bridge.fresh_credential()
        if credential is None or credential.kind != CredentialKind.STATIC_TOKEN:
            raise IntegrationError("Attach an API token credential for Notion.")
        if not credential.secret_value():
            raise IntegrationError("The Notion API token is empty.")
        response = self.http.request(
            "GET" if payload is None else "POST",
            f"https://api.notion.com/v1/{path}",
            headers={
                **credential.auth_headers(),
                "Notion-Version": "2026-03-11",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            body=None if payload is None else json.dumps(payload).encode(),
            max_bytes=8 * 1024 * 1024,
        )
        if not response.is_success:
            hint = response.headers.get("Retry-After", "")
            delay = timedelta(seconds=int(hint)) if hint.isdecimal() and 0 < int(hint) <= 86400 else None
            raise NotionResponseError(response.status_code, retry_after=delay)
        try:
            return model.model_validate_json(response.content)
        except ValidationError as error:
            raise IntegrationError("Notion returned an invalid response.") from error

    def probe(self, credential: Any) -> NotionUser:
        """Authenticate using the same transport and error policy as synchronization."""
        return self._request(NotionUser, "users/me", credential=credential)

    def discover_connection(self, credential: Any) -> ConnectionDiscovery:
        """Let Integration own credential validation, retries and completion state."""
        self.probe(credential)
        return ConnectionDiscovery()

    def streams(self, *, deadline: float | None = None) -> Iterable[StreamDefinition]:
        """Keep one identity namespace even when pages move between data sources."""
        self.bridge.ensure_vault()
        return (StreamDefinition("pages", kind=StreamKind.RECORD_REPLICA),)

    def extract(self, stream: Any, page_bound: int, *, deadline: float | None = None) -> StreamPage:
        """Commit each bounded batch with its continuation; search absence never deletes."""
        cursor = NotionCursor.model_validate(stream.cursor)
        bound = min(page_bound, 10)
        snapshots: list[NotionSnapshot] = []
        if cursor.phase == "hierarchy" and not cursor.pending:
            links = list(stream.links.filter(external_key__gt=cursor.after).order_by("external_key")[:bound])
            records = self._changes(stream, [NotionSnapshot.model_validate(link.source_payload) for link in links])
            cursor.after = links[-1].external_key if links else ""
            exhausted = len(links) < bound
            return StreamPage(records, {} if exhausted else cursor.model_dump(mode="json"), exhausted=exhausted)

        if not cursor.pending:
            source = None
            if cursor.phase == "sources":
                source = (
                    stream.links.filter(
                        source_payload__node__object="data_source", external_key__gt=cursor.after_source
                    )
                    .order_by("external_key")
                    .first()
                )
                if source is None:
                    cursor.phase = "hierarchy"
                    return StreamPage((), cursor.model_dump(mode="json"), exhausted=False)
            path = "search" if source is None else f"data_sources/{source.external_key}/query"
            payload: dict[str, Any] = {"page_size": bound}
            if cursor.start_cursor:
                payload["start_cursor"] = cursor.start_cursor
            try:
                listing = self._request(NotionListing, path, payload=payload)
            except NotionResponseError as error:
                if error.status == 400 and cursor.start_cursor:
                    raise CursorInvalid() from error
                if source is None or error.status not in {403, 404}:
                    raise
                source_snapshot = NotionSnapshot.model_validate(source.source_payload)
                snapshots.append(NotionSnapshot(node=source_snapshot.node, unavailable=True))
                listing = NotionListing(results=[], has_more=False, next_cursor=None)
            if listing.has_more and (not listing.next_cursor or listing.next_cursor == cursor.start_cursor):
                raise IntegrationError("Notion did not advance its listing cursor.")
            cursor.pending = listing.results
            cursor.start_cursor = listing.next_cursor if listing.has_more else None
            if not listing.has_more:
                if source is not None:
                    cursor.after_source = source.external_key
                cursor.phase = "sources"

        while cursor.pending and len(snapshots) < bound:
            if snapshots and deadline is not None and monotonic() >= deadline:
                break
            try:
                node = cursor.pending[0]
                link = stream.links.filter(external_key=str(node.id)).first()
                previous = NotionSnapshot.model_validate(link.source_payload) if link is not None else None
                snapshot = self._snapshot(node, previous=previous)
            except IntegrationError as error:
                if not snapshots or not error.transient:
                    raise
                break
            snapshots.append(snapshot)
            cursor.pending.pop(0)
        return StreamPage(self._changes(stream, snapshots), cursor.model_dump(mode="json"), exhausted=False)

    def _snapshot(self, node: NotionNode, *, previous: NotionSnapshot | None = None) -> NotionSnapshot:
        content = None
        if (
            previous is not None and previous.node == node
            and previous.content is not None and previous.failure is None and not node.is_trashed
        ):
            return NotionSnapshot(node=node, content=previous.content)
        if node.object == "page" and not node.is_trashed:
            try:
                content = self._request(NotionMarkdown, f"pages/{node.id}/markdown")
            except NotionResponseError as error:
                if error.status not in {403, 404}:
                    raise
                return NotionSnapshot(node=node, unavailable=True)
            if content.id != node.id:
                raise IntegrationError("Notion returned content for a different page.")
        return NotionSnapshot(node=node, content=content)

    def read_keys(self, stream: Any, keys: Sequence[str]) -> Iterable[RecordChange]:
        """Read explicit identities for the shared Retry and Keep remote actions."""
        links = {link.external_key: link for link in stream.links.filter(external_key__in=keys)}
        for key in keys:
            previous = NotionSnapshot.model_validate(links[key].source_payload)
            node = previous.node
            try:
                path = "pages" if node.object == "page" else "data_sources"
                node = self._request(NotionNode, f"{path}/{node.id}")
            except NotionResponseError as error:
                if error.status not in {403, 404}:
                    raise
                yield from self._changes(stream, [NotionSnapshot(node=previous.node, unavailable=True)])
                continue
            if str(node.id) != key:
                raise IntegrationError("Notion returned a different page identity.")
            yield from self._changes(stream, [self._snapshot(node)])

    def _changes(self, stream: Any, snapshots: Sequence[NotionSnapshot]) -> list[RecordChange]:
        keys = {key for snapshot in snapshots for key in (str(snapshot.node.id), snapshot.node.parent_key) if key}
        prepared = []
        for snapshot in snapshots:
            body, failure = None, snapshot.failure
            references: set[str] = set()
            if snapshot.content is not None and failure is None:
                converter = NotionMarkdownConverter()
                try:
                    body = converter.convert(snapshot.content.markdown)
                except ValueError as error:
                    failure = SemanticError("notion_unsupported_markup", details={"message": str(error)})
                if self.bridge.rewrite_page_links:
                    references = converter.page_ids - {str(snapshot.node.id)}
                    keys.update(references)
            prepared.append((snapshot, body, failure, references))
        pages = apps.get_model("knowledge", "Page")
        page_type = ContentType.objects.get_for_model(pages)
        links = {
            link.external_key: link
            for link in stream.links.filter(external_key__in=keys, target_content_type=page_type)
        }
        targets = {
            str(page.pk): page
            for page in pages.objects.filter(
                vault_id=self.bridge.vault_id,
                pk__in=[link.target_object_id for link in links.values()],
            ).select_related("markdown")
        }
        page_links = {
            key: target.wikilink_url
            for key, link in links.items()
            if (target := targets.get(link.target_object_id)) is not None and not target.is_trashed
        } if self.bridge.rewrite_page_links else {}
        title_limit = cast(int, pages._meta.get_field("title").max_length)
        titles = [snapshot.node.label[:title_limit] for snapshot in snapshots]
        occupied: dict[str, int | str] = dict(
            pages.objects.filter(vault_id=self.bridge.vault_id, title__in=titles).untrashed().values_list("title", "pk")
        )
        records = []
        for (snapshot, body, failure, references), title in zip(prepared, titles, strict=True):
            node = snapshot.node
            resolved_links = {key: page_links[key] for key in references if key in page_links}
            if body is not None and resolved_links and failure is None:
                assert snapshot.content is not None
                try:
                    body = NotionMarkdownConverter(resolved_links).convert(snapshot.content.markdown)
                except ValueError as error:
                    body = None
                    failure = SemanticError("notion_unsupported_markup", details={"message": str(error)})
            link = links.get(str(node.id))
            target = targets.get(link.target_object_id) if link else None
            parent_link = links.get(node.parent_key)
            parent = targets.get(parent_link.target_object_id) if parent_link else None
            if title in occupied and (target is None or occupied[title] != target.pk):
                suffix = f" [{node.id}]"
                title = title[: title_limit - len(suffix)] + suffix
            occupied[title] = target.pk if target is not None else str(node.id)
            mapped = {
                "title": title,
                "parent": str(parent.pk) if parent is not None else None,
                "icon": (node.icon or {}).get("emoji", ""),
                "body": body,
                "kind": Page.PageKind.NOTE if node.object == "page" else Page.PageKind.FOLDER,
            }
            payload = snapshot.model_dump(mode="json")
            records.append(
                RecordChange(
                    str(node.id),
                    payload,
                    "" if node.is_trashed else canonical_json_sha256(payload),
                    node.last_edited_time.isoformat(),
                    target=target,
                    projection=self._projection(target),
                    mapping_version=4,
                    mapped_payload=mapped,
                    dependency_digest=canonical_json_sha256([mapped["parent"], resolved_links]),
                    tombstone=node.is_trashed,
                    failure=failure,
                )
            )
        return records

    @staticmethod
    def _projection(page: Any) -> dict[str, Any] | None:
        if page is None or page.is_trashed:
            return None
        return {
            "title": page.title,
            "parent": str(page.parent_id) if page.parent_id else None,
            "icon": page.icon,
            "body": page.markdown.body if page.kind != Page.PageKind.FOLDER else None,
            "kind": str(page.kind),
        }

    def apply_record(self, stream: Any, record: RecordChange) -> ApplyResult:
        """Use Knowledge's factories and compare-and-set body writer under the owner's permissions."""
        vault = self.bridge.ensure_vault()
        owner = self.bridge.sync_owner()
        with actor_context(owner):
            pages = apps.get_model("knowledge", "Page").objects
            target = None
            if record.target is not None:
                try:
                    target = (
                        pages.with_actor(owner)
                        .lock_if_supported()
                        .select_related("markdown")
                        .get(pk=record.target.pk, vault=vault)
                    )
                except ObjectDoesNotExist as error:
                    raise SemanticError("local_page_unavailable", kind=SyncFailureKind.CONFLICT) from error
                if self._projection(target) != record.projection:
                    raise SemanticError("local_page_changed", kind=SyncFailureKind.CONFLICT)
            if record.tombstone:
                return ApplyResult(
                    target=target,
                    mapped_payload=self._projection(target),
                    dependency_digest=record.dependency_digest,
                    mapping_version=record.mapping_version,
                )
            mapped = record.mapped_payload
            parent = pages.with_actor(owner).get(pk=mapped["parent"], vault=vault) if mapped["parent"] else None
            fields = {"title": mapped["title"], "icon": mapped["icon"], "parent": parent}
            if target is None:
                target = pages.create_in(
                    vault,
                    kind=mapped["kind"],
                    **fields,
                    **({"body": mapped["body"]} if mapped["kind"] == Page.PageKind.NOTE else {}),
                )
            else:
                for name, value in fields.items():
                    setattr(target, name, value)
                target.full_clean()
                target.save(update_fields=[*fields, "updated_at"])
                if mapped["kind"] == Page.PageKind.NOTE and target.markdown.body != mapped["body"]:
                    apps.get_model("knowledge", "MarkdownPage").objects.write_body(
                        target,
                        mapped["body"],
                        expected_hash=target.markdown.body_hash,
                    )
            return ApplyResult(
                target=target,
                mapped_payload=mapped,
                dependency_digest=record.dependency_digest,
                mapping_version=record.mapping_version,
            )

    def source_url(self, link: Any) -> str:
        """Use Notion's immutable page identity for the shared source-record link."""
        return f"https://www.notion.so/{UUID(link.external_key).hex}"

    def source_label(self, link: Any) -> str:
        """Project the retained Notion title through its existing payload schema."""

        if not link.source_payload:
            return super().source_label(link)
        return NotionSnapshot.model_validate(link.source_payload).node.label
