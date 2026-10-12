"""Strawberry-Django schema contributions for the knowledge addon."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Annotated, Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db.models import F
from rebac import system_context
from rebac.resources import model_resource_type
from strawberry import auto

from angee.base.fields import SqidField
from angee.base.identity import instance_from_public_id
from angee.base.scoping import write_scoped_queryset
from angee.data.metadata import DataResourceSubtitleMetadata
from angee.graphql.actions import ActionResult, action_guard
from angee.graphql.capabilities import permissions_field
from angee.graphql.data import (
    AngeeHasuraWriteBackend,
    declared_hasura_resource_fields,
    declared_hasura_write_relation_fields,
    hasura_model_resource,
    public_pk_decoder,
)
from angee.graphql.deletion import DeletePreview, attach_delete_preview_metadata, delete_by_public_id
from angee.graphql.ids import (
    PublicID,
    optional_public_id,
    require_instance_for_id,
    require_public_id,
    to_public_id,
)
from angee.graphql.node import NODE_DISPLAY_NAME_DESCRIPTION, AngeeNode
from angee.graphql.revisions import revisions
from angee.graphql.subscriptions import changes
from angee.graphql.writes import write_queryset
from angee.iam.audit import AuthoredRefMixin, TrashedRefMixin, user_label_prefetch
from angee.iam.identity import user_display_label, user_label, user_public_id
from angee.iam.permissions import request_from_info, session_user
from angee.knowledge.models import (
    AmbiguousMatchError,
    RecordBindingManager,
    SectionNotFoundError,
    SectionOp,
    StaleBodyError,
    StructuredEditError,
    UnsupportedPageKindError,
)
from angee.tags.schema import TaggedNode, tags_input_extensions

Vault = apps.get_model("knowledge", "Vault")
Page = apps.get_model("knowledge", "Page")
MarkdownPage = apps.get_model("knowledge", "MarkdownPage")
strawberry.enum(SectionOp)
Link = apps.get_model("knowledge", "Link")
RecordBinding = apps.get_model("knowledge", "RecordBinding")


@strawberry_django.type(Vault)
class VaultType(AngeeNode):
    """GraphQL projection of a vault."""

    display_name: str = strawberry_django.field(
        resolver=AngeeNode.display_name, only=["name"], description=NODE_DISPLAY_NAME_DESCRIPTION
    )
    name: auto
    description: auto
    icon: auto
    accent: auto
    created_at: auto
    updated_at: auto

    permissions = permissions_field(("write",))

    @strawberry_django.field(only=["owner_id"])
    def owner(self) -> strawberry.ID | None:
        """Return the owner's public id without exposing the user object."""

        return optional_public_id(user_public_id(cast(Any, self).owner_id))

    @strawberry_django.field(only=["owner_id"], prefetch_related=[partial(user_label_prefetch, "owner")])
    def owner_label(self, info: strawberry.Info) -> str | None:
        """Return the owner's display label — no user object exposed."""

        if hasattr(self, "_iam_owner_label_source"):
            user = cast(Any, self)._iam_owner_label_source
            return user_label(user) if user is not None else None
        return user_display_label(cast(Any, self).owner_id, request=request_from_info(info))


@strawberry.type
class OutlineEntryType:
    """One ATX heading in a page body's outline."""

    level: int
    text: str
    slug: str


@strawberry_django.type(MarkdownPage)
class MarkdownPageType(AngeeNode):
    """GraphQL projection of a concrete markdown page."""

    body: auto
    body_hash: auto
    word_count: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["page_ptr_id"])
    def page(self) -> strawberry.ID:
        """Return the owning page's public id."""

        return require_public_id(Page, cast(Any, self).page_ptr_id)

    @strawberry_django.field(only=["body"])
    def excerpt(self) -> str:
        """Return the leading body characters used for list previews."""

        return cast(str, cast(Any, self).excerpt)

    @strawberry_django.field(only=["body"])
    def outline(self) -> list[OutlineEntryType]:
        """Return the body's heading outline, derived like :attr:`excerpt`.

        Parsed through the body's own structure owner
        (:meth:`MarkdownPage.parse_outline`) so the read field and the
        section-patch write share one markdown parser.
        """

        return [
            OutlineEntryType(level=entry.level, text=entry.text, slug=entry.slug)
            for entry in MarkdownPage.parse_outline(cast(Any, self).body)
        ]


@strawberry.type
class BacklinkType:
    """One resolved page that links to the page being viewed."""

    page: strawberry.ID
    title: str
    display_text: str


@strawberry_django.type(Page)
class PageType(AuthoredRefMixin, TrashedRefMixin, AngeeNode):
    """GraphQL projection of a page."""

    display_name: str = strawberry_django.field(
        resolver=AngeeNode.display_name, only=["title"], description=NODE_DISPLAY_NAME_DESCRIPTION
    )
    title: auto
    icon: auto
    is_trashed: auto
    created_at: auto
    updated_at: auto
    permissions = permissions_field(("write", "delete"))

    @strawberry_django.field(only=["id", "markdown__kind"])
    def kind(self) -> str:
        """Return the concrete page kind."""

        return cast(str, cast(Any, self).kind)

    @classmethod
    def get_queryset(cls, queryset: Any, info: strawberry.Info) -> Any:
        """Load concrete markdown identity with each page collection."""

        del info
        return queryset.select_related("markdown")

    @strawberry_django.field(only=["vault_id"])
    def vault(self) -> strawberry.ID:
        """Return the owning vault's public id without exposing the vault."""

        return require_public_id(Vault, cast(Any, self).vault_id)

    @strawberry_django.field(only=["vault_id"])
    def vault_label(self) -> str | None:
        """Return the owning vault's display name — no vault object exposed.

        Resolved under ``system_context`` so a page viewer who lacks vault
        read still sees where the page lives; only the name string leaves
        the resolver.
        """

        with system_context(reason="knowledge.graphql.vault_label"):
            vault = Vault._default_manager.filter(pk=cast(Any, self).vault_id).only("name").first()
        return None if vault is None else str(vault)

    @strawberry_django.field(only=["parent_id"])
    def parent(self) -> strawberry.ID | None:
        """Return the parent page's public id, if the page has one."""

        return to_public_id(Page, cast(Any, self).parent_id)

    @strawberry_django.field(only=["id"])
    def markdown(self) -> MarkdownPageType | None:
        """Return the concrete markdown body visible to the actor, if any."""

        return cast(
            MarkdownPageType | None,
            MarkdownPage._default_manager.filter(pk=cast(Any, self).pk).first(),
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
                source_page__is_trashed=False,
            )
            .scoped()
            .annotate(source_title=F("source_page__title"))
            .order_by("source_title", "sqid")
        )
        return [
            BacklinkType(
                page=require_public_id(Page, row.source_page_id),
                title=str(row.source_title),
                display_text=row.display_text,
            )
            for row in rows
        ]


@strawberry_django.type(RecordBinding)
class RecordBindingType(AngeeNode):
    """Binding metadata visible only to readers of both knowledge and target."""

    role: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["page_id", "page__title"])
    def page_title(self) -> str | None:
        """Return a readable bound page's title, when this edge owns a page."""

        page = cast(Any, self).page
        return None if page is None else str(page.title)

    @strawberry_django.field(only=["page_id"])
    def page_detail(self) -> PageType | None:
        """Expose the readable page through its existing content and author projection."""

        return cast(PageType | None, cast(Any, self).page)

    @strawberry_django.field(only=["page_id"])
    def page_can_write(self) -> bool:
        """Expose the page end's write permission for binding controls."""

        page = cast(Any, self).page
        return page is not None and bool(page.has_access("write"))

    @strawberry_django.field(only=["page_id"])
    def page(self) -> PublicID | None:
        """Return the bound page id, if this is a page binding."""

        return to_public_id(Page, cast(Any, self).page_id)

    @strawberry_django.field(only=["vault_id"])
    def vault(self) -> PublicID | None:
        """Return the bound vault id, if this is a vault binding."""

        return to_public_id(Vault, cast(Any, self).vault_id)

    @strawberry_django.field(only=["content_type_id", "object_id"])
    def model_label(self) -> str:
        """Return the canonical target's ``app_label.ModelName`` label."""

        return cast(Any, self).record_model_label

    @strawberry_django.field(only=["content_type_id", "object_id"])
    def record_id(self) -> PublicID:
        """Return the canonical ancestor's public id, not the passed model level's."""

        return PublicID(cast(Any, self).record_public_id)


@strawberry.input
class RecordBindingInput:
    """One Page/Vault-to-record role key used by bind and unbind."""

    model_label: str
    record_id: PublicID
    page: PublicID | None = None
    vault: PublicID | None = None
    role: str = RecordBindingManager.DEFAULT_ROLE


@strawberry.type
class PageBodyPayload:
    """Result of a markdown body write."""

    ok: bool
    markdown: MarkdownPageType | None = None
    error: str | None = None
    error_code: str | None = strawberry.field(name="error_code", default=None)


def _markdown_write_payload(write: Callable[[], Any]) -> PageBodyPayload:
    """Run a markdown body write and map its domain errors to a payload.

    The single owner of the knowledge body-write ``error -> error_code``
    mapping, shared by every body mutation (``update_page_body``,
    ``patch_page_section``, ``replace_page_text``). The structured-edit
    subclasses surface their own sub-codes before the structural base.
    """

    try:
        markdown = write()
    except StaleBodyError as error:
        return PageBodyPayload(ok=False, error=str(error), error_code="STALE_BODY")
    except UnsupportedPageKindError as error:
        return PageBodyPayload(ok=False, error=str(error), error_code="UNSUPPORTED_KIND")
    except SectionNotFoundError as error:
        return PageBodyPayload(ok=False, error=str(error), error_code="SECTION_NOT_FOUND")
    except AmbiguousMatchError as error:
        return PageBodyPayload(ok=False, error=str(error), error_code="AMBIGUOUS_MATCH")
    except StructuredEditError as error:
        return PageBodyPayload(ok=False, error=str(error), error_code="STRUCTURED_EDIT")
    return PageBodyPayload(ok=True, markdown=cast(MarkdownPageType, markdown))


class VaultWriteBackend(AngeeHasuraWriteBackend):
    """Write semantics for vaults: create belongs to the manager factory."""

    def _create_row(self, info: strawberry.Info, data: dict[str, Any]) -> Any:
        """Create a vault owned by the requesting user."""

        user = getattr(info.context.request, "user", None)
        fields = dict(data)
        # The factory stamps attribution; the shared backend already validated scope.
        fields.pop("created_by_id", None)
        return Vault._default_manager.create_for(user, **fields)


class PageWriteBackend(AngeeHasuraWriteBackend):
    """Write semantics for pages: create belongs to the manager factory."""

    def create(self, info: strawberry.Info, data: dict[str, Any], *, client_creation_key: str | None = None) -> Any:
        """Create a page in a vault the requesting user can write."""

        del info
        if client_creation_key is not None:
            raise ValidationError({"client_creation_key": "Page creation does not support creation keys."})
        vault = require_instance_for_id(Vault, data["vault"])
        parent = None
        if data.get("parent") is not None:
            parent = require_instance_for_id(Page, data["parent"])
        payload = dict(data)
        payload.pop("vault", None)
        payload.pop("parent", None)
        return Page._default_manager.create_in(vault, parent=parent, **payload)


_VAULT_EXTENSION_PUBLIC_ID_FIELDS = declared_hasura_write_relation_fields(Vault)
_VAULT_RESOURCE = hasura_model_resource(
    VaultType,
    model=Vault,
    name="vaults",
    filterable=["id", "name", "updated_at", *declared_hasura_resource_fields(Vault, "hasura_filterable_fields")],
    sortable=["name", "created_at", "updated_at", *declared_hasura_resource_fields(Vault, "hasura_sortable_fields")],
    aggregatable=["id", *declared_hasura_resource_fields(Vault, "hasura_aggregatable_fields")],
    groupable=["updated_at", *declared_hasura_resource_fields(Vault, "hasura_groupable_fields")],
    insertable=[
        "name",
        "description",
        "icon",
        "accent",
        *declared_hasura_resource_fields(Vault, "hasura_insertable_fields"),
    ],
    updatable=[
        "name",
        "description",
        "icon",
        "accent",
        *declared_hasura_resource_fields(Vault, "hasura_updatable_fields"),
    ],
    field_id_decode={
        name: public_pk_decoder(Vault._meta.get_field(name).related_model)
        for name in _VAULT_EXTENSION_PUBLIC_ID_FIELDS
    },
    write_backend=VaultWriteBackend(Vault, public_id_fields=_VAULT_EXTENSION_PUBLIC_ID_FIELDS),
)
_PAGE_RESOURCE = hasura_model_resource(
    PageType,
    model=Page,
    name="pages",
    filterable=["id", "vault", "title", "is_trashed", "updated_at"],
    sortable=["title", "created_at", "updated_at", "trashed_at"],
    aggregatable=["id"],
    groupable=["vault", "vault__name", "updated_at"],
    updatable=["title", "icon", "parent"],
    field_id_decode={
        "vault": public_pk_decoder(Vault),
        "parent": public_pk_decoder(Page),
    },
    write_backend=PageWriteBackend(Page, public_id_fields=("parent",)),
    get_queryset=partial(PageType.get_queryset, Page.objects),
    subtitle=DataResourceSubtitleMetadata(word_count="markdown.word_count"),
)


def _record_for_binding(
    model_label: str,
    record_id: PublicID,
    *,
    write: bool = False,
) -> Any | None:
    """Resolve an arbitrary REBAC record through the ambient actor's row scope."""

    try:
        model = apps.get_model(str(model_label).strip())
    except LookupError, ValueError:
        return None
    if model_resource_type(model) is None:
        return None
    queryset = write_scoped_queryset(model) if write else None
    return instance_from_public_id(model, str(record_id), queryset=queryset)


def _knowledge_for_binding(
    input: RecordBindingInput,
    *,
    write: bool = False,
) -> tuple[Any | None, Any | None]:
    """Resolve exactly one Page/Vault owner through the ambient actor's row scope."""

    if (input.page is None) == (input.vault is None):
        raise ValueError("Exactly one of page or vault is required.")
    if input.page is not None:
        queryset = write_scoped_queryset(Page) if write else None
        return require_instance_for_id(Page, input.page, queryset=queryset), None
    queryset = write_scoped_queryset(Vault) if write else None
    return None, require_instance_for_id(Vault, cast(PublicID, input.vault), queryset=queryset)


@strawberry.type
class KnowledgeQuery:
    """Knowledge content queries that span the page/body join."""

    @strawberry.field
    def search_pages(self, query: str, vault: PublicID | None = None, first: int = 20) -> list[PageType]:
        """Search a selected readable vault, or bounded readable backend groups."""
        rows = Vault.objects.all()
        if vault is not None:
            target = require_instance_for_id(Vault, vault)
            rows = rows.filter(pk=target.pk)
        return cast("list[PageType]", rows.search_pages(query, first=first))

    @strawberry.field(name="record_knowledge_bindings")
    def record_knowledge_bindings(
        self,
        model_label: str,
        record_id: PublicID,
        role: str | None = None,
    ) -> list[RecordBindingType]:
        """Return Page/Vault references bound to one actor-readable record."""

        record = _record_for_binding(model_label, record_id)
        if record is None:
            return []
        return cast(
            "list[RecordBindingType]",
            list(RecordBinding._default_manager.for_record(record, role=role)),
        )

    @strawberry.field(name="record_knowledge_can_bind")
    def record_knowledge_can_bind(self, model_label: str, record_id: PublicID) -> bool:
        """Report whether the record's type carries bindings and the actor writes it.

        Page writes remain independent.
        """

        record = _record_for_binding(model_label, record_id)
        return (
            record is not None
            and RecordBinding.declares_target(type(record))
            and bool(record.has_access("write"))
        )

    @strawberry.field(name="page_record_bindings")
    def page_record_bindings(
        self,
        page: PublicID,
        role: str | None = None,
    ) -> list[RecordBindingType]:
        """Return actor-readable target references bound from one readable page."""

        source = require_instance_for_id(Page, page)
        return cast(
            "list[RecordBindingType]",
            list(RecordBinding._default_manager.for_knowledge(source, role=role)),
        )

    @strawberry.field(name="vault_record_bindings")
    def vault_record_bindings(
        self,
        vault: PublicID,
        role: str | None = None,
    ) -> list[RecordBindingType]:
        """Return actor-readable target references bound from one readable vault."""

        source = require_instance_for_id(Vault, vault)
        return cast(
            "list[RecordBindingType]",
            list(RecordBinding._default_manager.for_knowledge(source, role=role)),
        )


@strawberry.type
class KnowledgeMutation:
    """Vault, binding and markdown writes owned by knowledge."""

    @strawberry.mutation(name="create_vault_from")
    @action_guard("Could not create the vault from this template.", errors=(UnsupportedPageKindError,))
    def create_vault_from(
        self,
        info: strawberry.Info,
        template: PublicID,
        name: str,
        owned: bool = True,
        client_creation_key: str | None = None,
    ) -> ActionResult:
        """Clone a readable template, optionally ownerless and replay-safe."""

        session_user(info)
        # Decode only identity: the manager resolves replay before reading the template.
        field = cast(SqidField, Vault._meta.get_field("sqid"))
        source = Vault(pk=field.public_id_to_value(template))
        vault = Vault._default_manager.create_from(
            source, name=name, owned=owned, client_creation_key=client_creation_key,
        )
        return ActionResult(ok=True, message="Vault created.", id=require_public_id(Vault, vault.pk))

    @strawberry.mutation(name="create_page")
    def create_page(
        self, vault: PublicID, title: str, kind: str = "note", parent: PublicID | None = None
    ) -> PageType:
        """Create the requested concrete page through its vault-owned preflight."""

        vault_row = require_instance_for_id(Vault, vault)
        parent_row = require_instance_for_id(Page, parent) if parent is not None else None
        return cast(PageType, Page._default_manager.create_in(vault_row, title=title, kind=kind, parent=parent_row))

    @strawberry.mutation(name="bind_knowledge_record")
    def bind_knowledge_record(self, input: RecordBindingInput) -> RecordBindingType:
        """Idempotently bind one writable Page/Vault to one writable record."""

        page, vault = _knowledge_for_binding(input, write=True)
        target = _record_for_binding(input.model_label, input.record_id, write=True)
        if target is None:
            raise ValueError("Record not found.")
        return cast(
            RecordBindingType,
            RecordBinding._default_manager.upsert(
                page=page,
                vault=vault,
                target=target,
                role=input.role,
            ),
        )

    @strawberry.mutation(name="unbind_knowledge_record")
    def unbind_knowledge_record(self, input: RecordBindingInput) -> bool:
        """Remove one Page/Vault-to-record role key if it exists."""

        page, vault = _knowledge_for_binding(input, write=True)
        target = _record_for_binding(input.model_label, input.record_id, write=True)
        if target is None:
            raise ValueError("Record not found.")
        RecordBinding._default_manager.unbind(
            page=page,
            vault=vault,
            target=target,
            role=input.role,
        )
        return True

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
        return _markdown_write_payload(
            lambda: MarkdownPage._default_manager.write_body(target, body, expected_hash=expected_hash)
        )

    @strawberry.mutation(name="patch_page_section")
    def patch_page_section(
        self,
        page: PublicID,
        heading_path: list[str],
        op: SectionOp,
        content: str,
        expected_hash: Annotated[
            str | None,
            strawberry.argument(name="expected_hash"),
        ] = None,
    ) -> PageBodyPayload:
        """Replace/append/prepend the section at ``heading_path`` in a page body.

        Mirrors :meth:`update_page_body`: same ``PublicID`` resolution, same
        :class:`PageBodyPayload`, same CAS via ``expected_hash``; the splice is
        a fail-fast structured edit (``SECTION_NOT_FOUND``/``AMBIGUOUS_MATCH``).
        """

        target = require_instance_for_id(Page, page)
        return _markdown_write_payload(
            lambda: MarkdownPage._default_manager.patch_section(
                target, heading_path, op.value, content, expected_hash=expected_hash
            )
        )

    @strawberry.mutation(name="replace_page_text")
    def replace_page_text(
        self,
        page: PublicID,
        old: str,
        new: str,
        expected_hash: Annotated[
            str | None,
            strawberry.argument(name="expected_hash"),
        ] = None,
    ) -> PageBodyPayload:
        """Replace the single occurrence of ``old`` with ``new`` in a page body.

        Mirrors :meth:`update_page_body`; uniqueness is enforced by the markdown
        owner, so a non-unique or absent target fails fast with
        ``AMBIGUOUS_MATCH``/``SECTION_NOT_FOUND`` before any write.
        """

        target = require_instance_for_id(Page, page)
        return _markdown_write_payload(
            lambda: MarkdownPage._default_manager.replace_unique(target, old, new, expected_hash=expected_hash)
        )

    @strawberry.mutation(name="append_to_page")
    def append_to_page(
        self,
        page: PublicID,
        content: str,
        expected_hash: Annotated[
            str | None,
            strawberry.argument(name="expected_hash"),
        ] = None,
    ) -> PageBodyPayload:
        """Append ``content`` to the end of a page body.

        Mirrors :meth:`update_page_body`: same ``PublicID`` resolution, same
        :class:`PageBodyPayload`, same CAS via ``expected_hash``; the markdown
        owner joins ``content`` one blank line after the body (no markdown
        re-rendered), so the section seam stays consistent.
        """

        target = require_instance_for_id(Page, page)
        return _markdown_write_payload(
            lambda: MarkdownPage._default_manager.append(target, content, expected_hash=expected_hash)
        )


attach_delete_preview_metadata(
    KnowledgeMutation,
    model=Vault,
    node=VaultType,
    field="delete_vault",
)
attach_delete_preview_metadata(
    KnowledgeMutation,
    model=Page,
    node=PageType,
    field="delete_page",
)


@strawberry_django.type(Page, name="PageType", extend=True)
class PageTags(TaggedNode):
    """Console-only tags on a page; the public page node stays unchanged."""


_KNOWLEDGE_SCHEMA_BUCKET = {
    "query": [
        KnowledgeQuery,
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
        RecordBindingType,
        RecordBindingInput,
        MarkdownPageType,
        OutlineEntryType,
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
            changes(RecordBinding, field="knowledgeRecordBindingChanged"),
        ],
        "type_extensions": [PageTags],
        "input_extensions": tags_input_extensions(_PAGE_RESOURCE),
    },
}
