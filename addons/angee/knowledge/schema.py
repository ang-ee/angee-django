"""Strawberry-Django schema contributions for the knowledge addon."""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.db.models import F
from rebac import system_context
from strawberry import auto
from strawberry_django.pagination import OffsetPaginated

from angee.base.models import public_id_for
from angee.graphql.crud import crud
from angee.graphql.ids import PublicID, require_instance_for_id
from angee.graphql.node import AngeeNode, detail
from angee.graphql.revisions import revisions
from angee.graphql.subscriptions import changes
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


@strawberry.input
class VaultInput:
    """Fields accepted when creating a vault."""

    name: str
    description: str = ""
    icon: str = ""
    accent: str = ""


@strawberry.input
class VaultPatch:
    """Fields accepted when updating a vault."""

    id: PublicID
    name: str | None = strawberry.UNSET
    description: str | None = strawberry.UNSET
    icon: str | None = strawberry.UNSET
    accent: str | None = strawberry.UNSET


@strawberry.input
class PageInput:
    """Fields accepted when creating a page."""

    vault: PublicID
    title: str
    kind: str = Page.Kind.NOTE
    parent: PublicID | None = None
    icon: str = ""


@strawberry.input
class PagePatch:
    """Fields accepted when updating a page."""

    id: PublicID
    title: str | None = strawberry.UNSET
    kind: str | None = strawberry.UNSET
    icon: str | None = strawberry.UNSET
    # Reparent (move) within the vault — null lifts the page to the root. The
    # REBAC `parent->write` gate authorises the destination.
    parent: PublicID | None = strawberry.UNSET


@strawberry.type
class PageBodyPayload:
    """Result of a markdown body write."""

    ok: bool
    markdown: MarkdownPageType | None = None
    error: str | None = None
    error_code: str | None = None


@strawberry_django.filter_type(Vault, lookups=True)
class VaultFilter:
    """Field lookups accepted when filtering the vaults connection."""

    name: auto
    updated_at: auto


@strawberry_django.order_type(Vault)
class VaultOrder:
    """Orderings accepted by the vaults connection."""

    name: auto
    created_at: auto
    updated_at: auto


@strawberry_django.filter_type(Page, lookups=True)
class PageFilter:
    """Field lookups accepted when filtering the pages connection."""

    vault: auto
    title: auto
    kind: auto
    updated_at: auto


@strawberry_django.order_type(Page)
class PageOrder:
    """Orderings accepted by the pages connection."""

    title: auto
    kind: auto
    created_at: auto
    updated_at: auto


@strawberry.type
class KnowledgeQuery:
    """Actor-scoped knowledge queries."""

    vaults: OffsetPaginated[VaultType] = strawberry_django.offset_paginated(
        filters=VaultFilter,
        order=VaultOrder,
    )
    vault: VaultType | None = detail(VaultType)
    pages: OffsetPaginated[PageType] = strawberry_django.offset_paginated(
        filters=PageFilter,
        order=PageOrder,
    )
    page: PageType | None = detail(PageType)


@strawberry.type
class KnowledgeMutation:
    """Create and body-write mutations that preflight their REBAC gate.

    Updates and deletes ride the row-scoped ``crud`` surfaces below; the
    operations here address rows that do not exist yet, so they dispatch to
    the manager factories that own the ``check_new`` preflight.
    """

    @strawberry.mutation
    def create_vault(self, info: strawberry.Info, data: VaultInput) -> VaultType:
        """Create a vault owned by the requesting user."""

        user = getattr(info.context.request, "user", None)
        return cast(
            VaultType,
            Vault._default_manager.create_for(
                user,
                name=data.name,
                description=data.description,
                icon=data.icon,
                accent=data.accent,
            ),
        )

    @strawberry.mutation
    def create_page(self, data: PageInput) -> PageType:
        """Create a page in a vault the requesting user can write."""

        vault = require_instance_for_id(Vault, data.vault)
        parent = None
        if data.parent is not None:
            parent = require_instance_for_id(Page, data.parent)
        return cast(
            PageType,
            Page._default_manager.create_in(
                vault,
                parent=parent,
                title=data.title,
                kind=data.kind,
                icon=data.icon,
            ),
        )

    @strawberry.mutation
    def update_page_body(
        self,
        page: PublicID,
        body: str,
        expected_hash: str | None = None,
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


def _as_id(public_id: str | None) -> strawberry.ID | None:
    """Return one optional public id as a GraphQL ID."""

    return None if public_id is None else strawberry.ID(public_id)


_KNOWLEDGE_SCHEMA_BUCKET = {
    "query": [KnowledgeQuery, revisions(MarkdownPageType, name="markdownPage")],
    "mutation": [
        KnowledgeMutation,
        crud(VaultType, update=VaultPatch, delete=True),
        crud(PageType, update=PagePatch, delete=True),
    ],
    "types": [VaultType, PageType, MarkdownPageType, BacklinkType, PageBodyPayload],
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
