"""Notion's concrete connection over the shared Integration and Bridge owners."""

from __future__ import annotations

from typing import Any, ClassVar, cast

from django.apps import apps
from django.core.exceptions import PermissionDenied
from django.db import models, transaction
from rebac import actor_context

from angee.base.fields.implementation import ImplClassField
from angee.integrate.models import Bridge, IntegrationCreateMode
from angee.knowledge_integrate_notion.backend import NotionBackend


class NotionConnection(Bridge):
    """A Notion source whose replicas live in one owner-authorized knowledge vault."""

    runtime = True
    extends: ClassVar[str | None] = "integrate.Integration"
    integration_create_mode: ClassVar[IntegrationCreateMode | None] = IntegrationCreateMode.FORM
    backend_class = ImplClassField(NotionBackend, default="notion", create_only=True)
    rewrite_page_links = models.BooleanField(
        default=True,
        help_text="Link to imported Knowledge pages. Unimported pages and Notion block links keep their source URL.",
    )
    vault = models.ForeignKey(
        "knowledge.Vault",
        null=True,
        blank=True,
        editable=False,
        on_delete=models.PROTECT,
        related_name="notion_connections",
    )

    class Meta:
        """Compose a native integration child with inherited access and lifecycle."""

        abstract = True
        verbose_name = "Notion connection"
        rebac_resource_type = "knowledge_integrate_notion/notion_connection"

    def test_connection(self) -> str:
        """Perform the vendor handshake for the shared Test connection action."""

        backend = cast(NotionBackend, self.capability_impl)
        identity = backend.probe(backend.integration.credential)
        return f"Connected to Notion as {identity.name or identity.id}."

    def sync_owner(self) -> Any:
        """Require a live owner before reading or writing the private replica vault."""

        owner = self.owner
        if owner is None or not owner.is_active:
            raise PermissionDenied("Notion synchronization requires an active connection owner.")
        return owner

    def ensure_vault(self) -> Any:
        """Allocate the destination once; retain Knowledge's native owner and access policy."""

        vaults = apps.get_model("knowledge", "Vault").objects
        if self.vault_id is None:
            # Allocation precedes stream creation. Applying a page must not lock
            # its integration after its stream (epoch changes lock them in reverse).
            with transaction.atomic():
                row = self.lock_capability()
                if row.vault_id is None:
                    owner = row.sync_owner()
                    with actor_context(owner):
                        row.vault = vaults.create_for(owner, name=f"Notion {row.public_id}", icon="integration")
                        row.save(update_fields=["vault", "updated_at"])
        self.refresh_from_db(fields=["owner", "vault"])
        assert self.vault_id is not None
        vault = vaults.with_actor(self.sync_owner()).get(pk=self.vault_id)
        vault.require_access("write")
        if vault.is_trashed:
            raise PermissionDenied("Restore the Notion vault before synchronizing.")
        self.vault = vault
        return vault
