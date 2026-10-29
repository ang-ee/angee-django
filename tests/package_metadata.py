"""Native editable metadata for isolated source-package test fixtures."""

from __future__ import annotations

import json
from importlib import metadata
from pathlib import Path
from typing import Any

import tomlkit
from packaging.utils import canonicalize_name


def write_addon_distribution(
    package: Path,
    name: str,
    *,
    depends_on: tuple[str, ...] = (),
    label: str | None = None,
    capabilities: dict[str, Any] | None = None,
    metadata_root: Path | None = None,
) -> metadata.PathDistribution:
    """Write real package data and dist-info discoverable on the fixture source root."""

    entries = {entry.name: entry for entry in metadata.entry_points(group="angee.addons")}
    requirements = [entries[app].dist.metadata["Name"] for app in depends_on if app in entries]
    foreign = tuple(app for app in depends_on if app not in entries)
    distribution = canonicalize_name(name)
    project = {
        "name": distribution, "version": "0.0.0", "dependencies": requirements,
        "entry-points": {"angee.addons": {name: name}},
    }
    (package / "pyproject.toml").write_text(tomlkit.dumps({
        "project": project, "tool": {"angee": capabilities or {}},
    }), encoding="utf-8")
    source_root = metadata_root if metadata_root is not None else package.parents[len(name.split(".")) - 1]
    dist_info = source_root / f"{distribution.replace('-', '_')}-0.0.0.dist-info"
    dist_info.mkdir(exist_ok=True)
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.4\nName: {distribution}\nVersion: 0.0.0\n"
        + "".join(f"Requires-Dist: {requirement}\n" for requirement in requirements), encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(f"[angee.addons]\n{name} = {name}\n", encoding="utf-8")
    (dist_info / "direct_url.json").write_text(json.dumps({
        "url": package.as_uri(), "dir_info": {"editable": True},
    }), encoding="utf-8")
    if foreign or label is not None:
        (package / "apps.py").write_text(
            "from django.apps import AppConfig\n"
            "class FixtureConfig(AppConfig):\n"
            f"    name = {name!r}\n    requires_apps = {foreign!r}\n"
            + (f"    label = {label!r}\n" if label is not None else ""), encoding="utf-8",
        )
    return metadata.PathDistribution(dist_info)
