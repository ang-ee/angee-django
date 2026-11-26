"""Print the test files a change reaches: the merge gate's changed-path selection.

Reads changed repository paths on stdin, one per line, as ``git diff --name-only
--no-renames`` prints them: a moved file as its old and its new path. A test file is
reached when it changed or when it imports a changed module, directly or through
other modules, in grimp's import graph (the one ``lint-imports`` checks). A string
in a test file naming a module or package (a composed-host label or app, a
``pytest_plugins`` entry, a patch target) counts as importing it and its submodules.
A conftest loads for every test beneath it, so each of those tests imports it, and
the repository's own conftest imports the shared test graph and names the plugins
every test loads. Importing any module of a package runs its ``__init__``, so a
changed one reaches every importer of the package. A changed template reaches the
test files that join a path onto the repository's ``templates`` directory, which
render or read it. A full run prints the configured ``testpaths`` instead: for a
changed input every test reads without importing it, a Python change to a shared
owner every addon composes onto, a change every test reaches, and a changed Python
file outside the import graph, such as a removed or moved module, whose importers
the graph cannot name. Other files, such as docs and web code, reach no Python test.
"""

from __future__ import annotations

import ast
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
# The directories the graph's packages sit in.
PACKAGE_ROOTS = ("src", "examples/src")
# The owners every addon composes onto, whose changes reach tests no import names.
SHARED_OWNERS = ("angee.base", "angee.server", "angee.graphql")
REFERENCE = re.compile(r"""["']((?:angee|example|tests)(?:\.\w+)+)""")
TEMPLATE_READER = re.compile(r"""/\s*["']templates[/"']""")


def module_name(path: str) -> str:
    """Return the dotted module a repository-relative Python path declares."""

    for root in PACKAGE_ROOTS:
        path = path.removeprefix(f"{root}/")
    return path.removesuffix(".py").replace("/", ".").removesuffix(".__init__")


def reached(changed: list[str]) -> list[str]:
    """Return the test files the changed paths reach, or the ``testpaths`` for a full run."""

    pytest = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]
    sources = [path for path in changed if not path.startswith("templates/")]
    if any(fnmatch(path if "/" in glob else PurePath(path).name, glob) for glob in FULL_RUN for path in sources):
        return pytest["testpaths"]
    # The compose migration templates sit in directories without ``__init__.py``,
    # which grimp scans only when they are named.
    templates = [path for root in PACKAGE_ROOTS for path in (ROOT / root).glob("*/*/compose_migrations")]
    graph = grimp.build_graph(
        "angee", "example", "tests", *(module_name(path.relative_to(ROOT).as_posix()) for path in sorted(templates)),
        cache_dir=None, exclude_type_checking_imports=True,
    )
    modules = {module_name(path) for path in sources if path.endswith(".py")}
    packages = {module_name(path) for path in sources if PurePath(path).name == "__init__.py"}
    shared = any(f"{module}.".startswith(f"{owner}.") for owner in SHARED_OWNERS for module in modules)
    if shared or not modules <= graph.modules:
        return pytest["testpaths"]
    # The repository's conftest sits outside the packages grimp scans.
    root_conftest = (ROOT / "conftest.py").read_text()
    loaded = set(REFERENCE.findall(root_conftest))
    for node in ast.walk(ast.parse(root_conftest)):
        if isinstance(node, ast.ImportFrom):
            loaded |= {node.module, *(f"{node.module}.{alias.name}" for alias in node.names)}
        elif isinstance(node, ast.Import):
            loaded |= {alias.name for alias in node.names}
    graph.add_module("conftest")
    for imported in loaded & graph.modules:
        graph.add_import(importer="conftest", imported=imported)
    tests = {
        module_name(path.relative_to(ROOT).as_posix()): path.relative_to(ROOT).as_posix()
        for root in pytest["testpaths"]
        for pattern in pytest["python_files"]
        for path in sorted((ROOT / root).rglob(pattern))
    }
    reach = set()
    for importer, path in tests.items():
        for directory in PurePath(path).parents:
            if (conftest := module_name((directory / "conftest.py").as_posix())) in graph.modules:
                graph.add_import(importer=importer, imported=conftest)
        text = (ROOT / path).read_text()
        if len(sources) < len(changed) and TEMPLATE_READER.search(text):
            reach.add(importer)
        for reference in REFERENCE.findall(text):
            while reference not in graph.modules and "." in reference:
                reference = reference.rpartition(".")[0]
            if "." in reference:
                for imported in {reference, *graph.find_descendants(reference)}:
                    graph.add_import(importer=importer, imported=imported)
    for module in modules:
        package = graph.find_descendants(module) if module in packages else set()
        reach |= {module, *package, *graph.find_downstream_modules(module, as_package=module in packages)}
    if tests.keys() <= reach:
        return pytest["testpaths"]
    return sorted(tests[module] for module in reach & tests.keys())


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    for test in reached([line.strip() for line in sys.stdin if line.strip()]):
        print(test)
