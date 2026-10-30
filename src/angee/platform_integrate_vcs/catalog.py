"""Prepare native package metadata from a scoped VCS source view."""

from __future__ import annotations

import tempfile
import tomllib
import zipfile
from contextlib import ExitStack
from importlib import metadata
from pathlib import Path
from typing import Any

from build import ProjectBuilder
from build.env import DefaultIsolatedEnv
from django_compose.components import index_components

from angee.operator.daemon import OperatorDaemon
from angee.platform.discovery import facts_from_distribution


def build_distribution(project: Path, output: Path, archives: ExitStack) -> metadata.PathDistribution:
    """Build a native wheel and retain its distribution metadata within ``archives``.

    The mandatory wheel hook supplies complete entry-point metadata even when a
    backend's optional preparation hook returns only core METADATA. Native build
    isolation owns backend loading and build requirements. No addon is imported
    into the serving process.
    """

    output.mkdir()
    with DefaultIsolatedEnv(installer="uv") as environment:
        builder = ProjectBuilder.from_isolated_env(environment, str(project))
        environment.install(builder.build_system_requires)
        environment.install(builder.get_requires_for_build("wheel"))
        wheel = builder.build("wheel", str(output))
    archive = archives.enter_context(zipfile.ZipFile(wheel))
    directories = [
        path for path in zipfile.Path(archive).iterdir() if path.is_dir() and path.name.endswith(".dist-info")
    ]
    if len(directories) != 1:
        raise ValueError("A built wheel must contain one native dist-info directory")
    return metadata.PathDistribution(directories[0])


def source_descriptors(source: Any) -> list[dict[str, Any]]:
    """Read one complete source view into plain catalogue facts before database writes.

    Remote Git credentials and complete checkout cleanup belong to the operator.
    Local backends retain working-tree semantics. The metadata frontend installs
    build requirements only in its temporary environment; neither the addon nor
    its AppConfig is imported into the serving process. No persistent source or
    metadata cache is maintained here.
    """

    daemon = OperatorDaemon.from_settings()
    with source.materialized(checkout=daemon.source_checkout) as (root, _revision):
        with tempfile.TemporaryDirectory(prefix="angee-catalogue-metadata-") as temporary, ExitStack() as archives:
            prepared = []
            for marker in source.marker_paths(root, marker="pyproject.toml"):
                document = tomllib.loads(marker.read_text(encoding="utf-8"))
                distribution = build_distribution(marker.parent, Path(temporary) / str(len(prepared)), archives)
                prepared.append((marker.parent, document, distribution))
            index = index_components(
                (distribution for _path, _document, distribution in prepared), group="angee.addons",
            )
            descriptors = []
            for path, document, distribution in prepared:
                facts = facts_from_distribution(distribution, index=index, document=document)
                if not facts:
                    continue
                relative = path.relative_to(root)
                descriptors.append({
                    **facts,
                    "keywords": list(facts["keywords"]),
                    "depends_on": list(facts["depends_on"]),
                    "path": "" if relative == Path(".") else relative.as_posix(),
                })
            return descriptors
