"""Check live links and executable recipes instead of fixed prose wording."""

import json
import re
import shlex
from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = sorted(
    {
        *(ROOT / name for name in ("AGENTS.md", "README.md", "CONTRIBUTING.md")),
        *(ROOT / "docs").rglob("*.md"),
        *(ROOT / ".agents").rglob("*.md"),
        *(ROOT / "src/angee").glob("*/skills/**/*.md"),
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


def test_local_documentation_links_and_anchors_resolve() -> None:
    broken = []
    for document in DOCUMENTS:
        destinations = [
            child.attrGet("href")
            for token in MarkdownIt().parse(document.read_text())
            for child in token.children or ()
            if child.type == "link_open"
        ]
        for destination in destinations:
            assert isinstance(destination, str)
            link = urlsplit(destination.strip("<>"))
            # Website-root routes belong to the external documentation site.
            if link.scheme or link.netloc or link.path.startswith("/"):
                continue
            target = (document.parent / unquote(link.path)).resolve() if link.path else document
            if not target.exists() or (
                link.fragment and target.suffix == ".md" and unquote(link.fragment) not in anchors(target.read_text())
            ):
                broken.append(f"{document.relative_to(ROOT)} -> {destination}")
    assert not broken, "\n".join(broken)


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
    executed = set()
    for snippet in snippets:
        argv = shlex.split(snippet)
        if "pnpm" not in argv:
            continue
        if "exec" in argv:
            binary = argv[argv.index("exec") + 1]
            if binary == "angee-web-codegen":
                assert binary in packages["@angee/app"]["bin"]
                assert "--config.verify-deps-before-run=false" in argv, snippet
                executed.add(binary)
        if "run" not in argv:
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
    assert {"typecheck", "test", "build", "test:e2e"} <= checked
    assert "angee-web-codegen" in executed


def test_documented_verification_files_exist() -> None:
    guide = (ROOT / "docs/checks.md").read_text()
    paths = re.findall(r"(?<![\w/])(?:tests/|packages/|\.agents/)[\w./-]+\.(?:py|mjs|ts)\b", guide)
    assert paths
    for relative in paths:
        assert (ROOT / relative).is_file(), relative
