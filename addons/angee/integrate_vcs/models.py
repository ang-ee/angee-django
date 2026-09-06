"""VCS capability models and inventory ownership."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any, cast

from django.apps import apps
from django.db import models, transaction
from django.utils import timezone
from rebac import system_context

from angee.base.fields import EncryptedField, StateField
from angee.base.impl import ImplClassField
from angee.base.mixins import AuditMixin, SqidMixin
from angee.base.models import AngeeManager, AngeeModel
from angee.integrate.models import Bridge
from angee.integrate_vcs import registry
from angee.integrate_vcs.backend import VCSBackend
from angee.integrate_vcs.templates import parse_template_meta


class RepoVisibility(models.TextChoices):
    """Visibility of a git remote on its host."""

    PUBLIC = "public", "Public"
    PRIVATE = "private", "Private"
    INTERNAL = "internal", "Internal"


class VcsBridge(Bridge):
    """The VCS sync child model over ``Integration``.

    A :class:`Bridge`: the scheduler refreshes its repositories' sources over the
    host REST API and an inbound push webhook triggers the same refresh. The
    host-specific wire format is the integration child row's non-model
    :class:`~angee.integrate_vcs.backend.VCSBackend` implementation — so
    github/gitlab/bitbucket share this one table, differing only in behavior.
    Django keeps the inventory only; the operator performs every git operation,
    consuming :meth:`Source.materialize_spec`.
    """

    runtime = True
    extends = "integrate.Integration"
    integration_create_mode = "FORM"
    integration_kind_label = "VCS bridge"

    backend_class = ImplClassField(
        base_class=VCSBackend,
        registry_setting="ANGEE_VCS_BACKEND_CLASSES",
        default="local",
        create_only=True,
    )
    """Registry key for the VCS backend bound to this bridge."""
    webhook_secret = EncryptedField(blank=True)
    """Shared secret for verifying inbound push webhooks (per account, not per repo)."""

    class Meta:
        """Django model options for the VCS bridge child model."""

        abstract = True
        db_table = "integrate_vcsbridge"
        ordering = ("-updated_at",)
        rebac_resource_type = "integrate_vcs/vcs_bridge"
        rebac_id_attr = "sqid"

    @property
    def backend(self) -> VCSBackend:
        """Return this bridge's selected VCS backend."""

        backend_class = cast(type[VCSBackend], self.resolve_impl("backend_class"))
        return backend_class(self)

    def repositories_by_org(self) -> dict[str, list[Any]]:
        """Return every visible repository grouped and sorted by owning org."""

        groups: dict[str, list[Any]] = {}
        for descriptor in self.backend.ls_repos():
            groups.setdefault(descriptor.org, []).append(descriptor)
        return {org: sorted(repos, key=lambda item: item.name) for org, repos in sorted(groups.items())}

    def discover(self, source: Any, *, marker: str, parse: Callable[[bytes], dict[str, Any]]) -> list[dict[str, Any]]:
        """Return one descriptor per directory under ``source`` bearing ``marker``.

        The single enumeration walk shared by every source kind: list the source's
        subtree, read each ``marker`` blob, parse it, record the bearing directory,
        and return the descriptors in deterministic order. A source kind's output
        manager supplies only its ``marker`` filename and ``parse`` function.
        """

        backend = self.backend
        repository = source.repository
        ref = source.ref or repository.default_branch
        descriptors: list[dict[str, Any]] = []
        for entry in backend.ls_tree(repository, ref=ref, path=source.path, recursive=True):
            if entry.type != "blob" or entry.name != marker:
                continue
            descriptor = dict(parse(backend.cat_file(repository, ref=ref, path=entry.path)))
            descriptor.setdefault("path", _parent_path(entry.path))
            descriptors.append(descriptor)
        return sorted(descriptors, key=_descriptor_key)

    def sync(self) -> int:
        """Refresh every inventoried repository's sources over REST (Bridge contract).

        Repository discovery (creating rows from the account) is the explicit
        ``discoverRepositories`` action; the scheduled/webhook ``sync`` refreshes the
        content of already-inventoried repositories.
        """

        source_model = apps.get_model("integrate_vcs", "Source")
        with system_context(reason="integrate.vcs_bridge.sync.sources"):
            sources = tuple(
                source_model.objects.filter(repository__vcs_bridge=self)
                .select_related("repository", "repository__vcs_bridge")
                .order_by("repository_id", "pk")
            )
        return sum(source.refresh() for source in sources)

    def handle_webhook(self, payload: Any) -> None:
        """Re-sync this bridge's inventory on an inbound push webhook."""

        del payload
        self.sync()

    def verify_webhook(self, request: Any) -> bool:
        """Return whether an inbound push webhook is authentic for this bridge."""

        return self.backend.verify_webhook(self, request)

    def search_repositories(self, query: str) -> list[Any]:
        """Return host repositories whose name matches ``query`` (the add typeahead)."""

        backend = self.backend
        return backend.search_repos(query, org=backend.repository_search_scope())

    def import_repository(self, name: str) -> Any:
        """Inventory one repository by its host ``name`` (a picked typeahead result)."""

        repository_model = apps.get_model("integrate_vcs", "Repository")
        return repository_model.objects.add(self, self.backend.get_repo(name))

    def discover_repositories(self, *, org: str = "") -> int:
        """Inventory every repository the account exposes (bulk import; prunes vanished)."""

        repository_model = apps.get_model("integrate_vcs", "Repository")
        return repository_model.objects.reconcile(self, self.backend.ls_repos(org=org))


class RepositoryManager(AngeeManager):
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
        with system_context(reason="integrate.repository.reconcile"), transaction.atomic():
            self.bulk_create(
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
            self.filter(vcs_bridge=vcs_bridge).exclude(name__in=descriptors_by_name).delete()
        return len(descriptor_list)

    def add(self, vcs_bridge: Any, descriptor: Any) -> Any:
        """Inventory one repository (no prune) — the typeahead "add this repo" path."""

        with system_context(reason="integrate.repository.add"), transaction.atomic():
            return self._upsert(vcs_bridge, descriptor)

    def _upsert(self, vcs_bridge: Any, descriptor: Any) -> Any:
        """Create or update one repository row from a host descriptor."""

        repository, _created = self.update_or_create(
            vcs_bridge=vcs_bridge,
            name=descriptor.name,
            defaults={
                "org": descriptor.org,
                "remote": descriptor.remote,
                "ssh_remote": descriptor.ssh_remote,
                "remote_id": descriptor.remote_id,
                "default_branch": descriptor.default_branch,
                "visibility": descriptor.visibility,
                "web_url": descriptor.web_url,
                "archived": descriptor.archived,
            },
        )
        return repository

    def _row_from_descriptor(self, vcs_bridge: Any, descriptor: Any, *, now: datetime) -> Any:
        """Return an unsaved repository row projected from one host descriptor."""

        return self.model(
            vcs_bridge=vcs_bridge,
            name=descriptor.name,
            org=descriptor.org,
            remote=descriptor.remote,
            ssh_remote=descriptor.ssh_remote,
            remote_id=descriptor.remote_id,
            default_branch=descriptor.default_branch,
            visibility=descriptor.visibility,
            web_url=descriptor.web_url,
            archived=descriptor.archived,
            created_at=now,
            updated_at=now,
        )


class Repository(SqidMixin, AuditMixin, AngeeModel):
    """Inventory of one git remote, reached through its ``VcsBridge``.

    A plain noun: Django records the remote; the operator clones it. ``org`` groups
    the account's repositories in the browse list.
    """

    runtime = True

    sqid_prefix = "repo_"
    vcs_bridge = models.ForeignKey(
        "integrate_vcs.VcsBridge",
        on_delete=models.CASCADE,
        related_name="repositories",
    )
    org = models.CharField(max_length=255, db_index=True)
    name = models.CharField(max_length=255)
    """The repository's ``owner/repo`` path on its remote host."""
    remote = models.CharField(max_length=512)
    """The HTTPS remote URL the operator clones."""
    ssh_remote = models.CharField(max_length=255, blank=True)
    remote_id = models.CharField(max_length=128, blank=True)
    default_branch = models.CharField(max_length=255, default="main")
    visibility = StateField(choices_enum=RepoVisibility, default=RepoVisibility.PRIVATE)
    web_url = models.URLField(blank=True)
    archived = models.BooleanField(default=False)

    objects = RepositoryManager()

    class Meta:
        """Django model options for repository inventory."""

        abstract = True
        db_table = "integrate_repository"
        ordering = ("org", "name")
        rebac_resource_type = "integrate_vcs/repository"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("vcs_bridge", "name"),
                name="uniq_integrate_repository_name",
            ),
        )

    def __str__(self) -> str:
        """Return the repository's host path."""

        return self.name


class Source(SqidMixin, AuditMixin, AngeeModel):
    """A pointer into a ``Repository`` at a ``ref`` and ``path``, with a ``kind``.

    One noun for every source kind. ``kind`` binds the source to an output model
    (``Template``/``Skill``) whose manager reconciles its rows; :meth:`refresh`
    dispatches there. The operator materializes a source from
    :meth:`materialize_spec`.
    """

    runtime = True

    sqid_prefix = "src_"
    repository = models.ForeignKey("integrate_vcs.Repository", on_delete=models.CASCADE, related_name="sources")
    kind = models.CharField(max_length=64)
    """The source kind (e.g. ``template``, ``skill``); resolves to an output model."""
    ref = models.CharField(max_length=255, blank=True)
    """Branch, tag, or commit oid; blank resolves to the repository's default branch."""
    path = models.CharField(max_length=1024, blank=True)
    """Pathspec of the subtree this source points at within the repository."""
    last_synced_at = models.DateTimeField(null=True, blank=True)

    objects = AngeeManager()

    class Meta:
        """Django model options for source inventory."""

        abstract = True
        db_table = "integrate_source"
        ordering = ("kind", "path")
        rebac_resource_type = "integrate_vcs/source"
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return a kind-qualified source label."""

        return f"{self.kind}:{self.path or '/'}"

    @classmethod
    def kind_models(cls) -> tuple[type[models.Model], ...]:
        """Return the output models that declare a ``source_kind`` (e.g. ``Template``).

        ``Source`` owns "what a kind resolves to": an output model binds itself to a
        kind with a ``source_kind`` class attribute; the integration registry owns
        the deterministic app scan and contract check.
        """

        return registry.source_kind_models()

    @classmethod
    def available_kinds(cls) -> tuple[str, ...]:
        """Return the source kinds any installed addon contributes an output model for."""

        return tuple(sorted({str(model.source_kind) for model in cls.kind_models()}))

    @classmethod
    def target_for_kind(cls, kind: str) -> type[models.Model]:
        """Return the output model bound to one source ``kind`` or raise."""

        for model in cls.kind_models():
            if model.source_kind == kind:
                return model
        known = ", ".join(cls.available_kinds()) or "none registered"
        raise ValueError(f"No output model for source kind {kind!r} (known: {known}).")

    def refresh(self) -> int:
        """Re-enumerate over REST into the kind's output rows; return the row count."""

        return int(type(self).target_for_kind(self.kind).objects.sync_from_source(self))

    def materialize_spec(self) -> dict[str, str]:
        """Return the operator handoff coordinates to clone and check out this source."""

        repository = self.repository
        return {
            "remote": str(repository.remote),
            "ssh_remote": str(repository.ssh_remote),
            "ref": str(self.ref or repository.default_branch),
            "path": str(self.path),
        }


class TemplateManager(AngeeManager):
    """Manager owning the reconcile of template rows from a template source."""

    def sync_from_source(self, source: Any) -> int:
        """Walk the source for ``copier.yml`` and upsert/prune ``Template`` rows."""

        vcs_bridge = source.repository.vcs_bridge
        descriptors = vcs_bridge.discover(source, marker="copier.yml", parse=parse_template_meta)
        descriptors_by_path = {str(descriptor.get("path", "")): descriptor for descriptor in descriptors}
        now = timezone.now()
        with system_context(reason="integrate.template.sync"), transaction.atomic():
            self.bulk_create(
                [self._row_from_descriptor(source, descriptor, now=now) for descriptor in descriptors_by_path.values()],
                update_conflicts=True,
                unique_fields=["source", "path"],
                update_fields=["name", "kind", "inputs", "updated_at"],
            )
            self.filter(source=source).exclude(path__in=descriptors_by_path).delete()
            source.last_synced_at = now
            source.save(update_fields=["last_synced_at", "updated_at"])
        return len(descriptors)

    def _row_from_descriptor(self, source: Any, descriptor: dict[str, Any], *, now: datetime) -> Any:
        """Return an unsaved template row projected from one discovered descriptor."""

        return self.model(
            source=source,
            path=str(descriptor.get("path", "")),
            name=str(descriptor.get("name", "")),
            kind=str(descriptor.get("kind", "")),
            inputs=list(descriptor.get("inputs", [])),
            created_at=now,
            updated_at=now,
        )


class Template(SqidMixin, AuditMixin, AngeeModel):
    """One Copier template discovered under a ``Source`` (``source_kind="template"``).

    The operator renders these; the kind here is the *template* kind from the
    manifest's ``_angee.kind`` (stack/workspace/service).
    """

    runtime = True
    source_kind = "template"
    """Binds the ``template`` source kind to this output model (see ``registry``)."""

    sqid_prefix = "tpl_"
    source = models.ForeignKey("integrate_vcs.Source", on_delete=models.CASCADE, related_name="templates")
    name = models.CharField(max_length=255, blank=True)
    kind = models.CharField(max_length=64, blank=True)
    """The template kind from ``_angee.kind`` (stack/workspace/service)."""
    path = models.CharField(max_length=1024, blank=True)
    inputs = models.JSONField(default=list, blank=True)

    objects = TemplateManager()

    class Meta:
        """Django model options for discovered templates."""

        abstract = True
        db_table = "integrate_template"
        ordering = ("kind", "name")
        rebac_resource_type = "integrate_vcs/template"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("source", "path"),
                name="uniq_integrate_template_path",
            ),
        )

    def __str__(self) -> str:
        """Return a kind-qualified template label."""

        return f"{self.kind}:{self.name or self.path}"


def _parent_path(path: str) -> str:
    """Return the directory containing ``path`` (empty string at the root)."""

    return path.rsplit("/", 1)[0] if "/" in path else ""


def _descriptor_key(descriptor: dict[str, Any]) -> tuple[str, str]:
    """Return a stable ``(kind, name|path)`` sort key for one discovered descriptor."""

    return (str(descriptor.get("kind", "")), str(descriptor.get("name") or descriptor.get("path", "")))
