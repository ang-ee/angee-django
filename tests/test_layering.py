"""Guard the one-way dependency direction of Angee backend packages."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANGEE = ROOT / "angee"
BASE = ANGEE / "base"
GRAPHQL = ANGEE / "graphql"
COMPOSE = ANGEE / "compose"
RESOURCES = ROOT / "addons" / "angee" / "resources"  # resources is a base addon

# Derived from the directory so a new base addon is guarded automatically.
_ADDON_PACKAGES = tuple(f"angee.{path.name}" for path in sorted((ROOT / "addons" / "angee").iterdir()) if path.is_dir())


def _module_imports(path: Path) -> set[str]:
    """Return every dotted module name imported by one source file."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    module = _module_name(path)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                package = module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
                prefix = package[: len(package) - node.level + 1]
                imported = ".".join((*prefix, *(node.module or "").split(".")))
            else:
                imported = node.module or ""
            if imported:
                names.add(imported)
                names.update(f"{imported}.{alias.name}" for alias in node.names if alias.name != "*")
    return names


def _module_name(path: Path) -> str:
    """Return the importable module name for one in-repository Python source."""

    for source_root in (ROOT, ROOT / "addons"):
        try:
            relative = path.relative_to(source_root).with_suffix("")
        except ValueError:
            continue
        parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
        return ".".join(parts)
    raise ValueError(f"{path} is outside the repository's Python source roots")


def _module_path(module: str) -> Path | None:
    """Resolve one in-repository module name to its Python source file."""

    parts = module.split(".")
    for source_root in (ROOT, ROOT / "addons"):
        base = source_root.joinpath(*parts)
        module_file = base.with_suffix(".py")
        if module_file.is_file():
            return module_file
        package_file = base / "__init__.py"
        if package_file.is_file():
            return package_file
    return None


def _import_closure(entry_modules: tuple[str, ...]) -> set[str]:
    """Walk every repository-owned ``angee.*`` import reachable from entries."""

    closure: set[str] = set()
    visited: set[str] = set()
    pending = list(entry_modules)
    while pending:
        module = pending.pop()
        if module in visited:
            continue
        visited.add(module)
        closure.add(module)
        path = _module_path(module)
        if path is None:
            continue
        for imported in _module_imports(path):
            closure.add(imported)
            if imported.startswith("angee.") and imported not in visited and _module_path(imported) is not None:
                pending.append(imported)
    return closure


def _tree_imports(root: Path) -> set[str]:
    """Return the union of imports across a package subtree."""

    names: set[str] = set()
    for path in root.rglob("*.py"):
        names |= _module_imports(path)
    return names


def test_base_is_the_model_layer_below_all_siblings() -> None:
    """base (the model toolkit) imports no sibling subsystem or addon."""

    imports = _tree_imports(BASE)
    forbidden = ("angee.compose", "angee.graphql", *_ADDON_PACKAGES)
    assert not any(name.startswith(prefix) for name in imports for prefix in forbidden)


def test_no_shared_addon_config_base_module() -> None:
    """Addons use plain Django AppConfig attributes, not an Angee subclass."""

    assert not (ANGEE / "apps.py").exists()


def test_resources_does_not_import_compose() -> None:
    """The resource subsystem does not import build-time compose code."""

    imports = _tree_imports(RESOURCES)
    assert not any(name.startswith("angee.compose") for name in imports)


def test_graphql_does_not_import_compose() -> None:
    """The GraphQL runtime does not import build-time compose code."""

    imports = _tree_imports(GRAPHQL)
    assert not any(name.startswith("angee.compose") for name in imports)


def test_stable_serving_entrypoints_do_not_import_compose() -> None:
    """Serving entrypoints use Django's populated registry, not compose."""

    imports = _module_imports(ANGEE / "urls.py") | _module_imports(ANGEE / "asgi.py")
    forbidden = ("angee.compose",)
    assert not any(name.startswith(prefix) for name in imports for prefix in forbidden)


def test_compose_has_no_rebac_permission_renderer() -> None:
    """Per-addon REBAC schemas stay with their owning apps."""

    assert not (COMPOSE / "rebac.py").exists()


def test_live_console_import_path_stays_vendor_free() -> None:
    """The full console import closure excludes worker-only and vendor libraries."""

    forbidden = ("discord", "mautrix", "neonize", "olm", "telethon", "qrcode", "PIL", "Pillow")
    console_entries = (
        "angee.integrate.live",
        "angee.integrate.impl",
        "angee.integrate.tasks",
        "angee.messaging.backends",
        "angee.messaging_integrate_whatsapp.client",
        "angee.messaging_integrate_whatsapp.backend",
        "angee.messaging_integrate_whatsapp.connect",
        "angee.messaging_integrate_whatsapp.schema",
        "angee.messaging_integrate_telegram.backend",
        "angee.messaging_integrate_telegram.identity",
        "angee.messaging_integrate_telegram.connect",
        "angee.messaging_integrate_telegram.schema",
        "angee.messaging_integrate_telegram.autoconfig",
        "angee.messaging_integrate_signal.backend",
        "angee.messaging_integrate_signal.identity",
        "angee.messaging_integrate_signal.connect",
        "angee.messaging_integrate_signal.schema",
        "angee.messaging_integrate_signal.autoconfig",
        "angee.messaging_integrate_matrix.backend",
        "angee.messaging_integrate_matrix.identity",
        "angee.messaging_integrate_matrix.connect",
        "angee.messaging_integrate_matrix.schema",
        "angee.messaging_integrate_matrix.autoconfig",
        "angee.messaging_integrate_discord.backend",
        "angee.messaging_integrate_discord.identity",
        "angee.messaging_integrate_discord.connect",
        "angee.messaging_integrate_discord.schema",
        "angee.messaging_integrate_discord.autoconfig",
        "angee.messaging_integrate_slack.backend",
        "angee.messaging_integrate_slack.identity",
        "angee.messaging_integrate_slack.connect",
        "angee.messaging_integrate_slack.schema",
        "angee.messaging_integrate_slack.autoconfig",
    )
    closure = _import_closure(console_entries)
    assert not any(name == prefix or name.startswith(f"{prefix}.") for name in closure for prefix in forbidden)
    assert "angee.integrate.session" not in closure
    assert "angee.messaging.session" not in closure
    assert "angee.messaging_integrate_signal.session" not in closure
    assert "angee.messaging_integrate_matrix.session" not in closure
    assert "angee.messaging_integrate_discord.session" not in closure
