"""Guard the generated dependency group against addon-manifest drift."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from hatch_angee import compile_dependencies, parse_manifest

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
# P8 split provenance: angee-base side of pre-90bdd58b tests/test_addon_dependencies.py; kept separate because it guards addon-manifest compilation.
