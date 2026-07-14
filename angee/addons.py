"""Plain AppConfig helpers for Angee addon declarations."""

from __future__ import annotations

import ast
import importlib
import json
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import metadata
from pathlib import Path
from typing import Any

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string, module_has_submodule

ADDON_ENTRY_POINT_GROUP = "angee.addons"


@dataclass(frozen=True, slots=True)
class AddonMigration:
    """One addon-owned Django migration materialized into a runtime app."""

    name: str
    app_label: str
    module: str


def _parse_migrations(raw: Any, *, marker: Path) -> tuple[AddonMigration, ...]:
    """Parse ordered ``[[migrations]]`` declarations from one addon manifest."""

    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ImproperlyConfigured(f"{marker}: migrations must be an array of tables")
    migrations: list[AddonMigration] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            raise ImproperlyConfigured(f"{marker}: migrations[{index}] must be a table")
        values: dict[str, str] = {}
        for key in ("name", "app_label", "module"):
            value = entry.get(key)
            if not isinstance(value, str) or not value:
                raise ImproperlyConfigured(f"{marker}: migrations[{index}] requires string {key}")
            values[key] = value
        migrations.append(AddonMigration(**values))
    return tuple(migrations)


@dataclass(frozen=True, slots=True)
class AddonContract:
    """An addon's declarative contract, read from its co-located ``addon.toml``.

    The manifest reuses pyproject's metadata vocabulary verbatim
    (``description``/``keywords``/``license``/``readme``/``version``/``authors``/
    ``urls``) so hatch-angee can compile it straight into the package's
    ``[project]``. The Angee-owned fields are only what pyproject lacks: the addon
    ``name`` (import id), the inter-addon ``depends_on`` graph, the freeform
    ``category``, and the contribution seams — ordered ``migrations`` plus
    ``schemas``/``permissions`` (simple strings) in ``[addon]``;
    ``web``/``mcp``/``resources`` as their own sections.
    The presence of the manifest *is* the addon marker, so an addon needs an
    ``apps.py`` only when it has a ``python`` seam to run (``ready()``). The
    contribution seams default to what the directory reveals (``schema.py``,
    ``permissions.zed``, ``web/package.json``, ``mcp_tools.py``); a manifest entry
    only overrides that default. ``permissions`` records the ``.zed`` contribution
    for the catalog — a remote catalog reads the manifest, never the addon's files —
    while the runtime still discovers the ``.zed`` by convention (adjacent to the
    addon). Ordered ``[[migrations]]`` tables name addon-owned source modules that
    explicit runtime builds may materialize into downstream Django migration apps.
    """

    name: str
    depends_on: tuple[str, ...] = ()
    migrations: tuple[AddonMigration, ...] = ()
    schemas: str | None = None
    permissions: str | None = None
    web: str | None = None
    web_codegen: Mapping[str, Any] | None = None
    mcp_tools: str | None = None
    resources: Mapping[str, Any] = field(default_factory=dict)
    # Metadata — pyproject vocabulary, compiled into [project] by hatch-angee.
    description: str = ""
    keywords: tuple[str, ...] = ()
    category: str | None = None
    license: str | None = None
    readme: str | None = None
    version: str | None = None
    authors: tuple[Mapping[str, Any], ...] = ()
    urls: Mapping[str, str] = field(default_factory=dict)


def _module_defines(module_path: Path, symbol: str) -> bool:
    """Return whether a module file binds a top-level ``symbol``."""

    try:
        module = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    except SyntaxError as error:
        raise ImproperlyConfigured(f"{module_path} could not be parsed for addon seam inference") from error
    return any(_node_binds_symbol(node, symbol) for node in module.body)


def _node_binds_symbol(node: ast.stmt, symbol: str) -> bool:
    """Return whether a top-level AST node binds ``symbol``."""

    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name == symbol
    if isinstance(node, ast.Assign):
        return any(_target_binds_symbol(target, symbol) for target in node.targets)
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        return _target_binds_symbol(node.target, symbol)
    return False


def _target_binds_symbol(target: ast.expr, symbol: str) -> bool:
    """Return whether an assignment target binds ``symbol``."""

    if isinstance(target, ast.Name):
        return target.id == symbol
    if isinstance(target, (ast.Tuple, ast.List)):
        return any(_target_binds_symbol(element, symbol) for element in target.elts)
    return False


def _infer_contributions(addon_dir: Path) -> dict[str, str]:
    """Infer the path-derivable contribution seams present in an addon directory.

    The conventional file *is* the declaration — an explicit ``addon.toml`` entry
    only exists to override it. ``schema.py`` → the GraphQL ``schemas`` bucket,
    ``permissions.zed`` → the REBAC ``permissions`` contribution,
    ``web/package.json`` → the web package (its ``name``), and ``mcp_tools.py`` with
    a top-level ``register`` → the MCP ``tools`` seam. The dependency graph, resource
    tiers, and metadata are never inferred — order and intent are not path-derivable.
    """

    inferred: dict[str, str] = {}
    schema_module = addon_dir / "schema.py"
    if schema_module.is_file() and _module_defines(schema_module, "schemas"):
        inferred["schemas"] = "schema.schemas"
    if (addon_dir / "permissions.zed").is_file():
        inferred["permissions"] = "permissions.zed"
    package_json = addon_dir / "web" / "package.json"
    if package_json.is_file():
        name = json.loads(package_json.read_text()).get("name")
        if isinstance(name, str) and name:
            inferred["web"] = name
    mcp_module = addon_dir / "mcp_tools.py"
    if mcp_module.is_file() and _module_defines(mcp_module, "register"):
        inferred["mcp_tools"] = "mcp_tools.register"
    return inferred


@lru_cache(maxsize=None)
def _read_addon_contract(marker: str) -> AddonContract | None:
    """Parse one ``addon.toml`` into its contract (cached per path), or ``None``.

    Contribution seams default to what the addon directory reveals
    (:func:`_infer_contributions`); an explicit ``addon.toml`` entry overrides the
    inferred default. The marker path fully determines the directory scanned, so the
    per-path cache stays correct.
    """

    path = Path(marker)
    if not path.is_file():
        return None
    data = tomllib.loads(path.read_text())
    addon = data.get("addon", {})
    web = data.get("web", {})
    mcp = data.get("mcp", {})
    inferred = _infer_contributions(path.parent)
    raw_depends_on = addon.get("depends_on", ())
    return AddonContract(
        name=addon.get("name", ""),
        depends_on=(raw_depends_on,) if isinstance(raw_depends_on, str) else tuple(raw_depends_on),
        migrations=_parse_migrations(data.get("migrations"), marker=path),
        schemas=addon.get("schemas") or inferred.get("schemas"),
        permissions=addon.get("permissions") or inferred.get("permissions"),
        web=web.get("package") or inferred.get("web"),
        web_codegen=web.get("codegen"),
        mcp_tools=mcp.get("tools") or inferred.get("mcp_tools"),
        resources=data.get("resources", {}),
        description=addon.get("description", ""),
        keywords=tuple(addon.get("keywords", ())),
        category=addon.get("category"),
        license=addon.get("license"),
        readme=addon.get("readme"),
        version=addon.get("version"),
        authors=tuple(addon.get("authors", ())),
        urls=addon.get("urls", {}),
    )


def addon_contract(app_config: AppConfig) -> AddonContract | None:
    """Return the addon's declared contract.

    An explicit ``_addon_contract`` attribute wins — a code-defined addon (or a
    test) that carries its contract in memory rather than a file on disk. Otherwise
    the contract is parsed from the co-located ``addon.toml``. ``None`` for any app
    with neither (plain Django apps, non-addons).
    """

    explicit = getattr(app_config, "_addon_contract", None)
    if explicit is not None:
        return explicit
    path = getattr(app_config, "path", None)
    if path is None:
        return None
    return _read_addon_contract(str(Path(path) / "addon.toml"))


@dataclass(frozen=True, slots=True)
class AvailableAddon:
    """An addon present in the environment, whether or not it is enabled.

    ``source`` is ``"installed"`` for an addon advertised by an installed bundle's
    ``angee.addons`` entry point, or ``"local"`` for one discovered as an
    ``addon.toml`` under a configured addon dir. ``anchor`` is the entry point's
    import target (installed) or the addon directory (local).
    """

    name: str
    source: str
    anchor: str

    def contract(self) -> AddonContract | None:
        """Return this available addon's manifest contract, when it can be read.

        Local addons are anchored at their source directory. Installed addons are
        anchored at the ``angee.addons`` entry point's import target; walk that target
        back to the package directory that carries ``addon.toml`` without requiring a
        configured Django app registry.
        """

        if self.source == "local":
            return _read_addon_contract(str(Path(self.anchor) / "addon.toml"))
        target = self.anchor.split(":", 1)[0]
        parts = target.split(".")
        for end in range(len(parts), 0, -1):
            module_name = ".".join(parts[:end])
            try:
                module = importlib.import_module(module_name)
            except ImportError:
                continue
            for directory in _module_directories(module):
                contract = _read_addon_contract(str(directory / "addon.toml"))
                if contract is not None:
                    return contract
        return None


def _module_directories(module: Any) -> tuple[Path, ...]:
    """Return directories that may contain ``module``'s co-located addon manifest."""

    path = getattr(module, "__path__", None)
    if path is not None:
        return tuple(Path(item) for item in path)
    file = getattr(module, "__file__", None)
    if file is None:
        return ()
    return (Path(file).parent,)


def available_addons(addon_dirs: Iterable[Path | str] = ()) -> dict[str, AvailableAddon]:
    """Return every *available* addon, keyed by name.

    The available set is the union of (1) the ``angee.addons`` entry points across
    all installed distributions — the SSOT being ``uv.lock``'s bundles, the same
    way ``pip``-installed packages are "available" before being added to
    ``INSTALLED_APPS`` — and (2) any ``addon.toml`` under the configured addon dirs
    (local/uninstalled consumer addons). The enabled set (``INSTALLED_APPS``) is
    expected to be a subset of this. Pure ``importlib.metadata`` + filesystem; no
    Django app loading required, so a catalog/marketplace can read it cheaply.
    """

    available: dict[str, AvailableAddon] = {}
    for entry_point in metadata.entry_points(group=ADDON_ENTRY_POINT_GROUP):
        available[entry_point.name] = AvailableAddon(
            name=entry_point.name, source="installed", anchor=entry_point.value
        )
    for addon_dir in addon_dirs:
        for marker in sorted(Path(addon_dir).glob("**/addon.toml")):
            if "node_modules" in marker.parts:
                continue
            contract = _read_addon_contract(str(marker))
            name = contract.name if contract is not None else None
            if name and name not in available:
                available[name] = AvailableAddon(name=name, source="local", anchor=str(marker.parent))
    return dict(sorted(available.items()))


def is_angee_addon(app_config: AppConfig) -> bool:
    """Return whether ``app_config`` is an Angee addon.

    An app is an addon exactly when it carries a contract — a co-located
    ``addon.toml`` manifest (the on-disk case) or an in-memory ``_addon_contract``
    (a code-defined addon or a test). The manifest is the marker; no ``apps.py``
    opt-in flag is needed.
    """

    return addon_contract(app_config) is not None


def resolve_addon_reference(app_config: AppConfig, dotted: str, *, attr: str) -> Any:
    """Import the object a ``<attr>`` dotted reference on an addon names.

    A bare ``"module.name"`` is taken relative to the addon's import package
    (``app_config.name``); an already-qualified path is used as-is. Raises
    ``ImproperlyConfigured`` naming ``<addon>.<attr>`` on failure. The one owner of
    the manifest dotted-reference contract shared by the ``schemas`` (GraphQL) and
    ``mcp_tools`` (MCP) discovery seams — including the fail-fast that the reference
    is a dotted string in the first place.
    """

    if not isinstance(dotted, str):
        raise ImproperlyConfigured(f"{app_config.name}.{attr} must be a dotted reference")
    path = dotted if dotted.startswith(f"{app_config.name}.") else f"{app_config.name}.{dotted}"
    try:
        return import_string(path)
    except ImportError as error:
        raise ImproperlyConfigured(f"{app_config.name}.{attr} references {path!r}") from error


def addon_contribution(
    app_config: AppConfig,
    module_name: str,
    attr: str,
    *,
    allow_callable: bool = False,
) -> list[Any]:
    """Return an installed addon's conventional iterable contribution.

    Addon subsystems expose small conventional modules such as ``urls.py`` or
    ``asgi.py``. This helper owns the repeated Angee-addon gate, submodule check,
    import error shape, optional callable execution, and iterable validation.
    """

    if not is_angee_addon(app_config):
        return []
    if not module_has_submodule(app_config.module, module_name):
        return []
    module_path = f"{app_config.name}.{module_name}"
    try:
        module = importlib.import_module(module_path)
    except ImportError as error:
        raise ImproperlyConfigured(f"{module_path} failed to import") from error
    contribution = getattr(module, attr, None)
    if contribution is None:
        return []
    value = contribution() if allow_callable and callable(contribution) else contribution
    if not isinstance(value, Iterable):
        suffix = "iterable or callable" if allow_callable else "iterable"
        raise ImproperlyConfigured(f"{module_path}.{attr} must be {suffix}")
    return list(value)
