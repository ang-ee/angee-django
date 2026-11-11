"""Print the test files a change reaches: the merge gate's changed-path selection.

Reads changed repository paths on stdin, one per line, as ``git diff --name-only``
prints them. A test file is reached when it changed or when it imports a changed
module, directly or through other modules, in grimp's import graph (the one
``lint-imports`` checks). A string in a test file naming a module or package (a
composed-host label or app, a ``pytest_plugins`` entry, a patch target) counts as
importing it and its submodules. A conftest loads for every test beneath it, like
the installed apps, so importing a helper from it adds no edge. A changed input
every test reads without importing it prints the configured ``testpaths`` instead:
a full run. Other files, such as docs and web code, reach no Python test.
"""

from __future__ import annotations

import re
import sys
import tomllib
from fnmatch import fnmatch
from pathlib import Path, PurePath

import grimp

ROOT = Path(__file__).resolve().parents[1]
# Matched against the file name, or against the path when the pattern has a slash.
FULL_RUN = (
    "pyproject.toml", "uv.lock", "conftest.py", "settings.py", "settings.yaml", "autoconfig.py", "*.zed",
    ".github/*", "scripts/*", "src/angee/base/testing/*", "*/testing/fixtures.py",
    "*/resources/*.yaml", "*/resources/*.yml",
)
REFERENCE = re.compile(r"""["']((?:angee|example|tests)(?:\.\w+)+)""")


def module_name(path: str) -> str:
    """Return the dotted module a repository-relative Python path declares."""

    for root in ("src/", "examples/src/"):
        path = path.removeprefix(root)
    return path.removesuffix(".py").replace("/", ".").removesuffix(".__init__")


def reached(changed: list[str]) -> list[str]:
    """Return the test files the changed paths reach, or the ``testpaths`` for a full run."""

    pytest = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]
    if any(fnmatch(path if "/" in glob else PurePath(path).name, glob) for glob in FULL_RUN for path in changed):
        return pytest["testpaths"]
    graph = grimp.build_graph("angee", "example", "tests", cache_dir=None, exclude_type_checking_imports=True)
    for module in [module for module in graph.modules if module.rpartition(".")[2] == "conftest"]:
        graph.remove_module(module)
    tests = {
        module_name(path.relative_to(ROOT).as_posix()): path.relative_to(ROOT).as_posix()
        for root in pytest["testpaths"]
        for pattern in pytest["python_files"]
        for path in sorted((ROOT / root).rglob(pattern))
    }
    for importer, path in tests.items():
        for reference in REFERENCE.findall((ROOT / path).read_text()):
            while reference not in graph.modules and "." in reference:
                reference = reference.rpartition(".")[0]
            if "." in reference:
                for imported in {reference, *graph.find_descendants(reference)}:
                    graph.add_import(importer=importer, imported=imported)
    modules = {module_name(path) for path in changed if path.endswith(".py")} & graph.modules
    reach = modules.union(*(graph.find_downstream_modules(module) for module in modules))
    return sorted(tests[module] for module in reach & tests.keys())


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    for test in reached([line.strip() for line in sys.stdin if line.strip()]):
        print(test)
