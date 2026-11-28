"""Select native pytest paths and isolated addon hosts from an exact checkout diff."""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tomllib
from collections.abc import Sequence
from contextlib import redirect_stdout
from dataclasses import asdict, dataclass
from importlib import metadata
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from urllib.parse import urlsplit
from urllib.request import url2pathname

import pytest
from packaging.markers import Marker

GROUP = "angee.addons"
GRAPH_INPUTS = {
    "pyproject.toml", "uv.lock", "pytest.ini", "setup.cfg", "tox.ini",
    ".python-version", "settings.py", "settings.yaml",
    ".test_durations", ".test_durations_postgres",
}
APP_INPUTS = {"apps.py", "autoconfig.py", "permissions.zed", "permissions.extends.zed"}
WORKSPACE_INPUTS = {Path("pyproject.toml"), Path("uv.lock")}
JS_INPUTS = {Path("package.json"), Path("pnpm-workspace.yaml"), Path("pnpm-lock.yaml")}
STACK_LOCK = Path(".github/ci/pnpm-lock.yaml")
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


def git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)


def absent_at(root: Path, base: str, path: Path) -> bool:
    entry = git(root, "ls-tree", base, "--", path.as_posix())
    return not entry.returncode and not entry.stdout


def changed_paths(root: Path, base: str | None, head: str | None) -> tuple[list[Path], str]:
    """Compare the caller's precise base and checked head, treating removals as unknown ownership."""
    if not base or not head:
        return [], "no pull-request comparison"
    if not COMMIT.fullmatch(base) or not COMMIT.fullmatch(head):
        return [], "expected exact base and head commit IDs"

    checked = git(root, "rev-parse", "HEAD")
    if checked.returncode or checked.stdout.strip().decode() != head:
        return [], "requested head differs from the checkout"
    diff = git(root, "diff", "--name-status", "-z", "--no-renames", base, head, "--")
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


def shared_input_reason(changed: Sequence[Path]) -> str:
    return next((f"graph or runner input: {path}" for path in changed if shared_input(path)), "")


def addon_graph(root: Path, apps: Sequence[str]) -> AppGraph:
    from django_compose.components import inspect_apps, installed_components

    index = installed_components(GROUP)
    roots = list(apps)
    for name in sorted(index.providers.keys() - set(apps)):
        try:
            spec = find_spec(name)
        except ModuleNotFoundError:
            spec = None
        if spec is not None:
            roots.append(name)
        elif origin := index.distributions[index.providers[name]].origin:
            source = urlsplit(origin.url)
            if source.scheme == "file" and Path(url2pathname(source.path)).resolve().is_relative_to(root):
                # An unimportable local provider is a configuration defect,
                # even if it isn't part of the current shared test host.
                roots.append(name)
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


def selectable(path: Path, apps: AppGraph) -> bool:
    # An addon's frontend may be read by its Python contracts or by consumer tests.
    return python_input(path) or (path.suffix != ".md" and addon_at(path, apps.owners) is not None)


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


def uv_export(root: Path, extra: Sequence[str], *, prune: Sequence[str] = ()) -> dict:
    """Read uv's standard resolved install records without installing another environment."""

    result = subprocess.run(
        ["uv", "export", "--frozen", "--offline", "--format", "pylock.toml",
         *(arg for name in extra for arg in ("--extra", name)),
         *(arg for name in prune for arg in ("--prune", name))],
        cwd=root, capture_output=True, check=True, timeout=30,
    )
    return tomllib.loads(result.stdout.decode())


def global_install_effect(records: Sequence[dict], donor: Path | None, root: Path) -> bool:
    """Detect changed effective donor records and installation-wide Python hooks."""

    def active(record: dict) -> bool:
        return "marker" not in record or Marker(record["marker"]).evaluate(context="lock_file")

    donor_records = {
        record["name"]: record for record in tomllib.loads(donor.read_text())["packages"] if active(record)
    } if donor else {}
    for record in records:
        if not active(record):
            continue
        name = record["name"]
        if name in donor_records:
            prior = donor_records[name]
            if "directory" in record and "directory" in prior:
                actual = root / record["directory"]["path"]
                previous = donor.parent / prior["directory"]["path"]
                prior = {**prior, "directory": {**prior["directory"], "path": record["directory"]["path"]}}
                if actual.resolve() != previous.resolve():
                    return True
            if record != prior:
                return True
            continue
        distribution = metadata.distribution(name)
        if distribution.entry_points.select(group="pytest11") or distribution.files is None:
            return True
        if any(
            len(path.parts) == 1 and (path.suffix == ".pth" or path.stem in {"sitecustomize", "usercustomize"})
            for path in distribution.files
        ):
            return True
        if distribution.entry_points.select(group="console_scripts"):
            scripts = {entry.name for entry in distribution.entry_points.select(group="console_scripts")}
            if any(
                entry.name in scripts and entry.dist.name != distribution.name
                for entry in metadata.entry_points(group="console_scripts")
            ):
                return True
    return False


def additive_addons(
    root: Path, base: str | None, changes: Sequence[Path], graph: AppGraph, isolated: dict[Path, str],
    *, apps: Sequence[str], extra: Sequence[str], donor: Path | None,
) -> dict[Path, str]:
    """Prove that new standalone addon roots account for the entire native install delta."""

    if not base or Path("pyproject.toml") not in changes:
        return {}
    additions = {
        path: name for path, name in isolated.items()
        if path / "pyproject.toml" in changes and absent_at(root, base, path)
        and conventional_test_dir(root, path) is not None
    }
    new_apps = set(additions.values())
    if not additions or new_apps.intersection(apps) or reverse_addons(new_apps, graph.dependencies) != new_apps:
        return {}
    if any(
        path not in WORKSPACE_INPUTS | {Path("pnpm-lock.yaml"), STACK_LOCK} and path.suffix != ".md"
        and addon_at(path, additions) is None
        for path in changes
    ) or git(root, "diff", "--quiet", "HEAD", "--").returncode:
        return {}

    from django_compose.components import installed_components

    try:
        index = installed_components(GROUP)
        distributions = {index.providers[name]: path for path, name in additions.items()}
        previous = tomllib.loads(git(root, "show", f"{base}:pyproject.toml").stdout.decode())
        current = tomllib.loads((root / "pyproject.toml").read_text())
        uv = current["tool"]["uv"]
        for name, path in distributions.items():
            distribution = index.distributions[name]
            if distribution.entry_points.select(group="pytest11"):
                return {}
            current["project"]["dependencies"].remove(f"{name}=={distribution.version}")
            if uv["sources"].pop(name) != {"workspace": True}:
                return {}
            if path.as_posix() in uv["workspace"]["members"]:
                uv["workspace"]["members"].remove(path.as_posix())
        if current != previous:
            return {}
        archive = git(root, "archive", base)
        archive.check_returncode()
        with TemporaryDirectory(prefix="angee-selection-") as directory:
            with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tree:
                tree.extractall(directory, filter="data")
            before = uv_export(Path(directory), extra)
        if uv_export(root, extra, prune=tuple(sorted(distributions))) != before:
            return {}
        after = uv_export(root, extra)
        records = after.pop("packages")
        for record in before.pop("packages"):
            records.remove(record)
        for name, path in distributions.items():
            records.remove({"name": name, "directory": {"path": path.as_posix(), "editable": True}})
        if after == before and not global_install_effect(records, donor, root):
            return additions
    except (ImportError, KeyError, ValueError, OSError, subprocess.SubprocessError, tarfile.TarError):
        pass
    return {}


def conventional_test_dir(root: Path, directory: Path) -> Path | None:
    """Recognize the one packaged layout whose test-only import edges we can prove."""
    from iniconfig import IniConfig, ParseError

    try:
        config = IniConfig(str(root / directory / "pytest.ini"))
    except (OSError, ParseError):
        return None
    tests = directory / "tests"
    if config.get("pytest", "testpaths") == "tests" and (root / tests / "__init__.py").is_file():
        return tests
    return None


def conftest_imports(path: Path, reference: re.Pattern[str]) -> set[str]:
    source = path.read_text()
    loaded = set(reference.findall(source))
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            loaded.update((node.module, *(f"{node.module}.{alias.name}" for alias in node.names)))
        elif isinstance(node, ast.Import):
            loaded.update(alias.name for alias in node.names)
    return loaded


def import_reach(
    root: Path, apps: AppGraph, changed: Sequence[Path], tests: Sequence[Path], affected: set[str],
    *, python_inputs: bool, isolated: dict[Path, str],
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
    try:
        graph = grimp.build_graph(*sorted(packages), cache_dir=None)
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
    for directory, addon in isolated.items():
        conftest = root / directory / "conftest.py"
        if conftest.is_file() and reach.intersection(conftest_imports(conftest, reference)):
            reach.add(f"{addon}.conftest")
    root_conftest = root / "conftest.py"
    if python_inputs and root_conftest.is_file() and reach.intersection(conftest_imports(root_conftest, reference)):
        selected.update(tests)
        reach.add("conftest")
        return selected, reach
    for path in tests:
        module = module_name(path, owners)
        if module in reach or python_inputs and any(
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
    added_apps: Sequence[str] = (), shared_apps: frozenset[str] | None = None,
    preflight: bool = False,
) -> tuple[Selection, Selection]:
    """Select shared-host pytest paths and standalone addon names using the same changed inputs."""
    isolated = isolated or {}
    changes = [path for path in changed if selectable(path, apps)]
    full_hosts = tuple(sorted(isolated.values()))
    fallback = fallback or shared_input_reason(changes)
    if fallback:
        return Selection("full", fallback), Selection("full", fallback, full_hosts)
    if not changes and not added_apps:
        # Consumer contract suites can read frontend declarations directly.
        paths = [path.as_posix() for path in shared_tests] if not legacy and any(
            path.suffix != ".md" for path in changed
        ) else []
        return Selection.reached(paths), Selection.reached(())

    test_dirs = {directory: conventional_test_dir(root, directory) for directory in isolated}
    owner_tests: set[str] = set()
    source_apps = set(added_apps)
    broad_shared = ""
    broad_hosts = "custom isolated test layout" if None in test_dirs.values() else ""
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
        if (test_dir := test_dirs.get(directory)) and path.is_relative_to(test_dir):
            owner_tests.add(addon)
            if path.suffix != ".py":
                broad_shared = f"test data input: {path}"
            continue
        if path not in tests:
            source_apps.add(addon)
        if (
            (path.name in APP_INPUTS and (shared_apps is None or addon in shared_apps))
            or (path.name not in APP_INPUTS and relative.parts[0] != "resources"
                and path.suffix != ".py" and python_input(path))
            or path.name == "conftest.py"
            or relative.parts[0] == "testing"
        ):
            broad_shared = f"shared-host input: {path}"
        # Test plugins are often loaded by argv or conftest, independent of native addon edges.
        if relative.parts[0] in {"testing", "testing.py"}:
            broad_hosts = f"test support input: {path}"

    affected = reverse_addons(source_apps, apps.dependencies)
    python_inputs = bool(added_apps) or any(map(python_input, changes))
    imports = import_reach(
        root, apps, changes, tests, affected, python_inputs=python_inputs, isolated=isolated,
    ) if not broad_shared or full_hosts else None
    reached, importers = imports or (set(), set())
    if imports is None and not broad_shared:
        broad_shared = "Python import graph could not account for a changed input"
    if legacy and python_inputs and source_apps.intersection(
        {"angee.base", "angee.server", "angee.graphql"}
    ):
        broad_shared = "shared framework owner changed"
    if source_apps or not legacy and not owner_tests:
        reached.update(shared_tests)
    selected = {path.as_posix() for path in reached}
    if not broad_shared and tests and set(tests) <= reached:
        broad_shared = "changed inputs reach the whole shared host"
    shared = Selection("full", broad_shared) if broad_shared else Selection.reached(sorted(selected))
    if preflight and shared.mode == "empty":
        dirs = tuple(test_dir for test_dir in test_dirs.values() if test_dir is not None)
        modules = tuple(module_name(directory / "__init__.py", apps.owners) for directory in dirs)
        if any(not any(path.is_relative_to(directory) for directory in dirs) for path in changes) or any(
            name in {"conftest", "tests"} or (
                (name.startswith("tests.") or ".tests." in name)
                and not any(name.startswith(f"{module}.") for module in modules)
            )
            for name in importers
        ):
            shared = Selection("selected", "native pytest paths after shared host setup")
    if added_apps and shared.mode != "full":
        shared = Selection(shared.mode, "verified additive addon inputs", shared.paths)

    # Test-helper imports can cross suite boundaries independently of native app requirements.
    owner_tests.update(addon for addon in full_hosts if any(name.startswith(f"{addon}.") for name in importers))
    if imports is None and full_hosts:
        broad_hosts = "isolated test-support imports unavailable"
    if any(path.parts[0] == "tests" and path not in tests for path in changes):
        broad_hosts = "legacy test support or profile changed"
    hosts = Selection("full", broad_hosts, full_hosts) if broad_hosts else Selection.reached(
        sorted(set(full_hosts) & (affected | owner_tests)),
    )
    if added_apps and hosts.mode == "selected":
        hosts = Selection("selected", "verified additive addon inputs", hosts.paths)
    return shared, hosts


def pnpm_projects(root: Path, projects: Sequence[Path] = ()) -> set[Path]:
    """Project pnpm's workspace package paths and native reverse selection."""

    result = subprocess.run(
        ["pnpm", *(arg for path in projects for arg in ("--filter", f"...{{./{path.as_posix()}}}")),
         "-r", "list", "--depth", "-1", "--lockfile-only", "--json"],
        cwd=root, capture_output=True, check=True, timeout=30,
    )
    return {
        path.relative_to(root) for package in json.loads(result.stdout)
        if (path := Path(package["path"]).resolve()).is_relative_to(root)
    }


def additive_web_lock(root: Path, base: str, lock: Path, web_projects: set[Path]) -> bool:
    """Verify that native workspace importers are the only JS lock change."""

    import yaml

    try:
        previous = yaml.safe_load(git(root, "show", f"{base}:{lock.as_posix()}").stdout)
        current = yaml.safe_load((root / lock).read_text())
        importers = current["importers"]
        for path in web_projects:
            del importers[path.as_posix()]
        return previous == current
    except (KeyError, TypeError, OSError, yaml.YAMLError):
        return False


def web_plan(
    root: Path, changes: Sequence[Path], graph: AppGraph, isolated: dict[Path, str],
    *, base: str | None, fallback: str, additions: dict[Path, str], graph_fallback: str, framework: bool = False,
) -> tuple[Selection, Selection]:
    """Project independent package tests and native composed integration roots."""

    if fallback:
        return Selection("full", fallback), Selection("full", fallback)
    paths = [path for path in changes if path.suffix != ".md"]
    global_js = JS_INPUTS.intersection(paths)
    if any(path.parts[0] == ".github" and path != STACK_LOCK for path in paths):
        global_js.add(Path(".github"))
    new_web = {
        directory / "web" for directory in graph.owners
        if base and directory / "web/package.json" in paths
        and absent_at(root, base, directory / "web")
    }
    package_paths = [
        path for path in paths
        if path.parts[0] == "packages" or (
            "web" in path.parts and addon_at(path, graph.owners) is not None
            and (root / "pnpm-workspace.yaml").is_file()
        )
    ]
    package_targets: set[Path] = set()
    reached: set[Path] = set()
    try:
        if package_paths or (new_web and Path("pnpm-lock.yaml") in global_js):
            projects = pnpm_projects(root)
            if (
                base and new_web and new_web <= projects and Path("pnpm-lock.yaml") in global_js
                and additive_web_lock(root, base, Path("pnpm-lock.yaml"), new_web)
            ):
                global_js.remove(Path("pnpm-lock.yaml"))
            for path in package_paths:
                owner = next((parent for parent in path.parents if parent in projects), None)
                if owner is None or not owner.parts:
                    global_js.add(path)
                    break
                package_targets.add(owner)
            if package_targets and not global_js:
                reached = pnpm_projects(root, sorted(package_targets))
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError):
        reason = "native pnpm workspace unavailable"
        return Selection("full", reason), Selection("full", reason)
    if global_js:
        reason = f"shared JS or workflow input: {min(global_js)}"
        return Selection("full", reason), Selection("full", reason)
    packages = Selection.reached([path.as_posix() for path in reached if path.parts[:1] == ("packages",)])
    if STACK_LOCK in paths and not (
        base and new_web and additive_web_lock(
            root, base, STACK_LOCK, {Path("workspaces/src") / root.name / path for path in new_web},
        )
    ):
        return packages, Selection("full", f"shared rendered lock input: {STACK_LOCK}")
    roots = {name for path, name in additions.items() if (root / path / "web/package.json").is_file()}
    source_apps = set()
    runtime_inputs = False
    for path in paths:
        if (
            path.parts[0] in {"templates", "scripts"}
            or path.name in {"settings.py", "settings.yaml", ".python-version"}
        ):
            return packages, Selection("full", f"shared composition input: {path}")
        if addon_at(path, additions) is not None or path in WORKSPACE_INPUTS:
            continue
        owner = addon_at(path, graph.owners)
        if owner is None and path.parts[0] == "src":
            return packages, Selection("full", graph_fallback or f"unowned composition input: {path}")
        if owner:
            directory, name = owner
            test_dir = conventional_test_dir(root, directory) if directory in isolated else None
            if (
                (test_dir and path.is_relative_to(test_dir))
                or path.relative_to(directory).parts[0] in {"tests", "testing"}
            ):
                continue
            source_apps.add(name)
            runtime_inputs |= python_input(path)
    if graph_fallback and (source_apps or reached or any(path.parts[0] == "src" for path in paths)):
        return packages, Selection("full", graph_fallback)
    affected = reverse_addons(source_apps, graph.dependencies)
    local = set(graph.owners.values())
    external = {name for name in graph.dependencies.keys() - local if name.startswith("angee.")}
    if framework and runtime_inputs and not external:
        return packages, Selection("full", "existing runtime input with no native external addon graph")
    roots.update(
        name for directory, name in graph.owners.items()
        if (name in affected and (root / directory / "web/package.json").is_file())
        or any(path.is_relative_to(directory) for path in reached)
    )
    roots.update(affected & external)
    return packages, Selection.reached(sorted(roots))


class Collection:
    """Ask pytest for its unsplit collection and the node-ID prefix of each file."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.shared_paths: set[str] = set()
        self.apps: set[str] = set()

    @pytest.hookimpl(tryfirst=True)
    def pytest_configure(self, config: pytest.Config) -> None:
        # Keep the native arguments and plugins; disable distribution in this probe.
        config.option.numprocesses = 0
        config.option.splits = None

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        from django.apps import apps

        self.files = {str(item.path): item.nodeid.partition("::")[0] for item in session.items}
        self.shared_paths = {str(item.path) for item in session.items if item.get_closest_marker("shared_inputs")}
        self.apps = {app.name for app in apps.get_app_configs()} if apps.ready else set()


def collected(
    root: Path, args: Sequence[str], *, setup_django: bool = False,
) -> tuple[dict[Path, str], list[Path], frozenset[str]]:
    command = [
        sys.executable, str(Path(__file__).resolve()), "_collect", "django" if setup_django else "pytest", *args,
    ]
    environ = {**os.environ, "PYTHONPATH": os.pathsep.join(map(os.path.abspath, sys.path))}
    result = subprocess.run(command, cwd=root, env=environ, capture_output=True, text=True, check=False)
    if result.returncode not in {pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED}:
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)
    data = json.loads(result.stdout)
    return (
        {Path(path).relative_to(root): node for path, node in data["files"].items()},
        [Path(path).relative_to(root) for path in data["shared_paths"]],
        frozenset(data["apps"]),
    )


def shared_pytest_args(root: Path, args: Sequence[str], suites: dict[Path, str]) -> list[str]:
    """Exclude native standalone addon suite roots before pytest loads their conftests."""

    return [
        *shared_targets(root, args, suites),
        *(arg for path in standalone_test_roots(root, suites) for arg in ("--ignore", str(path))),
    ]


def standalone_test_roots(root: Path, suites: dict[Path, str]) -> list[Path]:
    return [root / (conventional_test_dir(root, directory) or directory) for directory in sorted(suites)]


def shared_targets(root: Path, args: Sequence[str], suites: dict[Path, str]) -> list[str]:
    excluded = standalone_test_roots(root, suites)
    return [
        arg for arg in args if not any(
            (root / arg.partition("::")[0]).resolve().is_relative_to(directory) for directory in excluded
        )
    ]


@dataclass(frozen=True)
class Inputs:
    changes: Sequence[Path]
    python: Sequence[Path]
    graph: AppGraph
    suites: dict[Path, str]
    additions: dict[Path, str]
    comparison_fallback: str
    graph_fallback: str

    @property
    def fallback(self) -> str:
        return self.comparison_fallback or self.graph_fallback or shared_input_reason(self.python)


def unchanged_manifest(root: Path, base: str | None, path: Path) -> bool:
    if not base or path.name != "pyproject.toml":
        return False
    previous = git(root, "show", f"{base}:{path.as_posix()}")
    if previous.returncode:
        return False
    try:
        return tomllib.loads(previous.stdout.decode()) == tomllib.loads((root / path).read_text())
    except (OSError, ValueError):
        return False


def inputs(
    root: Path, base: str | None, head: str | None, *,
    apps: Sequence[str], extra: Sequence[str], donor: Path | None,
) -> Inputs:
    changes, fallback = changed_paths(root, base, head)
    changes = [path for path in changes if not unchanged_manifest(root, base, path)]
    graph, graph_fallback = installed_graph(root, apps)
    suites = isolated_suites(root, graph.owners)
    if graph_fallback:
        suites = {
            path.parent.relative_to(root): ".".join(path.parent.relative_to(root / "src").parts)
            for path in sorted(root.glob("src/*/*/pytest.ini"))
        }
    additions = {} if fallback or graph_fallback else additive_addons(
        root, base, changes, graph, suites, apps=apps, extra=extra, donor=donor,
    )
    python = [
        path for path in changes
        if path not in JS_INPUTS | {STACK_LOCK} and path.parts[0] != "packages"
        and (not additions or (path not in WORKSPACE_INPUTS and addon_at(path, additions) is None))
    ]
    return Inputs(changes, python, graph, suites, additions, fallback, graph_fallback)


def run_selected(
    pytest_args: Sequence[str], *, root: Path, base: str | None, head: str | None, apps: Sequence[str] = (),
    extra: Sequence[str] = (), donor: Path | None = None,
) -> int:
    """Run the caller's native pytest invocation with a visible changed-path selection."""
    from django.apps import apps as registry

    callers = Path.cwd()
    root = root.resolve()
    import_path = list(sys.path)
    os.chdir(root)
    sys.path.insert(0, str(root))
    try:
        plan = inputs(root, base, head, apps=apps, extra=extra, donor=donor)
        fallback = plan.fallback
        args = shared_pytest_args(root, pytest_args, plan.suites)
        if registry.ready and "DJANGO_SETTINGS_MODULE" not in os.environ:
            fallback = fallback or "caller Django settings are local to its interpreter"
        if fallback:
            print(json.dumps(asdict(Selection("full", fallback))), flush=True)
            return pytest.main(args)

        tests, shared_tests, shared_apps = collected(root, args, setup_django=registry.ready)
        selection, _ = select(
            root, plan.python, tuple(tests), plan.graph, isolated=plan.suites, shared_tests=shared_tests,
            shared_apps=shared_apps, added_apps=tuple(plan.additions.values()),
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
        status = pytest.main([*args, *options])
    finally:
        sys.path[:] = import_path
        os.chdir(callers)
    # A successfully collected nonempty selection may have no items in a particular shard.
    return 0 if status == pytest.ExitCode.NO_TESTS_COLLECTED and "--splits" in pytest_args else status


def installed_graph(root: Path, apps: Sequence[str]) -> tuple[AppGraph, str]:
    from django.core.exceptions import ImproperlyConfigured

    try:
        return addon_graph(root, apps), ""
    except (ImportError, ImproperlyConfigured) as error:
        return AppGraph({}, {}, {}), f"native app graph unavailable: {error}"


def selection_plan(
    root: Path, base: str | None, head: str | None, *, framework: bool,
    apps: Sequence[str], extra: Sequence[str], donor: Path | None, pytest_args: Sequence[str],
) -> tuple[dict[str, Selection], list[int]]:
    plan = inputs(root, base, head, apps=apps, extra=extra, donor=donor)
    packages, stack = web_plan(
        root, plan.changes, plan.graph, plan.suites, base=base, fallback=plan.comparison_fallback,
        additions=plan.additions, graph_fallback=plan.graph_fallback, framework=framework,
    )
    if not plan.additions and WORKSPACE_INPUTS.intersection(plan.python):
        stack = Selection("full", "unverified Python workspace change")
    changed = any(selectable(path, plan.graph) for path in plan.python)
    tests = {}
    shared_tests = []
    shared_apps = None
    if framework and not plan.fallback and (changed or plan.additions):
        args = shared_pytest_args(root, pytest_args, plan.suites)
        tests, shared_tests, shared_apps = collected(root, args)
    shared, hosts = select(
        root, plan.python, tuple(tests), plan.graph, fallback=plan.fallback,
        isolated=plan.suites, legacy=True, shared_tests=shared_tests,
        shared_apps=shared_apps, added_apps=tuple(plan.additions.values()), preflight=not framework,
    )
    groups = []
    if framework:
        from scripts.postgres_selection import GROUPS
        from scripts.postgres_selection import groups as duration_groups

        if shared.mode == "full":
            config = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]
            roots = shared_targets(root, config["testpaths"], plan.suites)
            shared = Selection("full", shared.reason, tuple(roots))
            groups = list(range(1, GROUPS + 1))
        elif shared.mode == "selected":
            durations = json.loads((root / ".test_durations").read_text())
            groups = duration_groups([path.as_posix() for path in tests], shared.paths, durations) if durations else [1]
    return {"shared": shared, "hosts": hosts, "packages": packages, "stack": stack}, groups


def main(argv: Sequence[str]) -> int:
    if argv[:1] == ["_collect"]:
        probe = Collection()
        with redirect_stdout(sys.stderr):
            if argv[1] == "django":
                import django

                django.setup()
            status = pytest.main(["--collect-only", *argv[2:]], plugins=[probe])
        if status in {pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED}:
            print(json.dumps({
                "files": probe.files, "shared_paths": sorted(probe.shared_paths), "apps": sorted(probe.apps),
            }))
        return status
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("command", choices=("framework", "plan", "pytest"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--app", action="append", default=[])
    parser.add_argument("--extra", action="append", default=[])
    parser.add_argument("--donor", type=Path)
    options, pytest_args = parser.parse_known_args(argv)
    pytest_args = pytest_args[1:] if pytest_args[:1] == ["--"] else pytest_args
    if options.command != "pytest":
        framework = options.command == "framework"
        plan, groups = selection_plan(
            options.root.resolve(), options.base, options.head, framework=framework,
            apps=options.app, extra=options.extra, donor=options.donor, pytest_args=pytest_args,
        )
        data = {name: asdict(selection) for name, selection in plan.items()}
        print(json.dumps({**data, "groups": groups} if framework else data))
        return 0
    return run_selected(
        pytest_args,
        root=options.root, base=options.base, head=options.head, apps=options.app,
        extra=options.extra, donor=options.donor,
    )


if __name__ == "__main__":
    sys.path.insert(0, str(Path.cwd()))
    raise SystemExit(main(sys.argv[1:]))
