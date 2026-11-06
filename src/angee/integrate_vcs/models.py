"""VCS capability models and inventory ownership."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, ClassVar

from django.apps import apps
from django.db import models
from django.utils import timezone
from rebac import system_context

from angee.base.fields.implementation import ImplClassField
from angee.base.fields.state import StateField
from angee.base.models.audit import AuditMixin
from angee.base.models.persistence import require_composed_type
from angee.iam.managers import ProtectedManager
from angee.iam.models.base import ProtectedDataModel
from angee.integrate.models import Bridge, IntegrationCreateMode
from angee.integrate_vcs import registry
from angee.integrate_vcs.backend import VCSBackend
from angee.integrate_vcs.managers import RepositoryManager, TemplateManager


class RepoVisibility(models.TextChoices):
    """Visibility of a git remote on its host."""

    PUBLIC = "public", "Public"
    PRIVATE = "private", "Private"
    INTERNAL = "internal", "Internal"


class VcsBridge(Bridge, inherits="integrate.Integration"):
    """The VCS sync child model over ``Integration``.

    A :class:`Bridge`: the scheduler refreshes its repositories' sources over the
    host REST API. The
    host-specific wire format is the integration child row's non-model
    :class:`~angee.integrate_vcs.backend.VCSBackend` implementation — so
    github/gitlab/bitbucket share this one table, differing only in behavior.
    Django keeps the inventory only; the operator performs every git operation,
    consuming :meth:`Source.materialize_spec`.
    """

    integration_create_mode: ClassVar[IntegrationCreateMode | None] = IntegrationCreateMode.FORM

    backend_class = ImplClassField(
        VCSBackend,
        default="local",
        create_only=True,
    )
    """Registry key for the VCS backend bound to this bridge."""

    class Meta:
        """Django model options for the VCS bridge child model."""

        abstract = True
        db_table = "integrate_vcsbridge"
        verbose_name = "VCS bridge"
        ordering = ("-updated_at",)
        rebac_resource_type = "integrate_vcs/vcs_bridge"

    @property
    def backend(self) -> VCSBackend:
        """Return this bridge's selected VCS backend."""

        backend_class = self.get_backend_class_impl()
        return backend_class(self)

    def discover(self, source: Any, *, marker: str, parse: Callable[[bytes], dict[str, Any]]) -> list[dict[str, Any]]:
        """Return one descriptor per directory under ``source`` bearing ``marker``.

        The single enumeration walk shared by every source kind: list the source's
        subtree, read each ``marker`` blob, parse it, record the bearing directory,
        and return the descriptors in deterministic order. A source kind's output
        manager supplies only its ``marker`` filename and ``parse`` function.
        """

        backend = self.backend
        repository = source.repository
        ref = backend.rev_parse(repository, source.ref or repository.default_branch)
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
        ``discoverRepositories`` action; the scheduled ``sync`` refreshes the
        content of already-inventoried repositories.
        """

        source_model = apps.get_model("integrate_vcs", "Source")
        with system_context(reason="integrate.vcs_bridge.sync.sources"):
            sources = tuple(
                source_model.objects.db_manager(self._state.db).filter(repository__vcs_bridge=self)
                .select_related("repository", "repository__vcs_bridge")
                .order_by("repository_id", "pk")
            )
        return sum(source.refresh() for source in sources)

    def search_repositories(self, query: str) -> list[Any]:
        """Return host repositories whose name matches ``query`` (the add typeahead)."""

        backend = self.backend
        return backend.search_repos(query, org=backend.repository_search_scope())

    def import_repository(self, name: str) -> Any:
        """Inventory one repository by its host ``name`` (a picked typeahead result)."""

        repository_model = apps.get_model("integrate_vcs", "Repository")
        return repository_model.objects.db_manager(self._state.db).add(self, self.backend.get_repo(name))

    def discover_repositories(self, *, org: str = "") -> int:
        """Inventory every repository the account exposes (bulk import; prunes vanished)."""

        repository_model = apps.get_model("integrate_vcs", "Repository")
        return repository_model.objects.db_manager(self._state.db).reconcile(self, self.backend.ls_repos(org=org))




class Repository(AuditMixin, ProtectedDataModel, concrete=True):
    """Inventory of one git remote, reached through its ``VcsBridge``.

    A plain noun: Django records the remote; the operator clones it. ``org`` groups
    the account's repositories in the browse list.
    """

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
        verbose_name_plural = "repositories"
        rebac_resource_type = "integrate_vcs/repository"
        constraints = (
            models.UniqueConstraint(
                fields=("vcs_bridge", "name"),
                name="uniq_integrate_repository_name",
            ),
        )

    def __str__(self) -> str:
        """Return the repository's host path."""

        return self.name


class Source(AuditMixin, ProtectedDataModel, concrete=True):
    """A pointer into a ``Repository`` at a ``ref`` and ``path``, with a ``kind``.

    One noun for every source kind. ``kind`` binds the source to an output model
    (``Template``/``Skill``) whose manager reconciles its rows; :meth:`refresh`
    dispatches there. The operator materializes a source from
    :meth:`materialize_spec`.
    """

    sqid_prefix = "src_"
    repository = models.ForeignKey("integrate_vcs.Repository", on_delete=models.CASCADE, related_name="sources")
    kind = models.CharField(max_length=64)
    """The source kind (e.g. ``template``, ``skill``); resolves to an output model."""
    ref = models.CharField(max_length=255, blank=True)
    """Branch, tag, or commit oid; blank resolves to the repository's default branch."""
    path = models.CharField(max_length=1024, blank=True)
    """Pathspec of the subtree this source points at within the repository."""
    last_synced_at = models.DateTimeField(null=True, blank=True)

    objects = ProtectedManager()

    class Meta:
        """Django model options for source inventory."""

        abstract = True
        db_table = "integrate_source"
        ordering = ("kind", "path")
        rebac_resource_type = "integrate_vcs/source"

    def __str__(self) -> str:
        """Return a kind-qualified source label."""

        return f"{self.kind}:{self.path or '/'}"

    @classmethod
    def kind_models(cls) -> tuple[type[registry.VcsOutput], ...]:
        """Return the output models that declare a ``source_kind`` (e.g. ``Template``).

        ``Source`` owns "what a kind resolves to": an output model binds itself to a
        kind by composing ``VcsOutput`` and declaring ``source_kind``; the registry owns
        the deterministic app scan and contract check.
        """

        return registry.source_kind_models()

    @classmethod
    def available_kinds(cls) -> tuple[str, ...]:
        """Return the source kinds any installed addon contributes an output model for."""

        return tuple(sorted({str(model.source_kind) for model in cls.kind_models()}))

    @classmethod
    def target_for_kind(cls, kind: str) -> type[registry.VcsOutput]:
        """Return the output model bound to one source ``kind`` or raise."""

        for model in cls.kind_models():
            if model.source_kind == kind:
                return model
        known = ", ".join(cls.available_kinds()) or "none registered"
        raise ValueError(f"No output model for source kind {kind!r} (known: {known}).")

    def complete_sync(self, *, now: datetime | None = None) -> None:
        """Stamp successful output persistence inside its caller's transaction."""

        self.last_synced_at = now or timezone.now()
        self.save(using=self._state.db, update_fields=["last_synced_at", "updated_at"])

    def refresh(self) -> int:
        """Re-enumerate over REST into the kind's output rows; return the row count."""

        manager = require_composed_type(
            type(self).target_for_kind(self.kind)._default_manager.db_manager(
                self._state.db, hints={"instance": self},
            ),
            registry.VcsOutputManager,
        )
        return manager.sync_from_source(self)

    def materialize_spec(self) -> dict[str, str]:
        """Return the operator handoff coordinates to clone and check out this source."""

        repository = self.repository
        return {
            "remote": str(repository.remote),
            "ssh_remote": str(repository.ssh_remote),
            "ref": str(self.ref or repository.default_branch),
            "path": str(self.path),
        }




class Template(registry.VcsOutput, AuditMixin, ProtectedDataModel, concrete=True):
    """One Copier template discovered under a ``Source`` (``source_kind="template"``).

    The operator renders these; the kind here is the *template* kind from the
    manifest's ``_angee.kind`` (stack/workspace/service).
    """

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
