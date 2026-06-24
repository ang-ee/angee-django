"""Strawberry-Django schema contributions for the knowledge addon."""

from __future__ import annotations

from typing import Annotated, Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.db.models import F
from rebac import system_context
from strawberry import auto

from angee.base.models import public_id_for
from angee.graphql.data import AngeeHasuraWriteBackend, hasura_resource, public_pk_decoder
from angee.graphql.deletion import DeletePreview, attach_delete_preview_metadata, delete_by_public_id
from angee.graphql.ids import PublicID, require_instance_for_id
from angee.graphql.node import AngeeNode
from angee.graphql.revisions import revisions
from angee.graphql.subscriptions import changes
from angee.graphql.writes import write_queryset
from angee.iam.identity import user_display_label, user_public_id
from angee.knowledge.models import StaleBodyError, UnsupportedPageKindError

Vault = apps.get_model("knowledge", "Vault")
Page = apps.get_model("knowledge", "Page")
MarkdownPage = apps.get_model("knowledge", "MarkdownPage")
Link = apps.get_model("knowledge", "Link")


@strawberry_django.type(Vault)
class VaultType(AngeeNode):
    """GraphQL projection of a vault."""

    name: auto
    description: auto
    icon: auto
    accent: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["owner_id"])
    def owner(self) -> strawberry.ID | None:
        """Return the owner's public id without exposing the user object."""

        return _as_id(user_public_id(cast(Any, self).owner_id))

    @strawberry_django.field(only=["owner_id"])
    def owner_label(self) -> str | None:
        """Return the owner's display label — no user object exposed."""

        return user_display_label(cast(Any, self).owner_id)


@strawberry_django.type(MarkdownPage)
class MarkdownPageType(AngeeNode):
    """GraphQL projection of a page's markdown body sidecar."""

    body: auto
    body_hash: auto
    word_count: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["page_id"])
    def page(self) -> strawberry.ID:
        """Return the owning page's public id."""

        return strawberry.ID(public_id_for(Page, cast(Any, self).page_id))

    @strawberry_django.field(only=["body"])
    def excerpt(self) -> str:
        """Return the leading body characters used for list previews."""

        return cast(str, cast(Any, self).excerpt)


@strawberry.type
class BacklinkType:
    """One resolved page that links to the page being viewed."""

    page: strawberry.ID
    title: str
    display_text: str


@strawberry_django.type(Page)
class PageType(AngeeNode):
    """GraphQL projection of a page."""

    title: auto
    kind: auto
    icon: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["vault_id"])
    def vault(self) -> strawberry.ID:
        """Return the owning vault's public id without exposing the vault."""

        return strawberry.ID(public_id_for(Vault, cast(Any, self).vault_id))

    @strawberry_django.field(only=["vault_id"])
    def vault_label(self) -> str | None:
        """Return the owning vault's display name — no vault object exposed.

        Resolved under ``system_context`` so a page viewer who lacks vault
        read still sees where the page lives; only the name string leaves
        the resolver.
        """

        with system_context(reason="knowledge.graphql.vault_label"):
            row = Vault._default_manager.filter(pk=cast(Any, self).vault_id).only("name").first()
        return None if row is None else str(row.name)

    @strawberry_django.field(only=["parent_id"])
    def parent(self) -> strawberry.ID | None:
        """Return the parent page's public id, if the page has one."""

        if cast(Any, self).parent_id is None:
            return None
        return strawberry.ID(public_id_for(Page, cast(Any, self).parent_id))

    @strawberry_django.field(only=["created_by_id"])
    def created_by(self) -> strawberry.ID | None:
        """Return the creator's public id without exposing the user object."""

        return _as_id(user_public_id(cast(Any, self).created_by_id))

    @strawberry_django.field(only=["created_by_id"])
    def created_by_label(self) -> str | None:
        """Return the creator's display label — no user object exposed."""

        return user_display_label(cast(Any, self).created_by_id)

    @strawberry_django.field(only=["id"])
    def markdown(self) -> MarkdownPageType | None:
        """Return the markdown body sidecar visible to the actor, if any."""

        return cast(
            MarkdownPageType | None,
            MarkdownPage._default_manager.filter(page_id=cast(Any, self).pk).first(),
        )

    @strawberry_django.field(only=["id"])
    def backlinks(self) -> list[BacklinkType]:
        """Return resolved pages linking here, scoped to readable sources.

        The source title is annotated across the relation rather than
        ``select_related``-ed: ``source_page`` is REBAC-guarded, so
        materializing it inside an actor-scoped resolver would fail.
        """

        rows = (
            Link._default_manager.filter(
                target_page_id=cast(Any, self).pk,
                is_resolved=True,
            )
            .apply_ambient_scope()
            .annotate(source_title=F("source_page__title"))
            .order_by("source_title", "sqid")
        )
        return [
            BacklinkType(
                page=strawberry.ID(public_id_for(Page, row.source_page_id)),
                title=str(row.source_title),
                display_text=row.display_text,
            )
            for row in rows
        ]


@strawberry.type
class PageBodyPayload:
    """Result of a markdown body write."""

    ok: bool
    markdown: MarkdownPageType | None = None
    error: str | None = None
    error_code: str | None = strawberry.field(name="error_code", default=None)


def _resource_queryset(model: type[Any], info: strawberry.Info) -> Any:
    """Return the row-scoped queryset for one knowledge resource."""

    del info
    return model.objects.all()


def _resource_aggregate_queryset(model: type[Any], info: strawberry.Info) -> Any:
    """Return the queryset safe for permission-naive aggregate math."""

    queryset = _resource_queryset(model, info)
    scoped = getattr(queryset, "scoped_for_aggregate", None)
    return scoped() if callable(scoped) else queryset


class VaultWriteBackend(AngeeHasuraWriteBackend):
    """Write semantics for vaults: create belongs to the manager factory."""

    def create(self, info: strawberry.Info, data: dict[str, Any]) -> Any:
        """Create a vault owned by the requesting user."""

        user = getattr(info.context.request, "user", None)
        return Vault._default_manager.create_for(user, **data)


class PageWriteBackend(AngeeHasuraWriteBackend):
    """Write semantics for pages: create belongs to the manager factory."""

    def create(self, info: strawberry.Info, data: dict[str, Any]) -> Any:
        """Create a page in a vault the requesting user can write."""

        del info
        vault = require_instance_for_id(Vault, data["vault"])
        parent = None
        if data.get("parent") is not None:
            parent = require_instance_for_id(Page, data["parent"])
        payload = dict(data)
        payload.pop("vault", None)
        payload.pop("parent", None)
        return Page._default_manager.create_in(vault, parent=parent, **payload)


_VAULT_RESOURCE = hasura_resource(
    VaultType,
    model=Vault,
    name="vaults",
    filterable=["id", "name", "updated_at"],
    sortable=["name", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["updated_at"],
    insertable=["name", "description", "icon", "accent"],
    updatable=["name", "description", "icon", "accent"],
    get_queryset=lambda info: _resource_queryset(Vault, info),
    get_aggregate_queryset=lambda info: _resource_aggregate_queryset(Vault, info),
    write_backend=VaultWriteBackend(Vault),
    id_decode=public_pk_decoder(Vault),
)
_PAGE_RESOURCE = hasura_resource(
    PageType,
    model=Page,
    name="pages",
    filterable=["id", "vault", "title", "kind", "updated_at"],
    sortable=["title", "kind", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["vault", "vault__name", "kind", "updated_at"],
    insertable=["vault", "title", "kind", "parent", "icon"],
    updatable=["title", "kind", "icon", "parent"],
    field_id_decode={
        "vault": public_pk_decoder(Vault),
        "parent": public_pk_decoder(Page),
    },
    get_queryset=lambda info: _resource_queryset(Page, info),
    get_aggregate_queryset=lambda info: _resource_aggregate_queryset(Page, info),
    write_backend=PageWriteBackend(Page, public_id_fields={"parent": Page}),
    id_decode=public_pk_decoder(Page),
)


@strawberry.type
class KnowledgeMutation:
    """Markdown body writes that belong to the Knowledge domain."""

    @strawberry.mutation(name="delete_vault")
    def delete_vault(self, id: PublicID, confirm: bool = False) -> DeletePreview:
        """Return or apply the authored vault cascade delete preview."""

        return delete_by_public_id(
            Vault,
            str(id),
            confirm=confirm,
            queryset=write_queryset(Vault),
        )

    @strawberry.mutation(name="delete_page")
    def delete_page(self, id: PublicID, confirm: bool = False) -> DeletePreview:
        """Return or apply the authored page cascade delete preview."""

        return delete_by_public_id(
            Page,
            str(id),
            confirm=confirm,
            queryset=write_queryset(Page),
        )

    @strawberry.mutation(name="update_page_body")
    def update_page_body(
        self,
        page: PublicID,
        body: str,
        expected_hash: Annotated[
            str | None,
            strawberry.argument(name="expected_hash"),
        ] = None,
    ) -> PageBodyPayload:
        """Write a page's markdown body, last-write-wins with a stale guard."""

        target = require_instance_for_id(Page, page)
        try:
            markdown = MarkdownPage._default_manager.write_body(
                target,
                body,
                expected_hash=expected_hash,
            )
        except StaleBodyError as error:
            return PageBodyPayload(ok=False, error=str(error), error_code="STALE_BODY")
        except UnsupportedPageKindError as error:
            return PageBodyPayload(ok=False, error=str(error), error_code="UNSUPPORTED_KIND")
        return PageBodyPayload(ok=True, markdown=cast(MarkdownPageType, markdown))


KnowledgeMutation = attach_delete_preview_metadata(
    KnowledgeMutation,
    model=Vault,
    node=VaultType,
    field="delete_vault",
)
KnowledgeMutation = attach_delete_preview_metadata(
    KnowledgeMutation,
    model=Page,
    node=PageType,
    field="delete_page",
)


def _as_id(public_id: str | None) -> strawberry.ID | None:
    """Return one optional public id as a GraphQL ID."""

    return None if public_id is None else strawberry.ID(public_id)


_KNOWLEDGE_SCHEMA_BUCKET = {
    "query": [
        _VAULT_RESOURCE.query,
        _PAGE_RESOURCE.query,
        revisions(MarkdownPageType, name="markdownPage"),
    ],
    "mutation": [
        KnowledgeMutation,
        _VAULT_RESOURCE.mutation,
        _PAGE_RESOURCE.mutation,
    ],
    "types": [
        VaultType,
        PageType,
        MarkdownPageType,
        BacklinkType,
        PageBodyPayload,
        *_VAULT_RESOURCE.types,
        *_PAGE_RESOURCE.types,
    ],
}


schemas = {
    "public": {
        **_KNOWLEDGE_SCHEMA_BUCKET,
    },
    "console": {
        **_KNOWLEDGE_SCHEMA_BUCKET,
        "subscription": [
            changes(Page, field="pageChanged"),
            changes(MarkdownPage, field="markdownPageChanged"),
        ],
    },
}
