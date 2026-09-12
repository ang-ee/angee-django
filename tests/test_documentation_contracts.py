"""Check live links and executable recipes instead of fixed prose wording."""

import json
import re
import shlex
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = sorted(
    {
        *(ROOT / name for name in ("AGENTS.md", "README.md", "CONTRIBUTING.md")),
        *(ROOT / "docs").rglob("*.md"),
        *(ROOT / ".agents").rglob("*.md"),
        *(ROOT / "packages").glob("*/README.md"),
        ROOT / "examples/e2e/README.md",
    }
)


def prose(text: str) -> str:
    return re.sub(r"^```[^\n]*\n.*?^```[ \t]*$", "", text, flags=re.MULTILINE | re.DOTALL)


def anchors(text: str) -> set[str]:
    result = set()
    counts: dict[str, int] = {}
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", prose(text), re.MULTILINE):
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        result.add(f"{slug}-{count}" if count else slug)
    result.update(re.findall(r'(?:id|name)=[\'"]([^\'"]+)[\'"]', text))
    return result


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda path: str(path.relative_to(ROOT)))
def test_local_documentation_links_and_anchors_resolve(document: Path) -> None:
    destinations = [
        child.attrGet("href")
        for token in MarkdownIt().parse(document.read_text())
        for child in token.children or ()
        if child.type == "link_open"
    ]
    for destination in destinations:
        assert destination is not None
        link = urlsplit(destination.strip("<>"))
        # Website-root routes belong to the external documentation site.
        if link.scheme or link.netloc or link.path.startswith("/"):
            continue
        target = (document.parent / unquote(link.path)).resolve() if link.path else document
        assert target.exists(), f"{document.relative_to(ROOT)} -> {destination}"
        if link.fragment and target.suffix == ".md":
            assert unquote(link.fragment) in anchors(target.read_text()), (
                f"{document.relative_to(ROOT)} -> {destination}"
            )


def test_documented_pnpm_recipes_use_existing_scripts_and_safe_slot_invocation() -> None:
    guide = (ROOT / "docs/checks.md").read_text()
    packages = {
        json.loads(path.read_text())["name"]: json.loads(path.read_text())
        for path in [*(ROOT / "packages").glob("*/package.json"), ROOT / "examples/e2e/package.json"]
    }
    web = ROOT / "templates/projects/web/template/{{ web_path }}/package.json.jinja"
    # The script object is literal JSON; Jinja owns other parts of this template.
    host_scripts, _ = json.JSONDecoder().raw_decode(web.read_text().split('"scripts":', 1)[1].lstrip())
    snippets = re.findall(r"`([^`\n]*\bpnpm [^`\n]*)`", prose(guide))
    snippets += [line for block in re.findall(r"```sh\n(.*?)\n```", guide, re.DOTALL) for line in block.splitlines()]
    checked = set()
    for snippet in snippets:
        argv = shlex.split(snippet)
        if "pnpm" not in argv or "run" not in argv:
            continue
        if "exec" in argv and argv.index("exec") < argv.index("run"):
            continue  # e.g. `exec vitest run` invokes a binary, not a package script.
        script = argv[argv.index("run") + 1]
        if script.startswith("<"):
            continue
        assert "--config.verify-deps-before-run=false" in argv, snippet
        if "--filter" in argv:
            assert "--fail-if-no-match" in argv, snippet
            filters = [argv[index + 1] for index, arg in enumerate(argv) if arg == "--filter"]
            selected = [packages[name] for name in filters if name in packages]
            if "./packages/**" in filters:
                selected = [record for name, record in packages.items() if name.startswith("@angee/")]
            selected = [record for record in selected if "!" + record["name"] not in filters]
            assert selected, snippet
            assert any(script in record.get("scripts", {}) for record in selected), snippet
        elif "--dir" in argv:
            assert argv[argv.index("--dir") + 1] == "web"
            assert script in host_scripts
        checked.add(script)
    assert {"typecheck", "test", "build", "codegen", "test:e2e"} <= checked


def test_documented_verification_files_exist() -> None:
    guide = (ROOT / "docs/checks.md").read_text()
    paths = re.findall(r"(?<![\w/])(?:tests/|packages/|\.agents/)[\w./-]+\.(?:py|mjs|ts)\b", guide)
    assert paths
    for relative in paths:
        assert (ROOT / relative).is_file(), relative
