"""Select native pytest paths and isolated addon hosts from an exact checkout diff."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from contextlib import redirect_stdout
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import pytest

ROOT = Path(__file__).resolve().parents[1]
GROUP = "angee.addons"
GRAPH_INPUTS = {
    "apps.py", "pyproject.toml", "uv.lock", "pytest.ini", "setup.cfg", "tox.ini",
    ".python-version", "settings.py", "settings.yaml", "autoconfig.py",
    ".test_durations", ".test_durations_postgres",
}
COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
TEMPLATE_READER = re.compile(r"""/\s*["']templates[/"']""")
APP_LOOKUP = re.compile(r"""\b(?:get_model|get_app_config)\(\s*["'](\w+)(?:\.\w+)?["']""")
MODEL_LABEL = re.compile(r"""["'](\w+)\.[A-Z]\w*["']""")


@dataclass(frozen=True)
class Selection:
    mode: Literal["full", "selected", "empty"]
    reason: str
    paths: tuple[str, ...] = ()

    @classmethod
    def reached(cls, paths: Sequence[str]) -> Selection:
        return cls("selected", "changed inputs reach these tests", tuple(sorted(set(paths)))) if paths else cls(
            "empty", "no collected tests reached",
        )


@dataclass(frozen=True)
class AppGraph:
    owners: dict[Path, str]
    dependencies: dict[str, list[str]]
    labels: dict[str, str]


def changed_paths(root: Path, base: str | None, head: str | None) -> tuple[list[Path], str]:
    """Compare the caller's precise base and checked head, treating removals as unknown ownership."""
    if not base or not head:
        return [], "no pull-request comparison"
    if not COMMIT.fullmatch(base) or not COMMIT.fullmatch(head):
        return [], "expected exact base and head commit IDs"

    def git(*args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)

    checked = git("rev-parse", "HEAD")
    if checked.returncode or checked.stdout.strip().decode() != head:
        return [], "requested head differs from the checkout"
    diff = git("diff", "--name-status", "-z", "--no-renames", base, head, "--")
    if diff.returncode:
        return [], "base or diff unavailable"
    fields = diff.stdout.rstrip(b"\0").split(b"\0") if diff.stdout else []
    paths = []
    for status, name in zip(fields[::2], fields[1::2], strict=True):
        path = Path(os.fsdecode(name))
        if status not in {b"A", b"M"} or not (root / path).is_file():
            return [], f"removed or replaced input: {path}"
        paths.append(path)
    return sorted(paths), ""


def python_input(path: Path) -> bool:
    """Ignore prose and ordinary frontend sources; codegen documents are Python test inputs."""
    return path.suffix != ".md" and (
        path.suffix not in {".ts", ".tsx", ".js", ".jsx", ".css"}
        or path.stem.split(".")[0] == "documents"
    )


def shared_input(path: Path) -> bool:
    return path.parts[0] != "templates" and (
        path.name in GRAPH_INPUTS or path.parts[0] in {".github", "scripts"}
    )


def addon_graph(root: Path, apps: Sequence[str]) -> AppGraph:
    from django_compose.components import inspect_apps, installed_components

    roots = apps or sorted(installed_components(GROUP).providers)
    result = inspect_apps(roots, group=GROUP)
    owners = {}
    for entry in result["app_entries"]:
        path = Path(entry["path"]).resolve()
        if path.is_relative_to(root):
            relative = path.relative_to(root)
            # Installed site-packages can sit inside the checkout's ignored virtualenv.
            if not any(part.startswith(".") for part in relative.parts):
                owners[relative] = entry["name"]
    labels = {entry["label"]: entry["name"] for entry in result["app_entries"]}
    return AppGraph(owners, result["dependencies"], labels)


def addon_at(path: Path, owners: dict[Path, str]) -> tuple[Path, str] | None:
    return next(((parent, owners[parent]) for parent in path.parents if parent in owners), None)


def module_name(path: Path, owners: dict[Path, str]) -> str:
    if (owner := addon_at(path, owners)) is not None:
        directory, addon = owner
        suffix = path.relative_to(directory).with_suffix("")
        return ".".join((addon, *suffix.parts)).removesuffix(".__init__")
    return ".".join(path.with_suffix("").parts).removesuffix(".__init__")


def reverse_addons(changed: set[str], dependencies: dict[str, list[str]]) -> set[str]:
    affected = set(changed)
    while (consumers := {app for app, needs in dependencies.items() if affected.intersection(needs)}) - affected:
        affected.update(consumers)
    return affected


def isolated_suites(root: Path, owners: dict[Path, str]) -> dict[Path, str]:
    """An addon-local pytest.ini declares and configures a standalone composed suite."""
    return {directory: addon for directory, addon in owners.items() if (root / directory / "pytest.ini").is_file()}


def import_reach(
    root: Path, apps: AppGraph, changed: Sequence[Path], tests: Sequence[Path], affected: set[str],
) -> tuple[set[Path], set[str]] | None:
    """Use grimp for helper imports and the existing string/conftest edges for legacy suites."""
    try:
        import grimp
    except ImportError:
        return None

    owners = apps.owners
    packages = {addon.split(".")[0] for addon in owners.values()}
    if (root / "tests" / "__init__.py").is_file():
        packages.add("tests")
    if not packages:
        return None
    migration_templates = {
        module_name(directory / "compose_migrations", owners)
        for directory in owners if (root / directory / "compose_migrations").is_dir()
    }
    try:
        graph = grimp.build_graph(*sorted(packages | migration_templates), cache_dir=None)
    except (ImportError, ValueError, grimp.exceptions.GrimpException):
        return None
    modules = graph.modules
    changed_modules = {
        module_name(path, owners) for path in changed if path.suffix == ".py" and path.parts[0] != "templates"
    }
    if changed_modules - modules - {module_name(path, owners) for path in tests}:
        return None

    reference = re.compile(r"""["']((?:""" + "|".join(map(re.escape, sorted(packages))) + r""")(?:\.\w+)+)""")
    selected = {path for path in changed if path in tests}
    seeds = changed_modules.intersection(modules) | affected
    reach = set(seeds)
    for module in seeds:
        if module in modules:
            as_package = module in affected or any(
                path.name == "__init__.py" and module_name(path, owners) == module for path in changed
            )
            reach.update(graph.find_downstream_modules(module, as_package=as_package))
            if as_package:
                reach.update(graph.find_descendants(module))
    if (root_conftest := root / "conftest.py").is_file():
        source = root_conftest.read_text()
        loaded = set(reference.findall(source))
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and node.module:
                loaded.update((node.module, *(f"{node.module}.{alias.name}" for alias in node.names)))
            elif isinstance(node, ast.Import):
                loaded.update(alias.name for alias in node.names)
        if reach.intersection(loaded):
            selected.update(tests)
            return selected, reach
    for path in tests:
        module = module_name(path, owners)
        if module in reach or any(
            module_name(directory / "conftest.py", owners) in reach for directory in path.parents
        ):
            selected.add(path)
            continue
        source = (root / path).read_text()
        labels = APP_LOOKUP.findall(source) + MODEL_LABEL.findall(source)
        if any(apps.labels.get(label) in affected for label in labels):
            selected.add(path)
            continue
        if any(change.parts[0] == "templates" for change in changed) and TEMPLATE_READER.search(source):
            selected.add(path)
            continue
        for name in reference.findall(source):
            while "." in name and name not in modules:
                name = name.rpartition(".")[0]
            if "." in name and any(
                item == name or (
                    item.startswith(f"{name}.")
                    # A production app reference alone is not a reference to its standalone tests.
                    and (".tests." not in item or name.endswith(".tests") or ".tests." in name)
                )
                for item in reach
            ):
                selected.add(path)
                break
    return selected, reach


def select(
    root: Path, changed: Sequence[Path], tests: Sequence[Path], apps: AppGraph, *, fallback: str = "",
    isolated: dict[Path, str] | None = None, legacy: bool = False, shared_tests: Sequence[Path] = (),
) -> tuple[Selection, Selection]:
    """Select shared-host pytest paths and standalone addon names using the same changed inputs."""
    isolated = isolated or {}
    changes = [path for path in changed if python_input(path)]
    full_hosts = tuple(sorted(isolated.values()))
    if not fallback:
        fallback = next((f"graph or runner input: {path}" for path in changes if shared_input(path)), "")
    if fallback:
        return Selection("full", fallback), Selection("full", fallback, full_hosts)
    if not changes:
        # Consumer contract suites can read frontend declarations directly.
        paths = [path.as_posix() for path in shared_tests] if not legacy and any(
            path.suffix != ".md" for path in changed
        ) else []
        return Selection.reached(paths), Selection.reached(())

    owner_tests: set[str] = set()
    source_apps: set[str] = set()
    broad_shared = ""
    broad_hosts = ""
    for path in changes:
        if (owner := addon_at(path, apps.owners)) is None:
            if path.parts[0] == "templates" and legacy:
                continue
            if path.parts[0] == "tests" and path.suffix == ".py" and path.name != "conftest.py":
                continue
            broad_shared = broad_hosts = f"shared or unowned input: {path}"
            continue
        directory, addon = owner
        relative = path.relative_to(directory)
        if directory in isolated and relative.parts[0] == "tests":
            owner_tests.add(addon)
            if path.suffix != ".py":
                broad_shared = f"test data input: {path}"
            continue
        if path not in tests:
            source_apps.add(addon)
        if path.suffix != ".py" or path.name == "conftest.py" or relative.parts[0] == "testing":
            broad_shared = f"shared-host input: {path}"
        # Test plugins are often loaded by argv or conftest, independent of native addon edges.
        if relative.parts[0] in {"testing", "testing.py"}:
            broad_hosts = f"test support input: {path}"

    affected = reverse_addons(source_apps, apps.dependencies)
    imports = import_reach(root, apps, changes, tests, affected) if not broad_shared or full_hosts else None
    reached, importers = imports or (set(), set())
    if imports is None and not broad_shared:
        broad_shared = "Python import graph could not account for a changed input"
    if legacy and source_apps.intersection({"angee.base", "angee.server", "angee.graphql"}):
        broad_shared = "shared framework owner changed"
    if source_apps or not legacy:
        reached.update(shared_tests)
    selected = {path.as_posix() for path in reached}
    if not broad_shared and tests and set(tests) <= reached:
        broad_shared = "changed inputs reach the whole shared host"
    shared = Selection("full", broad_shared) if broad_shared else Selection.reached(sorted(selected))

    # Test-helper imports can cross suite boundaries independently of native app requirements.
    owner_tests.update(addon for addon in full_hosts if any(name.startswith(f"{addon}.tests.") for name in importers))
    if imports is None and len(full_hosts) > 1:
        broad_hosts = "isolated test-support imports unavailable"
    if any(path.parts[0] == "tests" and path not in tests for path in changes):
        broad_hosts = "legacy test support or profile changed"
    hosts = Selection("full", broad_hosts, full_hosts) if broad_hosts else Selection.reached(
        sorted(set(full_hosts) & (affected | owner_tests)),
    )
    return shared, hosts


class Collection:
    """Ask pytest for its unsplit collection and the node-ID prefix of each file."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.shared_paths: set[str] = set()

    @pytest.hookimpl(tryfirst=True)
    def pytest_configure(self, config: pytest.Config) -> None:
        # Keep the native arguments and plugins; disable distribution in this probe.
        config.option.numprocesses = 0
        config.option.splits = None

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.files = {str(item.path): item.nodeid.partition("::")[0] for item in session.items}
        self.shared_paths = {str(item.path) for item in session.items if item.get_closest_marker("shared_inputs")}


def collected(root: Path, args: Sequence[str]) -> tuple[dict[Path, str], list[Path]]:
    command = [sys.executable, str(Path(__file__).resolve()), "_collect", *args]
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    if result.returncode:
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)
    data = json.loads(result.stdout)
    return (
        {Path(path).relative_to(root): node for path, node in data["files"].items()},
        [Path(path).relative_to(root) for path in data["shared_paths"]],
    )


def run_selected(
    pytest_args: Sequence[str], *, root: Path, base: str | None, head: str | None, apps: Sequence[str] = (),
) -> int:
    """Run the caller's native pytest invocation with a visible changed-path selection."""
    callers = Path.cwd()
    root = root.resolve()
    changes, fallback = changed_paths(root, base, head)
    graph, fallback = installed_graph(root, apps, fallback)
    tests, shared_tests = collected(root, pytest_args)
    selection, _ = select(
        root, changes, tuple(tests), graph, fallback=fallback, shared_tests=shared_tests,
    )
    if selection.mode == "full":
        selection = Selection("full", selection.reason, tuple(sorted(path.as_posix() for path in tests)))
    print(json.dumps(asdict(selection)), flush=True)
    if selection.mode == "empty":
        return 0
    included = set(selection.paths)
    options = [
        arg for path, node in tests.items() if path.as_posix() not in included
        for arg in ("--deselect", f"{node}::")
    ]
    os.chdir(root)
    try:
        status = pytest.main([*pytest_args, *options])
    finally:
        os.chdir(callers)
    # A successfully collected nonempty selection may have no items in a particular shard.
    return 0 if status == pytest.ExitCode.NO_TESTS_COLLECTED and "--splits" in pytest_args else status


def installed_graph(root: Path, apps: Sequence[str], fallback: str) -> tuple[AppGraph, str]:
    from django.core.exceptions import ImproperlyConfigured

    try:
        return addon_graph(root, apps), fallback
    except (ImportError, ImproperlyConfigured) as error:
        return AppGraph({}, {}, {}), fallback or f"native app graph unavailable: {error}"


def framework_plan(base: str | None, head: str | None) -> dict[str, Selection]:
    changes, fallback = changed_paths(ROOT, base, head)
    graph, fallback = installed_graph(ROOT, (), fallback)
    # Native configuration names the existing shared host's broad fallback paths.
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]
    roots = tuple(config["testpaths"])
    host_suites = isolated_suites(ROOT, graph.owners)
    if not graph.owners:
        # If native resolution fails, preserve declared suites and let the host's
        # own composition report the failure. This scans config, not test files.
        host_suites = {
            directory.parent.relative_to(ROOT): f"angee.{directory.parent.name}"
            for directory in (ROOT / "src/angee").glob("*/pytest.ini")
        }
    needs_collection = not fallback and any(map(python_input, changes)) and not any(map(shared_input, changes))
    tests, shared_tests = collected(ROOT, ()) if needs_collection else ({}, [])
    shared, hosts = select(
        ROOT, changes, tuple(tests), graph, fallback=fallback,
        isolated=host_suites, legacy=True, shared_tests=shared_tests,
    )
    if shared.mode == "full":
        shared = Selection("full", shared.reason, roots)
    return {"shared": shared, "hosts": hosts}


def main(argv: Sequence[str]) -> int:
    if argv[:1] == ["_collect"]:
        probe = Collection()
        with redirect_stdout(sys.stderr):
            status = pytest.main(["--collect-only", *argv[1:]], plugins=[probe])
        if status == pytest.ExitCode.OK:
            print(json.dumps({"files": probe.files, "shared_paths": sorted(probe.shared_paths)}))
        return status
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("command", choices=("framework", "pytest"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--app", action="append", default=[])
    options, pytest_args = parser.parse_known_args(argv)
    if options.command == "framework":
        if pytest_args:
            parser.error("framework accepts no pytest arguments")
        plan = framework_plan(options.base, options.head)
        print(json.dumps({name: asdict(selection) for name, selection in plan.items()}))
        return 0
    return run_selected(
        pytest_args[1:] if pytest_args[:1] == ["--"] else pytest_args,
        root=options.root, base=options.base, head=options.head, apps=options.app,
    )


if __name__ == "__main__":
    sys.path.insert(0, str(Path.cwd()))
    raise SystemExit(main(sys.argv[1:]))
