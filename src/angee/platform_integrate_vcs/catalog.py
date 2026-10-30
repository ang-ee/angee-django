"""Interpret native metadata returned by the operator's offline source build."""

from __future__ import annotations

import json
import tempfile
import time
from configparser import Error as IniError
from importlib import metadata
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django_compose.components import index_components

from angee.integrate_vcs.backend import LocalVCSBackend, source_path
from angee.operator.daemon import OperatorDaemon, OperatorDaemonError
from angee.platform.discovery import facts_from_distribution
from angee.platform_integrate_vcs.metadata_worker import (
    MAX_FILES,
    MAX_METADATA_BYTES,
    MAX_OUTPUT_BYTES,
    MAX_PACKAGES,
    relative_path,
    validate_record,
    wheel_metadata,
)


def metadata_output(daemon: OperatorDaemon, operation: dict[str, Any]) -> str:
    """Await the existing job receipt, refusing failed, malformed or bounded-out runs."""

    deadline = time.monotonic() + 910
    while operation.get("status") in {"pending", "running"}:
        identifier = operation.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise OperatorDaemonError("Catalogue metadata job returned no operation identifier")
        if time.monotonic() >= deadline:
            raise OperatorDaemonError(f"Catalogue metadata job {identifier} did not finish within its deadline")
        time.sleep(0.25)
        operation = daemon.job_run(identifier)
    if operation.get("status") != "succeeded":
        raise OperatorDaemonError(
            "Catalogue metadata preparation failed: " + str(operation.get("error") or "invalid job receipt")
        )
    output = operation.get("output")
    if not isinstance(output, str) or len(output.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise OperatorDaemonError("Catalogue metadata job returned missing or oversized output")
    return output


def source_descriptors(source: Any) -> list[dict[str, Any]]:
    """Prepare all metadata before the source owner's CAS/reconcile transaction.

    The selected VCS backend owns revision resolution and refreshed Git material.
    Only the operator's declared offline build job executes package backends.
    Django receives data files, never a checkout, wheel or executable module.
    """

    repository = source.repository
    backend = repository.vcs_bridge.backend
    scope = relative_path(source.path or ".", root=True)
    if isinstance(backend, LocalVCSBackend):
        def no_checkout(*args: Any) -> Any:
            raise ImproperlyConfigured("Local wheel indexing never checks out or builds source")
        with source.materialized(checkout=no_checkout) as (root, _revision):
            entries = backend.ls_tree(repository, ref=source.ref, path=source.path, recursive=True)
            wheels = [entry for entry in entries if entry.type == "blob" and entry.name.endswith(".whl")]
            if not wheels:
                raise ImproperlyConfigured(
                    "Local catalogue sources index explicitly built wheels only. Build wheels outside Django, "
                    "then set Source.path to their directory; no wheel was found."
                )
            if len(wheels) > MAX_PACKAGES:
                raise ImproperlyConfigured("Local wheel inventory exceeds its package limit")
            result = {"schema": 1, "packages": [
                {"path": entry.path, "files": wheel_metadata(source_path(root, entry.path))} for entry in wheels
            ]}
        output = json.dumps(result)
        if len(output.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise ImproperlyConfigured("Local wheel metadata exceeds its output byte limit")
    else:
        credential = backend.git_credentials()
        revision = backend.rev_parse(repository, source.ref or repository.default_branch)
        daemon = OperatorDaemon.from_settings()
        operation = daemon.run_source_build(
            str(getattr(settings, "ANGEE_CATALOGUE_METADATA_JOB", "catalogue-metadata")),
            repository_url=str(repository.remote), revision=revision, credential=credential,
            artifact=Path(__file__).with_name("metadata_worker.py").read_text(encoding="utf-8"),
            package_path=scope.as_posix(),
        )
        output = metadata_output(daemon, operation)
    return descriptors_from_metadata(output, scope=scope.as_posix())


def descriptors_from_metadata(output: str, *, scope: str = ".") -> list[dict[str, Any]]:
    """Project bounded wheel data through native distribution and capability owners."""

    try:
        if len(output.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise ValueError("Catalogue metadata exceeds its output byte limit")
        result = json.loads(output)
        if not isinstance(result, dict) or set(result) != {"schema", "packages"} or result["schema"] != 1:
            raise ValueError("Unsupported catalogue metadata envelope")
        packages = result["packages"]
        if not isinstance(packages, list) or len(packages) > MAX_PACKAGES:
            raise ValueError("Catalogue package inventory is invalid or oversized")
        with tempfile.TemporaryDirectory(prefix="angee-catalogue-data-") as temporary:
            prepared = []
            paths = set()
            for index, package in enumerate(packages):
                if not isinstance(package, dict) or set(package) != {"path", "files"}:
                    raise ValueError("Invalid catalogue package metadata")
                relative = relative_path(package["path"], root=True)
                if not relative.is_relative_to(relative_path(scope, root=True)):
                    raise ValueError("Catalogue package path escapes the selected source scope")
                path = relative.as_posix()
                if path in paths:
                    raise ValueError("Duplicate source package path")
                paths.add(path)
                files = package["files"]
                if not isinstance(files, dict) or len(files) > MAX_FILES:
                    raise ValueError("Invalid or oversized package data inventory")
                base = Path(temporary) / str(index)
                directories = set()
                for name, content in files.items():
                    relative = relative_path(name)
                    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_METADATA_BYTES:
                        raise ValueError("Invalid or oversized package metadata file")
                    info = len(relative.parts) == 2 and relative.parts[0].endswith(".dist-info")
                    if info and relative.name in {"METADATA", "entry_points.txt", "RECORD"}:
                        directories.add(relative.parts[0])
                    elif relative.name != "pyproject.toml":
                        raise ValueError("Catalogue job returned an executable or unsupported data file")
                    target = base / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                if len(directories) != 1:
                    raise ValueError("Expected one native dist-info directory")
                distribution = metadata.PathDistribution(base / directories.pop())
                if distribution.read_text("METADATA") is None or distribution.read_text("RECORD") is None:
                    raise ValueError("Incomplete native wheel metadata")
                validate_record(distribution.read_text("RECORD") or "")
                prepared.append((path, distribution))
            component_index = index_components((distribution for _path, distribution in prepared), group="angee.addons")
            descriptors = []
            for path, distribution in prepared:
                facts = facts_from_distribution(distribution, index=component_index)
                if facts:
                    descriptors.append({**facts, "keywords": list(facts["keywords"]),
                                        "depends_on": list(facts["depends_on"]), "path": "" if path == "." else path})
            return descriptors
    except (KeyError, TypeError, ValueError, OSError, IniError) as error:
        raise ImproperlyConfigured(f"Catalogue metadata was invalid; previous catalogue retained: {error}") from error
