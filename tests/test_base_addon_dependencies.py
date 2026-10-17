"""Guard the generated dependency group against addon-manifest drift."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from hatch_angee import compile_dependencies, parse_manifest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ADDONS_ROOT = PROJECT_ROOT / "addons"


def _read_toml(path: Path) -> dict[str, Any]:
    """Return the TOML document at ``path`` using the standard-library parser."""

    with path.open("rb") as stream:
        return tomllib.load(stream)


def test_addon_dependency_group_matches_manifests() -> None:
    """The checked-in addons group is the exact compiled manifest dependency union."""

    manifests = tuple(parse_manifest(marker) for marker in sorted(ADDONS_ROOT.glob("**/addon.toml")))
    expected = compile_dependencies(manifests)
    actual = _read_toml(PROJECT_ROOT / "pyproject.toml")["dependency-groups"]["addons"]

    assert tuple(actual) == expected


def test_addon_manifests_leave_core_pins_to_core() -> None:
    """A distribution the core wheel requires is pinned exactly by the core alone.

    A second exact pin in a manifest is copied into every host's generated group,
    and a core bump then leaves the two unresolvable before ``angee build`` can
    rewrite the group.
    """

    core = {
        canonicalize_name(Requirement(spec).name)
        for spec in _read_toml(PROJECT_ROOT / "pyproject.toml")["project"]["dependencies"]
    }
    exact = [
        f"{marker.relative_to(PROJECT_ROOT)}: {spec}"
        for marker in sorted(ADDONS_ROOT.glob("**/addon.toml"))
        for spec in parse_manifest(marker).dependencies
        if canonicalize_name(Requirement(spec).name) in core
        and any(item.operator in ("==", "===") for item in Requirement(spec).specifier)
    ]
    assert exact == []


# Pre-consolidation base-addon side of pre-90bdd58b tests/test_addon_dependencies.py;
# kept separate because it guards addon-manifest compilation.
