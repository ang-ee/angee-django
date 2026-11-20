"""Web runtime manifest projected from the composed addon graph.

The optional web addon's build projector. It is deliberately offline and pure: it reads
native ``web/package.json`` documents for enabled apps and renders three files under ``runtime/web/`` —
``manifest.json`` (the package graph, each package's addon ancestry, codegen,
named asset and standalone page declarations, and the deployment's ``ANGEE_UI``
layer), ``tailwind.sources.css`` (the Tailwind ``@source`` include) and
``pages.caddy`` (the ingress routes of the standalone pages). It holds **no**
GraphQL-schema knowledge: which schemas exist, whether each is live, and the
shape of their operation documents are owned by the SDL on disk and the
``@angee/app`` shared generator. Vite dev/build awaits it; ``angee-web-codegen``
exposes the same generation for standalone checks. It generates
``runtime/web/app.ts`` so no schema-shaped TypeScript is authored in Python.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from graphlib import TopologicalSorter
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any, cast

from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.csp import CSP, build_policy
from django_compose.components import installed_components
from django_compose.serving import GENERATED_SENTINEL

if TYPE_CHECKING:
    from django.apps.registry import Apps
    from django_compose.declarations import ModelsView
    from django_compose.model_composition import ModelComposition
CORE_WEB_PACKAGES: tuple[str, ...] = ("@angee/app", "@angee/ui")
"""Framework packages whose rendered sources and documents every host consumes."""

WEB_PACKAGE_RE = re.compile(r"^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$")
DEFAULT_WEB_ROOT = "../../web"
UI_LAYER_KEYS = frozenset({"menus", "containers", "shell"})
"""Manifest keys the deployment's ``ANGEE_UI`` setting may declare; ``@angee/app`` validates their values."""

PAGE_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)+$")
"""A standalone page id: dotted lower-case segments. Page ``<id>`` is built to ``<id>.html`` at the web build root."""

PAGE_PATH_RE = re.compile(r"^/(?:[a-z0-9][a-z0-9._~-]*/)+\*$")
"""A path a standalone page answers: an absolute lower-case prefix of whole segments ending in ``/*``."""

PAGE_PREFIXES: tuple[str, ...] = ("/forms/*",)
"""The prefixes the ingress sends to Django under which an addon may declare a standalone page.

The web forms addon's page declares the whole of ``/forms/*``, the only page prefix
today, so a second addon's page anywhere under ``/forms/`` is refused as overlapping.
A second page needs its own prefix added here, which also adds it to the paths the
ingress sends to Django. The prefix is the same fact as the web forms addon's public
URL prefix (``angee.webforms.urls.PUBLIC_PREFIX``), which a test of that addon pins
against its page declaration.
"""

INGRESS_DJANGO_PATHS: tuple[str, ...] = (
    "/graphql/*", "/acp/*", "/auth/csrf", "/auth/csrf/", *PAGE_PREFIXES,
    "/storage/upload", "/storage/upload/*", "/storage/download", "/storage/download/*",
    "/admin/*", "/static/*", "/media/*", "/mcp", "/mcp/*",
)
"""Every path the stack's static ingress sends to Django: the one list, which its ``@django`` matcher is pinned to.

A standalone page's own requests (the JSON it fetches, what it submits) are not
page requests, so they must reach Django on the page's paths: a page path sits
under a prefix listed here. Only ``PAGE_PREFIXES`` are open to pages. Every other
entry is the framework's own endpoint, whose browser navigations a page would take
over, and a path this list does not hold (an app route, the build's ``/assets/``,
the operator's ``/operator/``) falls to the app and never reaches Django. The
manifest publishes the list, in this order, as its top-level ``ingressDjangoPaths``,
so a dev server's proxy can read it rather than keep a copy.
"""

PAGE_ACCEPT = "text/html"
"""The media type a standalone page declares as ``accept``; this version admits no other."""

PAGE_ACCEPT_PATTERN = r"^[\t\x20]*[Tt][Ee][Xx][Tt]/[Hh][Tt][Mm][Ll][\t\x20]*(?:;[^,]*)?(?:,|$)"
"""What a request's ``Accept`` header must match before a ``text/html`` standalone page answers it.

It matches only when ``text/html`` is the FIRST media type listed, in any case and
with any parameters. A browser navigation lists it first; ``fetch``, ``curl`` and
API clients send ``*/*`` or ``application/json`` and must reach Django on the same
path, so neither a wildcard nor a later ``text/html`` selects a page. It is tested
against the header's field lines joined in order with a comma, so a client that
lists another type first reaches Django however its header is folded. The ingress
snippet and the Vite dev server apply this one string, published as the manifest's
top-level ``pageAccept`` (each admitted ``accept`` media type to its pattern); it
therefore uses only syntax Go RE2, JavaScript and Python read alike without flags,
which is why each letter spells out both cases. Parameters are not read: a leading
``text/html;q=0`` still selects the page, which is known and accepted.
"""

PAGE_CSP: dict[str, list[str]] = {
    "default-src": [CSP.SELF],
    "script-src": [CSP.SELF],
    "object-src": [CSP.NONE],
    "base-uri": [CSP.NONE],
    "frame-ancestors": [CSP.NONE],
    "connect-src": [CSP.SELF],
    "img-src": [CSP.SELF, "data:"],
    "font-src": [CSP.SELF],
    "style-src": [CSP.SELF, CSP.UNSAFE_INLINE],
    "form-action": [CSP.NONE],
}
"""The content security policy of every standalone page, and its one owner.

A standalone page renders markup written by staff on the application's origin, so
it must not be able to run inline script, and it is never framed. The ingress sends
it as a response header because a ``<meta>`` policy cannot set ``frame-ancestors``.
``style-src`` admits inline styles, the one loosened directive, because the
rendering library sets them. The policy is the same for every page: none declares
its own. The manifest publishes the rendered policy as its top-level ``pageCsp``
string, the value the ingress header carries, so the Vite dev server, which has
no ingress in front of it, can apply the same policy in development.
"""


def addon_ancestors(
    app_configs: Iterable[AppConfig], dependencies: Mapping[str, Iterable[str]],
) -> dict[str, tuple[str, ...]]:
    """Project transitive ancestry from the compiler's already-resolved graph."""

    ancestors: dict[str, set[str]] = {}
    for name in TopologicalSorter(dependencies).static_order():
        ancestors[name] = {
            ancestor for dependency in dependencies.get(name, ())
            for ancestor in (dependency, *ancestors[dependency])
        }
    return {config.name: tuple(sorted(ancestors[config.name])) for config in app_configs}


class WebRuntime:
    """Project enabled addon packages into the web transport document and CSS."""

    def __init__(
        self,
        addons: Iterable[AppConfig],
        *,
        runtime_dir: Path | None = None,
        web_root: str = DEFAULT_WEB_ROOT,
        ui: Mapping[str, Any] | None = None,
        dependencies: Mapping[str, Iterable[str]] | None = None,
    ) -> None:
        addons = tuple(addons)
        self.runtime_dir = runtime_dir
        self.web_root = web_root
        graph = settings.COMPOSE_SNAPSHOT.dependencies if dependencies is None else dependencies
        ancestors = addon_ancestors(addons, graph)
        core_packages = [{"package": name, "sourceRoot": "src"} for name in CORE_WEB_PACKAGES]
        addon_packages: list[dict[str, Any]] = []
        codegen_entries: list[dict[str, Any]] = []
        packages_seen: dict[str, str] = {}
        schemas_seen: dict[str, str] = {}
        assets_seen: dict[str, str] = {}
        pages_seen: dict[str, str] = {}
        page_paths_seen: dict[str, str] = {}
        providers = installed_components("angee.addons").providers
        for addon in addons:
            if addon.name not in providers:
                continue
            document = self._package_document(addon)
            if document is None:
                continue
            package = document.get("name")
            if not isinstance(package, str) or not WEB_PACKAGE_RE.fullmatch(package):
                raise ImproperlyConfigured(f"{addon.name} web/package.json name must be a valid npm package name")
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
            assets = self._asset_entries(addon, web.get("assets", {}))
            for name in sorted(assets):
                if name in assets_seen:
                    raise ImproperlyConfigured(
                        f"Duplicate web/package.json angee.assets name {name!r}: "
                        f"{assets_seen[name]} and {addon.name}"
                    )
                assets_seen[name] = addon.name
            if assets:
                entry["assets"] = assets
            pages = self._page_entries(addon, web.get("pages", {}))
            for name, page in pages.items():
                if name in pages_seen:
                    raise ImproperlyConfigured(
                        f"Duplicate web/package.json angee.pages id {name!r}: {pages_seen[name]} and {addon.name}"
                    )
                pages_seen[name] = addon.name
                owner = f"{name!r} in {addon.name}"
                for path in page["paths"]:
                    for seen, seen_owner in page_paths_seen.items():
                        # Both end in "/*", so one prefix containing the other claims the same requests.
                        if path.startswith(seen.removesuffix("*")) or seen.startswith(path.removesuffix("*")):
                            raise ImproperlyConfigured(
                                f"Overlapping web/package.json angee.pages paths {seen!r} ({seen_owner}) "
                                f"and {path!r} ({owner})"
                            )
                    page_paths_seen[path] = owner
            if pages:
                entry["pages"] = pages
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
            "pageAccept": {PAGE_ACCEPT: PAGE_ACCEPT_PATTERN},
            "pageCsp": build_policy(PAGE_CSP),
            "ingressDjangoPaths": list(INGRESS_DJANGO_PATHS),
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
            Path("web/pages.caddy"): self.pages_caddy(),
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

    def pages_caddy(self) -> str:
        """Return the Caddy routes a site block imports ahead of its Django and app routes.

        Each standalone page answers ``GET`` and ``HEAD`` on its paths when the
        request's ``Accept`` header matches the manifest's ``pageAccept`` pattern,
        with the web build's ``/<id>.html`` under the importing site's ``root``;
        every other request falls through to the site's own routes. The page
        carries the manifest's ``pageCsp`` on those paths and at the built file's
        own URL, which the site's file server also answers. Without pages only
        the sentinel remains.
        """

        pages = {
            name: page for entry in self.manifest["addonPackages"] for name, page in entry.get("pages", {}).items()
        }
        lines = [GENERATED_SENTINEL]  # already a "#" comment, which is Caddy's own
        if pages:
            lines += [
                "#",
                "# Standalone pages: static entries of the web build, served ahead of the app and of Django.",
                "# A page answers only when text/html is the FIRST media type of the Accept header. A browser",
                "# navigation lists it first; fetch, curl and API clients send */* or application/json and must",
                "# reach Django on the same path, so neither a wildcard nor a later text/html selects a page.",
                "# The placeholder joins the header's field lines in order, so folding never changes the first type.",
                "# Only GET and HEAD read a file, and the answer varies by Accept for every cache.",
                "# A page renders staff-written markup on the application's origin, so its content security",
                "# policy runs no inline script and forbids framing, wherever the built file is requested.",
            ]
        policy = f'{CSP.HEADER_ENFORCE} "{self.manifest["pageCsp"]}"'
        for name in sorted(pages):
            matcher = f"@page_{name.replace('.', '_')}"
            accept = self.manifest["pageAccept"][pages[name]["accept"]]
            lines += [
                "",
                f"{matcher} {{",
                "\tmethod GET HEAD",
                f"\tpath {' '.join(sorted(pages[name]['paths']))}",
                # header_regexp would test each field line alone and admit a later line that leads with text/html.
                f"\texpression `{{http.request.header.Accept}}.matches(r'{accept}')`",
                "}",
                f"handle {matcher} {{",
                "\theader +Vary Accept",
                f"\theader {policy}",
                f"\trewrite * /{name}.html",
                "\tfile_server",
                "}",
                f"header /{name}.html {policy}",
            ]
        return "\n".join(lines) + "\n"

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
        """Read the native npm owner of fragment identity, assets, pages and codegen intent once."""

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
    def _package_path(where: str, value: object) -> str:
        """Admit one package-relative POSIX path: the rule named assets and page entries share."""

        # npm paths are POSIX; the Windows anchor still catches drive and UNC forms.
        if (
            not isinstance(value, str) or value != value.strip() or "\\" in value or PureWindowsPath(value).anchor
            or not (parts := PurePosixPath(value).parts) or ".." in parts
        ):
            raise ImproperlyConfigured(
                f"{where} must be a relative POSIX path to a file, without surrounding whitespace, "
                "backslashes or '..' segments"
            )
        return value

    @classmethod
    def _asset_entries(cls, addon: AppConfig, raw: object) -> dict[str, str]:
        """Admit named package-relative POSIX paths; the web generator checks the selected files."""

        where = f"{addon.name} web/package.json angee.assets"
        if not isinstance(raw, Mapping):
            raise ImproperlyConfigured(f"{where} must be an object")
        for name, value in sorted(raw.items()):
            if not name or name != name.strip():
                raise ImproperlyConfigured(f"{where} name {name!r} must be non-empty without surrounding whitespace")
            cls._package_path(f"{where}[{name!r}]", value)
        return dict(raw)

    @classmethod
    def _page_entries(cls, addon: AppConfig, raw: object) -> dict[str, dict[str, Any]]:
        """Admit standalone pages: static entries of the web build that load none of the app.

        A page declares exactly its ``entry`` (an existing file of the package), the
        ``paths`` it answers (each under one of ``PAGE_PREFIXES``) and the ``accept``
        media type whose requests select it.
        Only the declaration is projected: the entry's bytes are the web build's input.
        """

        where = f"{addon.name} web/package.json angee.pages"
        if not isinstance(raw, Mapping):
            raise ImproperlyConfigured(f"{where} must be an object")
        package_root = (Path(addon.path) / "web").resolve()
        pages: dict[str, dict[str, Any]] = {}
        for name, page in sorted(raw.items()):
            if not PAGE_ID_RE.fullmatch(name):
                raise ImproperlyConfigured(f"{where} id {name!r} must be a dotted lower-case name such as 'site.form'")
            here = f"{where}[{name!r}]"
            if not isinstance(page, Mapping) or page.keys() != {"entry", "paths", "accept"}:
                raise ImproperlyConfigured(f"{here} must declare exactly 'entry', 'paths' and 'accept'")
            entry = cls._package_path(f"{here}.entry", page["entry"])
            file = (package_root / entry).resolve()
            if not file.is_file() or not file.is_relative_to(package_root):
                raise ImproperlyConfigured(f"{here}.entry must name an existing file inside the package: {entry}")
            paths = page["paths"]
            if not isinstance(paths, list) or not paths or not all(
                isinstance(path, str) and PAGE_PATH_RE.fullmatch(path) for path in paths
            ):
                raise ImproperlyConfigured(
                    f"{here}.paths must be a non-empty list of absolute lower-case prefixes ending in '/*', "
                    "such as '/forms/*'"
                )
            for path in paths:
                if not any(path.startswith(prefix.removesuffix("*")) for prefix in PAGE_PREFIXES):
                    raise ImproperlyConfigured(
                        f"{here}.paths {path!r} must sit under a prefix the ingress sends to Django for pages: "
                        f"{', '.join(map(repr, PAGE_PREFIXES))}"
                    )
            if page["accept"] != PAGE_ACCEPT:
                raise ImproperlyConfigured(f"{here}.accept must be {PAGE_ACCEPT!r}")
            pages[name] = {"entry": entry, "paths": list(paths), "accept": PAGE_ACCEPT}
        return pages

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


class WebIntegration:
    """Publish package/codegen transport inputs during the native ready phase."""

    external_root = "web"
    phase = "ready"

    def render(self, composition: ModelComposition, models: ModelsView | Apps) -> Mapping[Path, bytes]:
        del composition, models
        return {
            path: source.encode('utf-8')
            for path, source in WebRuntime(
                settings.COMPOSE_CLOSURE.app_configs, runtime_dir=Path(settings.COMPOSE_OUTPUT_DIR),
                ui=settings.ANGEE_UI,
            ).render_sources().items()
        }
