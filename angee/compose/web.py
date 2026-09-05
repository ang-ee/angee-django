"""Web runtime manifest projected from the composed addon graph.

The composer's *web* projector. It is deliberately offline and pure: it reads
native addon manifests bound to ``AppConfig`` and renders two files under ``runtime/web/`` —
``manifest.json`` (the package graph + codegen contributions) and
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
from typing import Any

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured

from angee.addons import addon_manifest
from angee.fs import GENERATED_SENTINEL

CORE_WEB_PACKAGES: tuple[str, ...] = ("@angee/app", "@angee/ui")
"""Framework packages whose rendered sources and documents every host consumes."""

WEB_PACKAGE_RE = re.compile(r"^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$")
DEFAULT_WEB_ROOT = "../../web"


class WebRuntime:
    """Project native addon manifests into the web transport document and CSS."""

    def __init__(
        self,
        addons: Iterable[AppConfig],
        *,
        runtime_dir: Path | None = None,
        web_root: str = DEFAULT_WEB_ROOT,
    ) -> None:
        self.runtime_dir = runtime_dir
        self.web_root = web_root
        core_packages = [{"package": name, "sourceRoot": "src"} for name in CORE_WEB_PACKAGES]
        addon_packages: list[dict[str, str]] = []
        codegen_entries: list[dict[str, Any]] = []
        packages_seen: dict[str, str] = {}
        schemas_seen: dict[str, str] = {}
        for addon in addons:
            manifest = addon_manifest(addon)
            if manifest is None:
                continue
            package = self._package_name(addon, manifest.web)
            if package is not None:
                if package in packages_seen:
                    raise ImproperlyConfigured(
                        f"Duplicate [web].package {package!r}: {packages_seen[package]} and {addon.name}"
                    )
                packages_seen[package] = addon.name
                entry = {"package": package, "sourceRoot": "src", "app": addon.name, "label": addon.label}
                if self.runtime_dir is not None:
                    package_root = Path(addon.path).resolve() / "web"
                    entry["root"] = Path(os.path.relpath(package_root, self.runtime_dir.resolve() / "web")).as_posix()
                addon_packages.append(entry)
            if "codegen" in manifest.web:
                codegen = self._codegen_entry(addon, manifest.web["codegen"], package)
                schema = codegen["schema"]
                if schema in schemas_seen:
                    raise ImproperlyConfigured(
                        f"Duplicate [web].codegen.schema {schema!r}: {schemas_seen[schema]} and {addon.name}"
                    )
                schemas_seen[schema] = addon.name
                codegen_entries.append(codegen)
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
        return "\n".join(
            [
                f"/* {GENERATED_SENTINEL} */",
                "",
                *(f'@source "{self.web_root}/node_modules/{entry["package"]}/src";' for entry in packages),
                f'@source "{self.web_root}/src";',
                "",
            ]
        )

    @staticmethod
    def _package_name(addon: AppConfig, web: Mapping[str, Any]) -> str | None:
        """Use an explicit package declaration before conventional package.json."""

        if "package" in web:
            package = web["package"]
        else:
            package_json = Path(addon.path) / "web" / "package.json"
            if not package_json.is_file():
                return None
            try:
                document = json.loads(package_json.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise ImproperlyConfigured(f"{addon.name}: cannot read {package_json}") from error
            package = document.get("name") if isinstance(document, Mapping) else None
        if not isinstance(package, str) or not WEB_PACKAGE_RE.fullmatch(package):
            raise ImproperlyConfigured(f"{addon.name} addon.toml [web].package must be a valid npm package name")
        return package

    @staticmethod
    def _codegen_entry(addon: AppConfig, raw: object, package: str | None) -> dict[str, Any]:
        """Validate the external schema declaration at its transport owner."""

        if not isinstance(raw, Mapping) or not {"schema", "sdl", "documents"} <= raw.keys():
            raise ImproperlyConfigured(
                f"{addon.name} addon.toml [web].codegen must declare 'schema', 'sdl', and 'documents'"
            )
        schema = raw["schema"]
        if not isinstance(schema, str) or not schema.isidentifier():
            raise ImproperlyConfigured(f"{addon.name} [web].codegen.schema must be a model-safe name")
        if package is None:
            raise ImproperlyConfigured(f"{addon.name} [web].codegen requires [web].package")
        for key in ("sdl", "documents"):
            if not isinstance(raw[key], str) or not raw[key]:
                raise ImproperlyConfigured(f"{addon.name} [web].codegen.{key} must be a non-empty string")
        types = raw.get("types", False)
        if not isinstance(types, bool):
            raise ImproperlyConfigured(f"{addon.name} [web].codegen.types must be a boolean")
        return {
            "schema": schema,
            "package": package,
            "sdl": raw["sdl"],
            "documents": raw["documents"],
            "app": addon.name,
            "types": types,
        }
