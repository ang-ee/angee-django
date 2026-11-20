"""Capability owners resolve authoritative declarations and real Python defaults."""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured
from django.utils.csp import build_policy
from django_compose.serving import GENERATED_SENTINEL

from angee.base.addons import addon_contribution, addon_facts
from angee.graphql.schema import schema_parts_for
from angee.mcp.server import tool_registrar
from angee.web.build import INGRESS_DJANGO_PATHS, PAGE_ACCEPT_PATTERN, PAGE_CSP, PAGE_PREFIXES, WebRuntime
from tests.conftest import make_addon, write_addon_package

FORM_PAGE: dict[str, Any] = {"entry": "./src/form.html", "paths": ["/forms/w/*"], "accept": "text/html"}


def _page_site(root: Path, pages: object, *, name: str = "tests.site") -> AppConfig:
    """A fixture addon whose web package declares ``pages`` and ships the entry file they name."""

    package = f"@example/{name.rpartition('.')[2]}"
    config = make_addon(name=name, path=root, web={"package": package})
    (root / "web/package.json").write_text(json.dumps({"name": package, "angee": {"pages": pages}}))
    (root / "web/src").mkdir()
    (root / "web/src/form.html").write_text("<!doctype html>")
    return config


def test_conventional_schema_and_mcp_support_reexports_and_conditional_exports(tmp_path: Path) -> None:
    config = make_addon(path=tmp_path)
    (tmp_path / "implementation.py").write_text(
        'schemas = {"public": {}}\ndef register(server):\n    return None\n', encoding="utf-8"
    )
    (tmp_path / "schema.py").write_text("if True:\n    from .implementation import schemas\n", encoding="utf-8")
    (tmp_path / "mcp_tools.py").write_text("from .implementation import register\n", encoding="utf-8")
    assert "public" in schema_parts_for(config)
    assert tool_registrar(config) is importlib.import_module(f"{config.name}.implementation").register
    assert "graphql" not in addon_facts(config.name, "angee")
    assert addon_facts(config.name, "angee").get("mcp", {}) == {}


@pytest.mark.parametrize("qualified", [False, True])
def test_explicit_graphql_declaration_wins_over_convention(tmp_path: Path, qualified: bool) -> None:
    config = make_addon(path=tmp_path)
    (tmp_path / "selected.py").write_text(
        'schemas = {"chosen": {}}\ndef register(server):\n    return None\n', encoding="utf-8"
    )
    (tmp_path / "schema.py").write_text('raise AssertionError("unselected convention")\n', encoding="utf-8")
    write_addon_package(
        config,
        graphql={"schema": f"{config.name}.selected.schemas" if qualified else "selected.schemas"},
    )
    assert tuple(schema_parts_for(config)) == ("chosen",)


def test_missing_explicit_graphql_contribution_does_not_fall_back(tmp_path: Path) -> None:
    config = make_addon(path=tmp_path)
    (tmp_path / "schema.py").write_text('schemas = {"fallback": {}}\n', encoding="utf-8")
    write_addon_package(config, graphql={"schema": "missing.schemas"})
    with pytest.raises(ImproperlyConfigured, match="references"):
        schema_parts_for(config)


@pytest.mark.parametrize("module, owner", [("schema", schema_parts_for), ("mcp_tools", tool_registrar)])
def test_broken_present_module_preserves_internal_import_error(tmp_path: Path, module: str, owner) -> None:
    config = make_addon(path=tmp_path)
    (tmp_path / f"{module}.py").write_text("import angee_missing_test_dependency\n", encoding="utf-8")
    with pytest.raises(ImproperlyConfigured, match="failed to import") as failure:
        owner(config)
    assert isinstance(failure.value.__cause__, ModuleNotFoundError)
    assert failure.value.__cause__.name == "angee_missing_test_dependency"


def test_web_codegen_reads_native_package_metadata(tmp_path: Path) -> None:
    config = make_addon(
        path=tmp_path,
        web={
            "package": "@example/operator",
            "codegen": {"schema": "operator", "sdl": "schema.graphql", "documents": "documents.ts"},
        },
    )
    projection = WebRuntime((config,), dependencies={config.name: ()})
    assert projection.manifest["codegen"][0]["package"] == "@example/operator"
    assert projection.manifest["codegen"][0]["types"] is False
    assert "web" not in addon_facts(config.name, "angee")


def test_web_assets_project_only_sorted_declarations_on_the_package(tmp_path: Path) -> None:
    config = make_addon(path=tmp_path, web={"package": "@example/site"})
    assets = {"site.preview": "./art/preview.png", "site.icon": "./art/icon.svg"}
    (tmp_path / "web/package.json").write_text(json.dumps({"name": "@example/site", "angee": {"assets": assets}}))
    projection = WebRuntime((config,), runtime_dir=tmp_path / "runtime", dependencies={config.name: ()})
    entry = projection.manifest["addonPackages"][0]
    # The entry's package root resolves the files; compose publishes declarations, never paths or bytes.
    assert entry["root"] == "../../web"
    assert entry["assets"] == assets
    manifest = projection.manifest_json()
    assert manifest.index('"site.icon"') < manifest.index('"site.preview"')
    assert str(tmp_path) not in manifest


def test_web_assets_omit_an_empty_declaration(tmp_path: Path) -> None:
    config = make_addon(path=tmp_path, web={"package": "@example/site"})
    (tmp_path / "web/package.json").write_text(json.dumps({"name": "@example/site", "angee": {"assets": {}}}))
    assert "assets" not in WebRuntime((config,), dependencies={config.name: ()}).manifest["addonPackages"][0]


@pytest.mark.parametrize(
    "assets",
    [None, [], "icon.svg", {"": "icon.svg"}, {" ": "icon.svg"}, {" icon": "icon.svg"}, {"icon": ""},
     {"icon": " "}, {"icon": " ./icon.svg"}, {"icon": 1}, {"icon": "."}, {"icon": "./"}, {"icon": "/icon.svg"},
     {"icon": "../icon.svg"}, {"icon": "art/../icon.svg"}, {"icon": "art\\icon.svg"},
     {"icon": "C:\\art\\icon.svg"}, {"icon": "C:/art/icon.svg"}, {"icon": "//server/share/icon.svg"},
     {"icon": "..\\icon.svg"}],
)
def test_web_assets_refuse_malformed_declarations(tmp_path: Path, assets: object) -> None:
    config = make_addon(path=tmp_path, web={"package": "@example/site"})
    (tmp_path / "web/package.json").write_text(json.dumps({"name": "@example/site", "angee": {"assets": assets}}))
    with pytest.raises(ImproperlyConfigured, match="web/package.json angee.assets"):
        WebRuntime((config,), dependencies={config.name: ()})


def test_web_assets_refuse_names_shared_by_addons(tmp_path: Path) -> None:
    configs = [make_addon(name=f"tests.{name}", path=tmp_path / name, web={"package": f"@example/{name}"})
               for name in ("first", "second")]
    for config in configs:
        (Path(config.path) / "web/package.json").write_text(json.dumps({
            "name": f"@example/{config.label}", "angee": {"assets": {"site.icon": "./icon.svg"}},
        }))
    with pytest.raises(
        ImproperlyConfigured,
        match=r"Duplicate web/package.json angee.assets name 'site.icon': tests.first and tests.second",
    ):
        WebRuntime(configs, dependencies={config.name: () for config in configs})


def test_web_pages_project_declarations_on_the_package_and_the_accept_rule_once(tmp_path: Path) -> None:
    config = _page_site(tmp_path, {"site.form": FORM_PAGE})
    projection = WebRuntime((config,), runtime_dir=tmp_path / "runtime", dependencies={config.name: ()})
    # Like assets: the entry's package root resolves the file; compose publishes the declaration, never a path.
    assert projection.manifest["addonPackages"][0]["pages"] == {"site.form": FORM_PAGE}
    assert projection.manifest["pageAccept"] == {"text/html": PAGE_ACCEPT_PATTERN}
    assert str(tmp_path) not in projection.manifest_json()


@pytest.mark.parametrize("declared", [{}, {"pages": {}}], ids=["undeclared", "empty"])
def test_a_package_without_pages_emits_no_pages_key_and_an_importable_snippet(
    tmp_path: Path, declared: dict[str, Any],
) -> None:
    config = make_addon(path=tmp_path, web={"package": "@example/site"})
    (tmp_path / "web/package.json").write_text(json.dumps({"name": "@example/site", "angee": declared}))
    projection = WebRuntime((config,), dependencies={config.name: ()})
    assert "pages" not in projection.manifest["addonPackages"][0]
    # The page rules are the composer's own, so the manifest carries them whether or not a page is declared.
    assert projection.manifest["pageAccept"] == {"text/html": PAGE_ACCEPT_PATTERN}
    assert projection.manifest["pageCsp"] == build_policy(PAGE_CSP)
    # The ingress imports the file unconditionally, so it holds its sentinel comment and no route.
    assert projection.pages_caddy() == f"# {GENERATED_SENTINEL}\n"
    assert Path("web/pages.caddy") in projection.render_sources()


@pytest.mark.parametrize(
    "pages",
    [
        pytest.param(None, id="not-an-object"),
        pytest.param([], id="a-list"),
        pytest.param({"": FORM_PAGE}, id="id-empty"),
        pytest.param({"form": FORM_PAGE}, id="id-undotted"),
        pytest.param({"Site.form": FORM_PAGE}, id="id-upper-case"),
        pytest.param({"site..form": FORM_PAGE}, id="id-empty-segment"),
        pytest.param({"site.form\n": FORM_PAGE}, id="id-trailing-newline"),
        pytest.param({"site.form": "./src/form.html"}, id="page-not-an-object"),
        pytest.param({"site.form": {**FORM_PAGE, "title": "Form"}}, id="unknown-key"),
        pytest.param({"site.form": {"entry": "./src/form.html", "paths": ["/forms/w/*"]}}, id="accept-missing"),
        pytest.param({"site.form": {**FORM_PAGE, "entry": 1}}, id="entry-not-a-string"),
        pytest.param({"site.form": {**FORM_PAGE, "entry": "/src/form.html"}}, id="entry-absolute"),
        pytest.param({"site.form": {**FORM_PAGE, "entry": "src/../../form.html"}}, id="entry-leaves-the-package"),
        pytest.param({"site.form": {**FORM_PAGE, "entry": "./src/missing.html"}}, id="entry-missing"),
        pytest.param({"site.form": {**FORM_PAGE, "entry": "./src"}}, id="entry-a-directory"),
        pytest.param({"site.form": {**FORM_PAGE, "paths": "/forms/w/*"}}, id="paths-not-a-list"),
        pytest.param({"site.form": {**FORM_PAGE, "paths": []}}, id="paths-empty"),
        pytest.param({"site.form": {**FORM_PAGE, "paths": [7]}}, id="path-not-a-string"),
        pytest.param({"site.form": {**FORM_PAGE, "paths": ["forms/w/*"]}}, id="path-relative"),
        pytest.param({"site.form": {**FORM_PAGE, "paths": ["/forms/w"]}}, id="path-exact"),
        pytest.param({"site.form": {**FORM_PAGE, "paths": ["/forms/w/"]}}, id="path-without-wildcard"),
        pytest.param({"site.form": {**FORM_PAGE, "paths": ["/forms/w*"]}}, id="path-partial-segment"),
        pytest.param({"site.form": {**FORM_PAGE, "paths": ["/forms/*/w/*"]}}, id="path-inner-wildcard"),
        pytest.param({"site.form": {**FORM_PAGE, "paths": ["/*"]}}, id="path-whole-site"),
        pytest.param({"site.form": {**FORM_PAGE, "paths": ["/Forms/w/*"]}}, id="path-upper-case"),
        pytest.param({"site.form": {**FORM_PAGE, "paths": ["/forms//w/*"]}}, id="path-empty-segment"),
        pytest.param({"site.form": {**FORM_PAGE, "paths": ["/forms/../*"]}}, id="path-dot-segment"),
        pytest.param({"site.form": {**FORM_PAGE, "paths": ["/forms/w/* /admin/*"]}}, id="path-two-in-one"),
        pytest.param({"site.form": {**FORM_PAGE, "paths": ["/forms/w/*\n"]}}, id="path-trailing-newline"),
        pytest.param({"site.form": {**FORM_PAGE, "accept": "*/*"}}, id="accept-wildcard"),
        pytest.param({"site.form": {**FORM_PAGE, "accept": "TEXT/HTML"}}, id="accept-other-case"),
        pytest.param({"site.form": {**FORM_PAGE, "accept": ["text/html"]}}, id="accept-a-list"),
    ],
)
def test_web_pages_refuse_malformed_declarations(tmp_path: Path, pages: object) -> None:
    config = _page_site(tmp_path, pages)
    with pytest.raises(ImproperlyConfigured, match=r"tests\.site web/package\.json angee\.pages"):
        WebRuntime((config,), dependencies={config.name: ()})


def test_web_pages_refuse_an_entry_that_resolves_outside_the_package(tmp_path: Path) -> None:
    config = _page_site(tmp_path / "site", {"site.form": FORM_PAGE})
    outside = tmp_path / "outside.html"
    outside.write_text("<!doctype html>")
    entry = tmp_path / "site/web/src/form.html"
    entry.unlink()
    entry.symlink_to(outside)
    with pytest.raises(
        ImproperlyConfigured,
        match=r"angee\.pages\['site\.form'\]\.entry must name an existing file inside the package",
    ):
        WebRuntime((config,), dependencies={config.name: ()})


def test_web_pages_refuse_ids_shared_by_addons(tmp_path: Path) -> None:
    configs = [
        _page_site(tmp_path / name, {"site.form": {**FORM_PAGE, "paths": [f"/forms/{name}/*"]}}, name=f"tests.{name}")
        for name in ("first", "second")
    ]
    with pytest.raises(
        ImproperlyConfigured,
        match=r"Duplicate web/package.json angee.pages id 'site.form': tests.first and tests.second",
    ):
        WebRuntime(configs, dependencies={config.name: () for config in configs})


@pytest.mark.parametrize(
    ("packages", "claims"),
    [
        pytest.param(
            [{"site.form": ["/forms/*", "/forms/w/*"]}],
            "'/forms/*' ('site.form' in tests.first) and '/forms/w/*' ('site.form' in tests.first)",
            id="within-a-page",
        ),
        pytest.param(
            [{"site.form": ["/forms/w/*"], "site.list": ["/forms/*"]}],
            "'/forms/w/*' ('site.form' in tests.first) and '/forms/*' ('site.list' in tests.first)",
            id="across-pages",
        ),
        pytest.param(
            [{"first.form": ["/forms/w/*"]}, {"second.form": ["/forms/w/draft/*"]}],
            "'/forms/w/*' ('first.form' in tests.first) and '/forms/w/draft/*' ('second.form' in tests.second)",
            id="across-packages",
        ),
        pytest.param(
            [{"first.form": ["/forms/w/*"]}, {"second.form": ["/forms/w/*"]}],
            "'/forms/w/*' ('first.form' in tests.first) and '/forms/w/*' ('second.form' in tests.second)",
            id="the-same-prefix",
        ),
    ],
)
def test_web_pages_refuse_overlapping_paths(tmp_path: Path, packages: list[dict[str, list[str]]], claims: str) -> None:
    configs = [
        _page_site(
            tmp_path / name, {page: {**FORM_PAGE, "paths": paths} for page, paths in pages.items()},
            name=f"tests.{name}",
        )
        for name, pages in zip(("first", "second"), packages, strict=False)
    ]
    with pytest.raises(ImproperlyConfigured) as refusal:
        WebRuntime(configs, dependencies={config.name: () for config in configs})
    assert str(refusal.value) == f"Overlapping web/package.json angee.pages paths {claims}"


@pytest.mark.parametrize("path", ["/forms/*", "/forms/w/*"])
def test_web_pages_admit_paths_under_a_prefix_the_ingress_sends_to_django_for_pages(tmp_path: Path, path: str) -> None:
    config = _page_site(tmp_path, {"site.form": {**FORM_PAGE, "paths": [path]}})
    entry = WebRuntime((config,), dependencies={config.name: ()}).manifest["addonPackages"][0]
    assert entry["pages"]["site.form"]["paths"] == [path]


@pytest.mark.parametrize(
    "path",
    [
        # Unknown to the ingress: the page's own JSON request would fall to the app and receive HTML.
        pytest.param("/surveys/*", id="unrouted"),
        pytest.param("/assets/*", id="build-assets"),
        pytest.param("/operator/*", id="operator"),
        # Sent to Django, yet the framework's own: a page there would take over its browser navigations.
        *(
            pytest.param(owned, id=owned.strip("/*"))
            for owned in INGRESS_DJANGO_PATHS
            if owned.endswith("/*") and owned not in PAGE_PREFIXES
        ),
        pytest.param("/admin/login/*", id="beneath-a-framework-prefix"),
    ],
)
def test_web_pages_refuse_paths_outside_the_prefixes_open_to_pages(tmp_path: Path, path: str) -> None:
    config = _page_site(tmp_path, {"site.form": {**FORM_PAGE, "paths": ["/forms/w/*", path]}})
    with pytest.raises(ImproperlyConfigured) as refusal:
        WebRuntime((config,), dependencies={config.name: ()})
    assert str(refusal.value) == (
        f"tests.site web/package.json angee.pages['site.form'].paths {path!r} "
        "must sit under a prefix the ingress sends to Django for pages: '/forms/*'"
    )


def test_web_pages_admit_prefixes_that_share_only_leading_segments(tmp_path: Path) -> None:
    pages = {
        "site.form": {**FORM_PAGE, "paths": ["/forms/w/*", "/forms/wide/*"]},
        "site.list": {**FORM_PAGE, "paths": ["/forms/x/*", "/forms/2024-q1_draft.v2~a/*"]},
    }
    config = _page_site(tmp_path, pages)
    assert WebRuntime((config,), dependencies={config.name: ()}).manifest["addonPackages"][0]["pages"] == pages


@pytest.mark.parametrize(
    ("accept", "selected"),
    [
        pytest.param("text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", True, id="browser-navigation"),
        pytest.param("text/html", True, id="text-html-alone"),
        pytest.param("TEXT/HTML; charset=UTF-8, */*", True, id="any-case-with-parameters"),
        pytest.param(" text/html ;q=0.9", True, id="optional-whitespace"),
        pytest.param("application/json", False, id="json"),
        pytest.param("*/*", False, id="wildcard"),
        pytest.param("text/*", False, id="type-wildcard"),
        pytest.param("application/json, text/html", False, id="text-html-not-first"),
        pytest.param("application/xhtml+xml,text/html", False, id="xhtml-first"),
        # The ingress joins a header's field lines in order with a comma before it applies the pattern.
        pytest.param("application/json,text/html", False, id="two-lines-json-first"),
        pytest.param("text/html,application/json", True, id="two-lines-text-html-first"),
        pytest.param("text/html5", False, id="longer-subtype"),
        pytest.param("", False, id="absent"),
    ],
)
def test_the_page_accept_pattern_selects_only_a_leading_text_html(accept: str, selected: bool) -> None:
    assert bool(re.search(PAGE_ACCEPT_PATTERN, accept)) is selected


def test_the_page_ingress_snippet_routes_each_page_by_method_path_and_leading_accept(tmp_path: Path) -> None:
    config = _page_site(tmp_path, {
        "site.list": {**FORM_PAGE, "paths": ["/forms/lists/*"]},
        "site.form": {**FORM_PAGE, "paths": ["/forms/w/*", "/forms/draft/*"]},
    })
    projection = WebRuntime((config,), dependencies={config.name: ()})
    preamble, _, routes = projection.pages_caddy().partition("\n\n")
    assert preamble.startswith(f"# {GENERATED_SENTINEL}\n")
    assert all(line.startswith("#") for line in preamble.splitlines())
    accept = projection.manifest["pageAccept"]["text/html"]
    # One fixed policy for every page: no inline script, never framed; inline styles are the one loosened directive.
    # The manifest publishes it for the dev server, and the ingress header carries that same string.
    assert projection.manifest["pageCsp"] == (
        "default-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'; "
        "frame-ancestors 'none'; connect-src 'self'; img-src 'self' data:; font-src 'self'; "
        "style-src 'self' 'unsafe-inline'; form-action 'none'"
    )
    policy = f'Content-Security-Policy "{projection.manifest["pageCsp"]}"'
    # Pages and their paths are sorted; page <id> is the web build's /<id>.html under the importing site's root.
    # The Accept condition reads the header's joined field lines, never one line alone.
    assert routes == (
        "@page_site_form {\n"
        "\tmethod GET HEAD\n"
        "\tpath /forms/draft/* /forms/w/*\n"
        f"\texpression `{{http.request.header.Accept}}.matches(r'{accept}')`\n"
        "}\n"
        "handle @page_site_form {\n"
        "\theader +Vary Accept\n"
        f"\theader {policy}\n"
        "\trewrite * /site.form.html\n"
        "\tfile_server\n"
        "}\n"
        f"header /site.form.html {policy}\n"
        "\n"
        "@page_site_list {\n"
        "\tmethod GET HEAD\n"
        "\tpath /forms/lists/*\n"
        f"\texpression `{{http.request.header.Accept}}.matches(r'{accept}')`\n"
        "}\n"
        "handle @page_site_list {\n"
        "\theader +Vary Accept\n"
        f"\theader {policy}\n"
        "\trewrite * /site.list.html\n"
        "\tfile_server\n"
        "}\n"
        f"header /site.list.html {policy}\n"
    )


def test_broken_native_web_package_fails_before_projection(tmp_path: Path) -> None:
    config = make_addon(path=tmp_path, web={"package": "@example/selected"})
    (tmp_path / "web/package.json").write_text("broken json", encoding="utf-8")
    with pytest.raises(ImproperlyConfigured, match="cannot read"):
        WebRuntime((config,), dependencies={config.name: ()})


@pytest.mark.parametrize("key, value", [("types", "false"), ("types", 1), ("sdl", 7), ("documents", False)])
def test_web_codegen_rejects_coercible_but_invalid_types(tmp_path: Path, key: str, value: object) -> None:
    raw = {"schema": "operator", "sdl": "schema.graphql", "documents": "documents.ts", key: value}
    config = make_addon(path=tmp_path, web={"package": "@example/operator", "codegen": raw})
    with pytest.raises(ImproperlyConfigured, match=f"codegen.{key} must be"):
        WebRuntime((config,), dependencies={config.name: ()})


@pytest.mark.parametrize(("capability", "explicit"), [("schema", False), ("schema", True), ("mcp", False)])
def test_present_null_export_is_invalid(tmp_path: Path, capability: str, explicit: bool) -> None:
    config = make_addon(path=tmp_path)
    module, attr = ("schema", "schemas") if capability == "schema" else ("mcp_tools", "register")
    (tmp_path / f"{module}.py").write_text(f"{attr} = None\n", encoding="utf-8")
    if explicit and capability == "schema":
        write_addon_package(config, graphql={"schema": "schema.schemas"})
    with pytest.raises(ImproperlyConfigured, match="must"):
        (schema_parts_for if capability == "schema" else tool_registrar)(config)


@pytest.mark.parametrize("reference", [None, 3, [], {}])
def test_explicit_graphql_reference_rejects_non_string_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reference: object,
) -> None:
    config = make_addon(path=tmp_path)
    # TOML cannot represent null: inject the projected capability value instead.
    monkeypatch.setattr("angee.graphql.schema.addon_facts", lambda *args, **kwargs: {"schema": reference})
    with pytest.raises(ImproperlyConfigured, match="graphql.schema must be a dotted reference"):
        schema_parts_for(config)


def test_contributions_preserve_mapping_values_and_defer_registrar_invocation():
    module = ModuleType("contributor")
    calls = []

    def register(server):
        calls.append(server)

    module.register = register
    module.schemas = {"public": {"query": [object()]}}
    assert addon_contribution(module, "register", kind="callable") is register
    assert calls == []
    assert addon_contribution(module, "schemas", kind="mapping", allow_callable=False) is module.schemas
    module.protocols = lambda: {"websocket": register}
    assert addon_contribution(module, "protocols", kind="mapping", allow_callable="required") == {
        "websocket": register,
    }
    assert addon_contribution(module, "missing") == []
    assert addon_contribution(module, "missing", kind="mapping") == {}
    assert addon_contribution(module, "missing", kind="callable") is None


@pytest.mark.parametrize("value", [None, [], "websocket", {"websocket"}])
def test_mapping_factory_contributions_reject_invalid_results(value):
    module = ModuleType("contributor")
    module.protocols = lambda: value
    with pytest.raises(ImproperlyConfigured, match="must resolve to a mapping"):
        addon_contribution(module, "protocols", kind="mapping", allow_callable="required")
