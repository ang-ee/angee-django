"""Hatchling defaults for one in-place Angee addon distribution.

Project metadata remains authored PEP 621 data. The backend supplies only the
source remapping that lets an addon package be its own distribution root.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any, cast

from hatchling.builders.sdist import SdistBuilder
from hatchling.builders.wheel import WheelBuilder


def _builder(target: str) -> Any:
    root = Path.cwd()
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project = config["project"]
    entries = project.get("entry-points", {}).get("angee.addons", {})
    if len(entries) != 1:
        raise ValueError("An addon distribution must declare exactly one angee.addons entry point")
    app = next(iter(entries))
    parts = app.split(".")
    if not all(part.isidentifier() for part in parts):
        raise ValueError(f"Invalid addon package {app!r}")
    expected = app.replace(".", "-").replace("_", "-")
    if re.sub(r"[-_.]+", "-", project["name"]).lower() != re.sub(r"[-_.]+", "-", expected).lower():
        raise ValueError(f"Addon {app!r} must use distribution name {expected!r}")
    # sdists are relocated archives, identified by Hatchling's native metadata.
    if not (root / "PKG-INFO").is_file() and tuple(root.parts[-len(parts):]) != tuple(parts):
        raise ValueError(f"Addon package directory must end in {'/'.join(parts)!r}")
    build = config.setdefault("tool", {}).setdefault("hatch", {}).setdefault("build", {})
    build.setdefault("include", ["*"])
    build.setdefault("exclude", ["PKG-INFO", ".gitignore"])
    wheel = build.setdefault("targets", {}).setdefault("wheel", {})
    if "sources" not in build:
        wheel.setdefault("sources", {"": "/".join(parts)})
    if "dev-mode-dirs" not in build:
        wheel.setdefault("dev-mode-dirs", ["/".join([".."] * len(parts))])
    cls = WheelBuilder if target == "wheel" else SdistBuilder
    return cls(str(root), config=config)


def _build(target: str, directory: str, version: str) -> str:
    return Path(next(_builder(target).build(directory=directory, versions=[version]))).name


def build_wheel(wheel_directory: str, config_settings: Any = None, metadata_directory: str | None = None) -> str:
    return _build("wheel", wheel_directory, "standard")


def build_editable(wheel_directory: str, config_settings: Any = None, metadata_directory: str | None = None) -> str:
    return _build("wheel", wheel_directory, "editable")


def build_sdist(sdist_directory: str, config_settings: Any = None) -> str:
    return _build("sdist", sdist_directory, "standard")


def get_requires_for_build_wheel(config_settings: Any = None) -> list[str]:
    return cast(list[str], _builder("wheel").config.dependencies)


def get_requires_for_build_editable(config_settings: Any = None) -> list[str]:
    from hatchling.builders.constants import EDITABLES_REQUIREMENT

    return [*_builder("wheel").config.dependencies, EDITABLES_REQUIREMENT]


def get_requires_for_build_sdist(config_settings: Any = None) -> list[str]:
    return cast(list[str], _builder("sdist").config.dependencies)


def _prepare_metadata(metadata_directory: str, *, editable: bool) -> str:
    builder = _builder("wheel")
    directory = Path(metadata_directory) / f"{builder.artifact_project_id}.dist-info"
    directory.mkdir(exist_ok=True)
    from hatchling.builders.constants import EDITABLES_REQUIREMENT

    dependencies = []
    if editable and not builder.config.dev_mode_dirs and builder.config.dev_mode_exact:
        dependencies.append(EDITABLES_REQUIREMENT)
    metadata = builder.config.core_metadata_constructor(builder.metadata, extra_dependencies=dependencies)
    (directory / "METADATA").write_text(metadata, encoding="utf-8")
    return directory.name


def prepare_metadata_for_build_wheel(metadata_directory: str, config_settings: Any = None) -> str:
    return _prepare_metadata(metadata_directory, editable=False)


def prepare_metadata_for_build_editable(metadata_directory: str, config_settings: Any = None) -> str:
    return _prepare_metadata(metadata_directory, editable=True)
