"""The host-agnostic git implementation contract.

A :class:`VCSBackend` is an ``Integration`` implementation that reads a repository
through a ``VcsBridge`` related model — either a host's remote over its REST API
(``integrate_github.GitHubBackend``) or a local working tree
(:class:`LocalVCSBackend`) — listing repositories, walking trees, reading blobs,
and resolving refs. A host backend never clones (git transport belongs to the
operator); the local backend reads files directly for dev/offline inventory.
``VcsBridge`` owns the shared enumeration walk over the primitives. This module
imports no models, so it breaks no import cycle.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

from angee.integrate.impl import BridgeImpl

# Directories the local backend never treats as source — a broad ``Source.path`` on a
# working tree would otherwise walk (and ingest stray ``copier.yml`` from) these.
_LOCAL_SKIP_DIRS = frozenset({".git", ".venv", "node_modules", "__pycache__", ".angee"})


@dataclass(frozen=True)
class TreeEntry:
    """One entry in a repository tree at a ref."""

    path: str
    """Full path of the entry relative to the repository root."""
    type: str
    """``"tree"`` for a directory, ``"blob"`` for a file."""
    oid: str = ""

    @property
    def name(self) -> str:
        """Return the entry's basename."""

        return self.path.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class RepoDescriptor:
    """A repository as reported by a host's repo listing.

    The shape :meth:`VCSBackend.ls_repos` returns and ``Repository`` reconcile
    upserts into rows.
    """

    name: str
    """The ``owner/repo`` path on the host."""
    org: str
    """The owning org/account login (used to group/sort the listing)."""
    remote: str
    """The HTTPS remote URL the operator clones."""
    ssh_remote: str = ""
    remote_id: str = ""
    default_branch: str = "main"
    visibility: str = "private"
    web_url: str = ""
    archived: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class VCSBackend(BridgeImpl):
    """Abstract REST backend for a git host, bound to one ``Integration``.

    Concrete hosts implement the primitives below; ``VcsBridge`` calls into
    them. The constructor receives the integration so the backend can read its
    credential (``integration.credential.auth_headers()``) and config.
    """

    category = "vcs"
    related_model = "integrate.VcsBridge"
    related_create_fields = ("webhook_secret",)
    label = "VCS"
    icon = "git-branch"

    def ls_repos(self, *, org: str = "") -> list[RepoDescriptor]:
        """List repositories visible to this integration, optionally within ``org``."""

        raise NotImplementedError("VCSBackend subclasses must implement ls_repos().")

    def ls_tree(self, repository: Any, *, ref: str, path: str, recursive: bool = False) -> list[TreeEntry]:
        """List the tree under ``path`` at ``ref`` (recursively when asked)."""

        raise NotImplementedError("VCSBackend subclasses must implement ls_tree().")

    def cat_file(self, repository: Any, *, ref: str, path: str) -> bytes:
        """Return the bytes of one blob at ``ref``; raise ``FileNotFoundError`` if absent."""

        raise NotImplementedError("VCSBackend subclasses must implement cat_file().")

    def rev_parse(self, repository: Any, ref: str) -> str:
        """Resolve ``ref`` to a commit oid."""

        raise NotImplementedError("VCSBackend subclasses must implement rev_parse().")

    def verify_webhook(self, vcs_integration: Any, request: Any) -> bool:
        """Return whether an inbound webhook request is authentic for this integration."""

        raise NotImplementedError("VCSBackend subclasses must implement verify_webhook().")

    def search_repos(self, query: str, *, org: str = "") -> list[RepoDescriptor]:
        """Return repositories whose name matches ``query`` — the typeahead source."""

        raise NotImplementedError("VCSBackend subclasses must implement search_repos().")

    def get_repo(self, name: str) -> RepoDescriptor:
        """Return one repository by its ``owner/repo`` name; raise ``FileNotFoundError`` if absent."""

        raise NotImplementedError("VCSBackend subclasses must implement get_repo().")


class LocalVCSBackend(VCSBackend):
    """Reads a repository straight from a local working tree — for dev/offline inventory.

    Where a host backend reads a remote over REST, this walks the filesystem under
    ``integration.config["local_root"]``, so templates (or skills) committed in the
    local checkout are inventoried through the same ``VcsBridge → Source →
    Template`` flow as a hosted remote, with no network. It reads the *working tree*;
    ``ref`` is informational — there is one repo and no commit to resolve.
    """

    key = "local"
    label = "Local checkout"
    defaults = {
        "config": {
            "local_root": "../..",
            "local_org": "local",
            "local_default_branch": "main",
        },
    }

    def ls_repos(self, *, org: str = "") -> list[RepoDescriptor]:
        """Return the single configured local repository."""

        del org
        return [self._descriptor()]

    def ls_tree(self, repository: Any, *, ref: str, path: str, recursive: bool = False) -> list[TreeEntry]:
        """Walk the working tree under ``path``; entry paths are relative to the repo root.

        Prunes ``_LOCAL_SKIP_DIRS`` — a working-tree-only concern (a REST host returns
        committed tree entries and has no such noise), so the filter lives here, not in
        the host-agnostic ``VcsBridge.discover`` walk.
        """

        del repository, ref
        base = self._safe_join(path)
        if not base.is_dir():
            return []
        children = sorted(base.rglob("*") if recursive else base.iterdir())
        entries: list[TreeEntry] = []
        for child in children:
            relative = child.relative_to(self._root)
            if _LOCAL_SKIP_DIRS.intersection(relative.parts):
                continue
            entries.append(TreeEntry(path=relative.as_posix(), type="tree" if child.is_dir() else "blob"))
        return entries

    def cat_file(self, repository: Any, *, ref: str, path: str) -> bytes:
        """Return one working-tree file's bytes; raise if absent or a directory."""

        del repository, ref
        target = self._safe_join(path)
        if target.is_dir():
            raise IsADirectoryError(path)
        try:
            return target.read_bytes()
        except FileNotFoundError:
            raise FileNotFoundError(path) from None

    def rev_parse(self, repository: Any, ref: str) -> str:
        """Return ``ref`` unchanged — a local working tree resolves no commit oid."""

        del repository
        return ref

    def search_repos(self, query: str, *, org: str = "") -> list[RepoDescriptor]:
        """Return the local repository when ``query`` matches its name (typeahead)."""

        del org
        descriptor = self._descriptor()
        return [descriptor] if query.lower() in descriptor.name.lower() else []

    def get_repo(self, name: str) -> RepoDescriptor:
        """Return the local repository; raise ``FileNotFoundError`` on a name mismatch."""

        descriptor = self._descriptor()
        if name and name != descriptor.name:
            raise FileNotFoundError(name)
        return descriptor

    def verify_webhook(self, vcs_integration: Any, request: Any) -> bool:
        """Reject inbound webhooks: a local working tree has no host to authenticate."""

        del vcs_integration, request
        return False

    @property
    def _root(self) -> pathlib.Path:
        """Return the configured working-tree root (resolved), or raise when unset.

        A relative ``local_root`` resolves against ``settings.BASE_DIR`` (the project
        dir), so a fixture can point at the checkout portably (e.g. ``"../.."``) rather
        than hardcoding an absolute path; an absolute ``local_root`` is used as-is.
        """

        root = str(self.integration.config.get("local_root") or "").strip()
        if not root:
            raise FileNotFoundError("LocalVCSBackend requires integration.config['local_root'].")
        path = pathlib.Path(root)
        if not path.is_absolute():
            path = pathlib.Path(settings.BASE_DIR) / path
        return path.resolve()

    def _descriptor(self) -> RepoDescriptor:
        """Project the configured local checkout into a :class:`RepoDescriptor`."""

        root = self._root
        config = self.integration.config
        return RepoDescriptor(
            name=str(config.get("local_name") or root.name),
            org=str(config.get("local_org") or "local"),
            remote=root.as_uri(),
            default_branch=str(config.get("local_default_branch") or "main"),
            visibility="private",
        )

    def _safe_join(self, path: str) -> pathlib.Path:
        """Join ``path`` under the root, rejecting traversal outside it."""

        root = self._root
        target = (root / path.strip("/")).resolve()
        if target != root and root not in target.parents:
            raise FileNotFoundError(path)
        return target
