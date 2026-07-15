"""Web runtime manifest projected from the composed addon graph.

The composer's *web* projector. It is deliberately offline and pure: it reads
static ``AppConfig`` declarations and renders two files under ``runtime/web/`` —
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
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured

from angee.addons import addon_contract
from angee.fs import GENERATED_SENTINEL

CORE_WEB_PACKAGES: tuple[str, ...] = ("@angee/app", "@angee/ui")
"""Rendered framework packages every host must scan and compose against.

Scan and compose is all this drives: the Tailwind ``@source`` list and the
codegen document roots. A package that renders nothing and declares no documents
belongs to the host's ``package.json``, not here — listing it would contribute an
empty scan and an empty document root.
"""

WEB_PACKAGE_RE = re.compile(r"^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$")

DEFAULT_WEB_ROOT = "../../web"
"""Path from ``runtime/web/`` to the host web package when both are project siblings."""


@dataclass(frozen=True)
class WebPackage:
    """Web package projected into a composed web runtime manifest."""

    package: str
    source_root: str = "src"
    root: str | None = None
    app: str | None = None
    label: str | None = None

    def manifest_entry(self) -> dict[str, str]:
        """Return the JSON-safe manifest entry for this package."""

        entry = {
            "package": self.package,
            "sourceRoot": self.source_root,
        }
        if self.root is not None:
            entry["root"] = self.root
        if self.app is not None:
            entry["app"] = self.app
        if self.label is not None:
            entry["label"] = self.label
        return entry


@dataclass(frozen=True)
class WebCodegen:
    """An external GraphQL codegen pass an addon contributes to the manifest.

    The Django ``console``/``public`` schemas are emitted into
    ``runtime/schemas/`` (owned by :class:`~angee.graphql.sdl.GraphQLSdl`) and
    discovered by the CLI there. A schema owned elsewhere — the operator daemon —
    keeps its committed SDL in its own addon package; this entry records the
    package, the package-relative SDL path, and the document file so the CLI
    reads the SDL straight from ``node_modules`` and generates
    ``runtime/gql/<schema>/`` with the entry's config. The composer only collects
    and orders the declaration; it neither runs the daemon nor mixes external SDL
    into the Django-owned schema directory.
    """

    schema: str
    package: str
    sdl: str
    documents: str
    app: str
    types: bool = False

    def manifest_entry(self) -> dict[str, object]:
        """Return the JSON-safe manifest entry for this codegen pass."""

        return {
            "schema": self.schema,
            "package": self.package,
            "sdl": self.sdl,
            "documents": self.documents,
            "app": self.app,
            "types": self.types,
        }


class WebRuntime:
    """Render the ``runtime/web`` manifest + Tailwind sources from AppConfigs."""

    def __init__(
        self,
        addons: Iterable[AppConfig],
        *,
        runtime_dir: Path | None = None,
        web_root: str = DEFAULT_WEB_ROOT,
    ) -> None:
        """Create a web projector over ``addons`` rooted at ``web_root``."""

        self.addons = tuple(addons)
        self.runtime_dir = runtime_dir
        self.web_root = web_root
        self.core_packages = tuple(WebPackage(package) for package in CORE_WEB_PACKAGES)
        self.addon_packages = self._addon_packages()
        self.codegen_entries = self._codegen_entries()

    def render_sources(self) -> dict[Path, str]:
        """Return generated ``runtime/web`` files keyed by relative path."""

        return {
            Path("web/manifest.json"): self.manifest_json(),
            Path("web/tailwind.sources.css"): self.tailwind_sources_css(),
        }

    def manifest_json(self) -> str:
        """Return the deterministic web runtime manifest."""

        return (
            json.dumps(
                {
                    "schema": 1,
                    "corePackages": [package.manifest_entry() for package in self.core_packages],
                    "addonPackages": [package.manifest_entry() for package in self.addon_packages],
                    "codegen": [entry.manifest_entry() for entry in self.codegen_entries],
                    "documentRoots": self._document_roots(),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )

    def tailwind_sources_css(self) -> str:
        """Return the Tailwind source include consumed by host CSS."""

        lines = [
            f"/* {GENERATED_SENTINEL} */",
            "",
            *(
                f'@source "{self._web_package_source(package)}";'
                for package in (*self.core_packages, *self.addon_packages)
            ),
            f'@source "{self.web_root}/src";',
            "",
        ]
        return "\n".join(lines)

    def _addon_packages(self) -> tuple[WebPackage, ...]:
        """Return addon web packages in composed app order."""

        packages: list[WebPackage] = []
        seen: dict[str, AppConfig] = {}
        for addon in self.addons:
            contract = addon_contract(addon)
            raw_package = contract.web if contract is not None else None
            if raw_package is None:
                continue
            if not isinstance(raw_package, str) or not WEB_PACKAGE_RE.match(raw_package):
                raise ImproperlyConfigured(
                    f"{addon.name} addon.toml [web].package must be a valid npm package name"
                )
            previous = seen.setdefault(raw_package, addon)
            if previous is not addon:
                raise ImproperlyConfigured(
                    f"Duplicate [web].package {raw_package!r}: {previous.name} and {addon.name}"
                )
            packages.append(
                WebPackage(
                    raw_package,
                    root=self._addon_web_root(addon),
                    app=addon.name,
                    label=addon.label,
                )
            )
        return tuple(packages)

    def _addon_web_root(self, addon: AppConfig) -> str | None:
        """Return the addon's web root relative to the generated manifest."""

        if self.runtime_dir is None:
            return None
        addon_path = getattr(addon, "path", None)
        if not isinstance(addon_path, str) or not addon_path:
            return None
        manifest_dir = self.runtime_dir.resolve() / "web"
        package_root = Path(addon_path).resolve() / "web"
        return Path(os.path.relpath(package_root, manifest_dir)).as_posix()

    def _codegen_entries(self) -> tuple[WebCodegen, ...]:
        """Return external codegen contributions in composed app order."""

        entries: list[WebCodegen] = []
        seen: dict[str, AppConfig] = {}
        for addon in self.addons:
            contract = addon_contract(addon)
            if contract is None or contract.web_codegen is None:
                continue
            raw = contract.web_codegen
            if not isinstance(raw, dict) or not {"schema", "sdl", "documents"} <= set(raw):
                raise ImproperlyConfigured(
                    f"{addon.name} addon.toml [web].codegen must declare 'schema', 'sdl', and 'documents'"
                )
            schema = raw["schema"]
            if not isinstance(schema, str) or not schema.isidentifier():
                raise ImproperlyConfigured(
                    f"{addon.name} [web].codegen.schema must be a model-safe name"
                )
            package = contract.web
            if not isinstance(package, str):
                raise ImproperlyConfigured(
                    f"{addon.name} [web].codegen requires [web].package"
                )
            previous = seen.setdefault(schema, addon)
            if previous is not addon:
                raise ImproperlyConfigured(
                    f"Duplicate [web].codegen.schema {schema!r}: {previous.name} and {addon.name}"
                )
            entries.append(
                WebCodegen(
                    schema=schema,
                    package=package,
                    sdl=str(raw["sdl"]),
                    documents=str(raw["documents"]),
                    app=addon.name,
                    types=bool(raw.get("types", False)),
                )
            )
        return tuple(entries)

    def _document_roots(self) -> list[dict[str, str]]:
        """Return document roots consumed by the framework codegen CLI."""

        return [
            {
                "kind": "package",
                "package": package.package,
                "path": f"node_modules/{package.package}/{package.source_root}",
            }
            for package in (*self.core_packages, *self.addon_packages)
        ] + [{"kind": "host", "path": "src"}]

    def _web_package_source(self, package: WebPackage) -> str:
        """Return a Tailwind source path from ``runtime/web``."""

        return f"{self.web_root}/node_modules/{package.package}/{package.source_root}"
