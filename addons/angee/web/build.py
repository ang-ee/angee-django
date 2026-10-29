"""Web runtime manifest projected from the composed addon graph.

The optional web addon's build projector. It is deliberately offline and pure: it reads
native ``web/package.json`` documents for enabled apps and renders two files under ``runtime/web/`` —
``manifest.json`` (the package graph, each package's addon ancestry, codegen
contributions and the deployment's ``ANGEE_UI`` layer) and
``tailwind.sources.css`` (the Tailwind ``@source`` include). It holds **no**
GraphQL-schema knowledge: which schemas exist, whether each is live, and the
shape of their operation documents are owned by the SDL on disk and the
``@angee/app`` ``angee-web-codegen`` CLI that reads this manifest. Generating
``runtime/web/app.ts`` is the CLI's job, not the composer's, so that no
schema-shaped TypeScript is ever authored in Python.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from angee.addons import addon_dependencies, is_angee_addon, resolve_package_roots
from angee.compose.declarations import BuildIntegration
from angee.compose.runtime import GENERATED_SENTINEL

if TYPE_CHECKING:
    from angee.compose.runtime import Runtime

CORE_WEB_PACKAGES: tuple[str, ...] = ("@angee/app", "@angee/ui")
"""Framework packages whose rendered sources and documents every host consumes."""

WEB_PACKAGE_RE = re.compile(r"^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$")
DEFAULT_WEB_ROOT = "../../web"
UI_LAYER_KEYS = frozenset({"menus", "containers", "shell"})
"""Manifest keys the deployment's ``ANGEE_UI`` setting may declare; ``@angee/app`` validates their values."""


def addon_ancestors(app_configs: Iterable[AppConfig]) -> dict[str, tuple[str, ...]]:
    """Project the host's dependency closure as sorted enabled-app ancestry."""

    configs = tuple(app_configs)
    aliases = {
        alias: config.name
        for config in configs
        for alias in (config.label, config.name, f"{type(config).__module__}.{type(config).__qualname__}")
    }
    packages = tuple({"name": config.name, "depends_on": addon_dependencies(config)} for config in configs)
    return {
        config.name: tuple(sorted(
            package["name"] for package in resolve_package_roots((config.name,), packages, aliases=aliases)
            if package["name"] != config.name
        ))
        for config in configs
    }


class WebRuntime:
    """Project enabled addon packages into the web transport document and CSS."""

    def __init__(
        self,
        addons: Iterable[AppConfig],
        *,
        runtime_dir: Path | None = None,
        web_root: str = DEFAULT_WEB_ROOT,
        ui: Mapping[str, Any] | None = None,
    ) -> None:
        addons = tuple(addons)
        self.runtime_dir = runtime_dir
        self.web_root = web_root
        ancestors = addon_ancestors(addons)
        core_packages = [{"package": name, "sourceRoot": "src"} for name in CORE_WEB_PACKAGES]
        addon_packages: list[dict[str, Any]] = []
        codegen_entries: list[dict[str, Any]] = []
        packages_seen: dict[str, str] = {}
        schemas_seen: dict[str, str] = {}
        for addon in addons:
            if not is_angee_addon(addon):
                continue
            document = self._package_document(addon)
            if document is None:
                continue
            package = document.get("name")
            if not isinstance(package, str) or not WEB_PACKAGE_RE.fullmatch(package):
                raise ImproperlyConfigured(f"{addon.name} web/package.json name must be a valid npm package name")
            if package is not None:
                if package in packages_seen:
                    raise ImproperlyConfigured(
                        f"Duplicate web/package.json name {package!r}: {packages_seen[package]} and {addon.name}"
                    )
                packages_seen[package] = addon.name
                entry: dict[str, Any] = {
                    "package": package, "sourceRoot": "src", "app": addon.name, "label": addon.label,
                }
                if self.runtime_dir is not None:
                    package_root = Path(addon.path).resolve() / "web"
                    entry["root"] = Path(os.path.relpath(package_root, self.runtime_dir.resolve() / "web")).as_posix()
                addon_packages.append(entry)
            web = document.get("angee", {})
            if not isinstance(web, Mapping):
                raise ImproperlyConfigured(f"{addon.name} web/package.json angee must be an object")
            if "codegen" in web:
                codegen = self._codegen_entry(addon, web["codegen"], package)
                schema = codegen["schema"]
                if schema in schemas_seen:
                    raise ImproperlyConfigured(
                        f"Duplicate web/package.json angee.codegen.schema {schema!r}: "
                        f"{schemas_seen[schema]} and {addon.name}"
                    )
                schemas_seen[schema] = addon.name
                codegen_entries.append(codegen)
        package_apps = {entry["app"] for entry in addon_packages}
        for entry in addon_packages:
            entry["dependsOn"] = [name for name in ancestors[entry["app"]] if name in package_apps]
        self.manifest: dict[str, Any] = {
            "schema": 1,
            "corePackages": core_packages,
            "addonPackages": addon_packages,
            "codegen": codegen_entries,
            "documentRoots": [
                {"kind": "package", "package": entry["package"], "path": f"node_modules/{entry['package']}/src"}
                for entry in (*core_packages, *addon_packages)
            ]
            + [{"kind": "host", "path": "src"}],
        }
        if ui:
            self.manifest["deployment"] = self._ui_layer(ui)

    def render_sources(self) -> dict[Path, str]:
        """Return generated web files keyed by runtime-relative path."""

        return {
            Path("web/manifest.json"): self.manifest_json(),
            Path("web/tailwind.sources.css"): self.tailwind_sources_css(),
        }

    def manifest_json(self) -> str:
        """Return the deterministic codegen transport document."""

        return json.dumps(self.manifest, indent=2, sort_keys=True) + "\n"

    def tailwind_sources_css(self) -> str:
        """Return the Tailwind source include consumed by host CSS."""

        packages = (*self.manifest["corePackages"], *self.manifest["addonPackages"])
        def source_path(entry: Mapping[str, str]) -> str:
            root = entry.get("root", f'{self.web_root}/node_modules/{entry["package"]}')
            source = f'{root}/{entry["sourceRoot"]}'
            if self.runtime_dir is None:
                return source
            css_root = self.runtime_dir.resolve() / "web"
            return Path(os.path.relpath((css_root / source).resolve(), css_root)).as_posix()

        return "\n".join(
            [
                f"/* {GENERATED_SENTINEL} */",
                "",
                *(f'@source "{source_path(entry)}";' for entry in packages),
                f'@source "{self.web_root}/src";',
                "",
            ]
        )

    @staticmethod
    def _ui_layer(ui: object) -> dict[str, Any]:
        """Admit the deployment's UI layer; ``@angee/app`` resolves and validates its values."""

        if not isinstance(ui, Mapping):
            raise ImproperlyConfigured("ANGEE_UI must be a mapping")
        unknown = sorted(set(ui) - UI_LAYER_KEYS)
        if unknown:
            raise ImproperlyConfigured(f"ANGEE_UI declares unknown keys {unknown}; allowed: {sorted(UI_LAYER_KEYS)}")
        try:
            return cast(dict[str, Any], json.loads(json.dumps(ui, sort_keys=True)))
        except (TypeError, ValueError) as error:
            raise ImproperlyConfigured("ANGEE_UI must contain only JSON values") from error

    @staticmethod
    def _package_document(addon: AppConfig) -> Mapping[str, Any] | None:
        """Read the native npm owner of fragment identity and codegen intent once."""

        package_json = Path(addon.path) / "web" / "package.json"
        if not package_json.is_file():
            return None
        try:
            document = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ImproperlyConfigured(f"{addon.name}: cannot read {package_json}") from error
        if not isinstance(document, Mapping):
            raise ImproperlyConfigured(f"{addon.name} web/package.json must be an object")
        return document

    @staticmethod
    def _codegen_entry(addon: AppConfig, raw: object, package: str) -> dict[str, Any]:
        """Validate the external schema declaration at its transport owner."""

        if not isinstance(raw, Mapping) or not {"schema", "sdl", "documents"} <= raw.keys():
            raise ImproperlyConfigured(
                f"{addon.name} web/package.json angee.codegen must declare 'schema', 'sdl', and 'documents'"
            )
        schema = raw["schema"]
        if not isinstance(schema, str) or not schema.isidentifier():
            raise ImproperlyConfigured(f"{addon.name} web/package.json angee.codegen.schema must be a model-safe name")
        for key in ("sdl", "documents"):
            if not isinstance(raw[key], str) or not raw[key]:
                raise ImproperlyConfigured(
                    f"{addon.name} web/package.json angee.codegen.{key} must be a non-empty string"
                )
        types = raw.get("types", False)
        if not isinstance(types, bool):
            raise ImproperlyConfigured(f"{addon.name} web/package.json angee.codegen.types must be a boolean")
        return {
            "schema": schema,
            "package": package,
            "sdl": raw["sdl"],
            "documents": raw["documents"],
            "app": addon.name,
            "types": types,
        }


class WebIntegration(BuildIntegration):
    """Contribute Angee's web projections to the shared generated-file lifecycle."""

    def render_sources(self, runtime: Runtime) -> Mapping[Path, str]:
        """Render the existing web owner with the host's declared UI settings."""

        return WebRuntime(
            runtime.addons, runtime_dir=runtime.runtime_dir, ui=getattr(settings, "ANGEE_UI", None),
        ).render_sources()
