"""VCS capability models and inventory ownership."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from django.db import transaction
from django.utils import timezone
from rebac import database_context, system_context

from angee.iam.managers import ProtectedManager
from angee.integrate_vcs import registry
from angee.integrate_vcs.templates import parse_template_meta

if TYPE_CHECKING:
    from angee.integrate_vcs.models import Repository, Template


class RepositoryManager[RepositoryT: Repository](ProtectedManager[RepositoryT]):
    """Manager owning the upsert/reconcile of repository rows from a host listing."""

    def reconcile(self, vcs_bridge: Any, descriptors: Iterable[Any]) -> int:
        """Upsert one repository row per descriptor and prune rows that vanished.

        Bulk import for ``discoverRepositories``: prunes against the full listing,
        so the caller must pass every repository (see ``GitHubBackend.ls_repos``
        pagination), never a partial page.
        """

        descriptor_list = list(descriptors)
        descriptors_by_name = {str(descriptor.name): descriptor for descriptor in descriptor_list}
        now = timezone.now()
        using = self._db or vcs_bridge._state.db or self.db
        rows = self.db_manager(using)
        with (
            database_context(using),
            system_context(reason="integrate.repository.reconcile"),
            transaction.atomic(using=using),
        ):
            rows.bulk_create(
                [
                    self._row_from_descriptor(vcs_bridge, descriptor, now=now)
                    for descriptor in descriptors_by_name.values()
                ],
                update_conflicts=True,
                unique_fields=["vcs_bridge", "name"],
                update_fields=[
                    "org",
                    "remote",
                    "ssh_remote",
                    "remote_id",
                    "default_branch",
                    "visibility",
                    "web_url",
                    "archived",
                    "updated_at",
                ],
            )
            rows.filter(vcs_bridge=vcs_bridge).exclude(name__in=descriptors_by_name).delete()
        return len(descriptor_list)

    def add(self, vcs_bridge: Any, descriptor: Any) -> Any:
        """Inventory one repository (no prune) — the typeahead "add this repo" path."""

        using = self._db or vcs_bridge._state.db or self.db
        with (
            database_context(using),
            system_context(reason="integrate.repository.add"),
            transaction.atomic(using=using),
        ):
            repository, _created = self.db_manager(using).update_or_create(
                vcs_bridge_id=vcs_bridge.pk,
                name=descriptor.name,
                defaults=self._descriptor_values(descriptor),
            )
            return repository

    def _row_from_descriptor(self, vcs_bridge: Any, descriptor: Any, *, now: datetime) -> Any:
        """Return an unsaved repository row projected from one host descriptor."""

        return self.model(
            vcs_bridge_id=vcs_bridge.pk,
            name=descriptor.name,
            **self._descriptor_values(descriptor),
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _descriptor_values(descriptor: Any) -> dict[str, Any]:
        """Project mutable host metadata identically for single and bulk writes."""

        return {
            "org": descriptor.org,
            "remote": descriptor.remote,
            "ssh_remote": descriptor.ssh_remote,
            "remote_id": descriptor.remote_id,
            "default_branch": descriptor.default_branch,
            "visibility": descriptor.visibility,
            "web_url": descriptor.web_url,
            "archived": descriptor.archived,
        }


class TemplateManager[TemplateT: Template](ProtectedManager[TemplateT], registry.VcsOutputManager[TemplateT]):
    """Manager owning the reconcile of template rows from a template source."""

    def sync_from_source(self, source: Any) -> int:
        """Walk the source for ``copier.yml`` and upsert/prune ``Template`` rows."""

        using = self._db or source._state.db or self.db
        if source._state.db is not None and source._state.db != using:
            raise ValueError("Template reconciliation and its source must use the same database.")
        rows = self.db_manager(using)
        repository: Any = source.repository
        vcs_bridge = repository.vcs_bridge
        descriptors = vcs_bridge.discover(source, marker="copier.yml", parse=parse_template_meta)
        descriptors_by_path = {str(descriptor.get("path", "")): descriptor for descriptor in descriptors}
        now = timezone.now()
        with (
            database_context(using),
            system_context(reason="integrate.template.sync"),
            transaction.atomic(using=using),
        ):
            rows.bulk_create(
                [self._row_from_descriptor(source, descriptor, now=now) for descriptor in descriptors_by_path.values()],
                update_conflicts=True,
                unique_fields=["source", "path"],
                update_fields=["name", "kind", "inputs", "updated_at"],
            )
            rows.filter(source=source).exclude(path__in=descriptors_by_path).delete()
            source.complete_sync(now=now)
        return len(descriptors)

    def _row_from_descriptor(self, source: Any, descriptor: dict[str, Any], *, now: datetime) -> Any:
        """Return an unsaved template row projected from one discovered descriptor."""

        return self.model(
            source_id=source.pk,
            path=str(descriptor.get("path", "")),
            name=str(descriptor.get("name", "")),
            kind=str(descriptor.get("kind", "")),
            inputs=list(descriptor.get("inputs", [])),
            created_at=now,
            updated_at=now,
        )
