"""Project native metadata from explicitly built local or remote wheel artifacts."""

from __future__ import annotations

import io
import re
import tempfile
import zipfile
from configparser import Error as IniError
from importlib import metadata
from pathlib import Path
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django_compose.components import index_components

from angee.integrate_vcs.backend import LocalVCSBackend
from angee.platform.discovery import facts_from_distribution
from angee.platform_integrate_vcs.wheels import MAX_CATALOGUE_BYTES, MAX_PACKAGES, relative_path, wheel_metadata


def source_descriptors(source: Any) -> list[dict[str, Any]]:
    """Read built artifacts before the source owner's CAS/reconcile transaction.

    Authors and CI own building. The selected VCS backend supplies immutable
    inventory and binary blobs; local sources retain their native file policy.
    No checkout, installation, package import or build backend is invoked.
    """

    repository = source.repository
    backend = repository.vcs_bridge.backend
    scope = relative_path(source.path or ".", root=True)
    local = isinstance(backend, LocalVCSBackend)
    revision = ""
    if not local:
        credential = repository.vcs_bridge.credential
        if credential is not None:
            credential.ensure_fresh()
        revision = backend.rev_parse(repository, source.ref or repository.default_branch)
        if not isinstance(revision, str) or re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", revision) is None:
            raise ImproperlyConfigured("Catalogue artifacts require an immutable source commit")
    entries = backend.ls_tree(repository, ref=revision, path=source.path, recursive=True)
    wheels = [entry for entry in entries if entry.type == "blob" and entry.name.endswith(".whl")]
    if not wheels:
        raise ImproperlyConfigured(
            "Catalogue sources index already built wheels only. Build/publish artifacts outside Django, "
            "then set Source.path to their directory; no wheel was found."
        )
    if len(wheels) > MAX_PACKAGES:
        raise ImproperlyConfigured("Wheel inventory exceeds its package limit")
    try:
        with tempfile.TemporaryDirectory(prefix="angee-catalogue-data-") as temporary:
            prepared = []
            paths = set()
            total_bytes = 0
            for index, entry in enumerate(sorted(wheels, key=lambda entry: entry.path)):
                relative = relative_path(entry.path)
                if not relative.is_relative_to(scope) or entry.path in paths:
                    raise ValueError("Artifact inventory escapes its source scope or repeats a path")
                paths.add(entry.path)
                content = backend.cat_file(repository, ref=revision, path=entry.path)
                files = wheel_metadata(io.BytesIO(content))
                total_bytes += sum(len(content.encode("utf-8")) for content in files.values())
                if total_bytes > MAX_CATALOGUE_BYTES:
                    raise ValueError("Catalogue metadata exceeds its byte limit")
                base = Path(temporary) / str(index)
                directory = next(name.split("/", 1)[0] for name in files if name.endswith(".dist-info/METADATA"))
                for name, content in files.items():
                    target = base / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                prepared.append((entry.path, metadata.PathDistribution(base / directory)))
            component_index = index_components((distribution for _path, distribution in prepared), group="angee.addons")
            descriptors = []
            for path, distribution in prepared:
                facts = facts_from_distribution(distribution, index=component_index)
                if facts:
                    descriptors.append({**facts, "keywords": list(facts["keywords"]),
                                        "depends_on": list(facts["depends_on"]), "path": path})
            return descriptors
    except (KeyError, TypeError, ValueError, OSError, IniError, zipfile.BadZipFile) as error:
        raise ImproperlyConfigured(f"Invalid wheel metadata; previous catalogue retained: {error}") from error
