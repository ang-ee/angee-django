"""The shipped message template library: the base layout against the mail checklist, the blocks and the parts."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
import yaml
from django.apps import apps
from rebac import system_context

from angee.brandkit.testing.models import BrandKit
from angee.messaging_templates.models import MessageTemplateKind
from angee.messaging_templates.testing.models import MessageTemplate
from angee.messaging_templates.walker import TemplateWarningCode
from angee.resources.testing.models import Resource

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT / "src/angee/messaging_templates/resources/install"
SHIPPED_KIT = ROOT / "src/angee/brandkit/resources/install/010_brandkit.brandkit.yaml"
FILES = sorted(RESOURCES.glob("*.yaml"))


def _shipped(kind: str) -> list[dict[str, Any]]:
    return [row for path in FILES for row in yaml.safe_load(path.read_text()) if row["kind"] == kind]


LAYOUT = next(row for row in _shipped("layout") if row["key"] == "base")


@pytest.fixture
def library(db: None) -> dict[str, Any]:
    """The install tier loaded as a host loads it, with the shipped Angee brand kit."""

    del db
    Resource.objects.load_addons((apps.get_app_config("brandkit"),), tiers=[Resource.Tier.INSTALL])
    Resource.objects.load_addons((apps.get_app_config("messaging_templates"),), tiers=[Resource.Tier.INSTALL])
    with system_context(reason="tests.messaging_templates.layout"):
        return {
            "kit": BrandKit.objects.default(),
            "rows": {(row.kind, row.key, row.language): row for row in MessageTemplate.objects.all()},
        }


class _Document(HTMLParser):
    """Every element, its attributes and its depth below <body>, the <style> text, and the end tags in order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[tuple[str, dict[str, str | None], int]] = []
        self.tags: list[str] = []
        self.styles: list[str] = []
        self.comments: list[str] = []
        self.decls: list[str] = []
        self.stack: list[str] = []
        self.in_style = False
        self.title = ""
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        depth = len(self.stack) - self.stack.index("body") if "body" in self.stack else -1
        self.elements.append((tag, dict(attrs), depth))
        self.tags.append(tag)
        if tag not in ("meta", "br", "img", "hr", "link"):
            self.stack.append(tag)
        self.in_style, self.in_title = tag == "style", tag == "title"

    def handle_endtag(self, tag: str) -> None:
        self.tags.append("/" + tag)
        if tag in self.stack:
            del self.stack[len(self.stack) - 1 - self.stack[::-1].index(tag):]
        self.in_style = self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_style:
            self.styles.append(data)
        if self.in_title:
            self.title += data

    def handle_comment(self, data: str) -> None:
        self.comments.append(data)

    def handle_decl(self, decl: str) -> None:
        self.decls.append(decl)


def _document(html: str) -> _Document:
    document = _Document()
    document.feed(html)
    document.close()
    return document


def test_the_install_tier_loads_every_shipped_row_as_a_baseline_and_a_reload_changes_nothing(library: Any) -> None:
    shipped = {(row["kind"], row["key"], row["language"]) for path in FILES for row in yaml.safe_load(path.read_text())}
    assert set(library["rows"]) == shipped
    assert all(row.is_baseline for row in library["rows"].values())
    result = Resource.objects.load_addons((apps.get_app_config("messaging_templates"),), tiers=[Resource.Tier.INSTALL])
    assert (result.created, result.updated) == (0, 0)


def test_every_shipped_row_renders_its_sample(library: Any) -> None:
    with system_context(reason="tests.messaging_templates.layout"):
        for row in library["rows"].values():
            preview = row.preview(kit=library["kit"])
            assert preview.rendered is not None, (row.kind, row.key, row.language, preview.warnings)
            for form in (preview.rendered.html, preview.rendered.plain_text):
                assert not re.search(r"\{\{|\{%|\{#", form), (row.key, form)
            codes = {warning.code for warning in preview.warnings}
            assert codes <= {TemplateWarningCode.PREHEADER}, (row.key, preview.warnings)


def test_the_base_layout_meets_the_mail_checklist(library: Any) -> None:
    with system_context(reason="tests.messaging_templates.layout"):
        preview = library["rows"][("layout", "base", "en")].preview(kit=library["kit"])
    assert preview.rendered is not None
    html = preview.rendered.html
    document = _document(html)
    elements = document.elements
    by_tag = lambda tag: [attrs for name, attrs, _ in elements if name == tag]  # noqa: E731

    # 1. a doctype, one html, head and body; Outlook's conditional comments, each [if mso], their markup balanced.
    assert [decl.lower() for decl in document.decls] == ["doctype html"]
    assert [len(by_tag(tag)) for tag in ("html", "head", "body")] == [1, 1, 1]
    outlook = [re.fullmatch(r"\[if mso\]>(.*)<!\[endif\]", comment, re.DOTALL) for comment in document.comments]
    assert outlook and all(outlook), document.comments
    word = _document("".join(match.group(1) for match in outlook if match))
    opened: list[str] = []
    for tag in word.tags:
        if tag.startswith("/"):
            assert opened.pop() == tag[1:], word.tags
        else:
            opened.append(tag)
    assert opened == []
    # 2. the Office namespaces on <html>, and the DPI setting Word reads: PixelsPerInch 96.
    assert {"xmlns:o", "xmlns:v"} <= set(by_tag("html")[0])
    assert word.tags[:3] == ["xml", "o:officedocumentsettings", "o:pixelsperinch"]
    assert "<o:PixelsPerInch>96</o:PixelsPerInch>" in LAYOUT["source"]
    # 3. one column: the layout table inside a ghost table Word lays out 600 wide, since Word ignores max-width.
    assert [attrs for name, attrs, _ in word.elements if name == "table"] == [
        {"role": "presentation", "width": "600", "align": "center", "cellpadding": "0", "cellspacing": "0"},
    ]
    assert len(by_tag("table")) == 1
    # 4. nothing a mail may not carry.
    assert not {"script", "iframe", "form", "object", "embed", "link"} & {name for name, _, _ in elements}
    assert not any((value or "").startswith("data:") for _, attrs, _ in elements for value in attrs.values())
    # 5. lang and dir on <html> and on every direct child of <body>; xml:lang on <body>.
    assert by_tag("html")[0]["lang"] == "en" and by_tag("html")[0]["dir"] == "ltr"
    assert by_tag("body")[0]["xml:lang"] == "en"
    children = [attrs for name, attrs, depth in elements if depth == 1 and name != "body"]
    assert children and all(attrs.get("lang") == "en" and attrs.get("dir") == "ltr" for attrs in children)
    # 6. the wrapper is an article named by the subject; 7. so is the title.
    article = next(attrs for attrs in by_tag("div") if attrs.get("role") == "article")
    assert article["aria-roledescription"] == "email"
    assert article["aria-label"] == document.title == preview.rendered.subject
    # 8. every layout table is presentation.
    assert all(attrs.get("role") == "presentation" for attrs in by_tag("table"))
    # no <h1> in the layout.
    assert not by_tag("h1")
    # 13. body text 16px, secondary text at least 14px.
    sizes = [
        int(size) for _, attrs, _ in elements for size in re.findall(r"font:[^;]*?(\d+)px/", attrs.get("style") or "")
    ]
    assert sizes and min(sizes) >= 14 and "font:16px/24px" in html
    # 18. the fonts are the kit's stacks, each ending in a generic family; Word, which ignores fallbacks, gets Arial.
    assert library["kit"].font_body in html and library["kit"].font_body.rstrip().endswith("sans-serif")
    assert "table,td,p,a,span{font-family:Arial,sans-serif !important}" in "".join(word.styles)
    # 20. the colour schemes the layout declares.
    metas = {attrs.get("name"): attrs.get("content") for attrs in by_tag("meta")}
    assert metas["color-scheme"] == metas["supported-color-schemes"] == "light dark"
    style = "".join(document.styles)
    assert ":root{color-scheme:light dark;supported-color-schemes:light dark}" in style
    # 21. every role class has a dark rule, repeated for Outlook's data-ogsc/data-ogsb.
    dark = library["kit"].mail_palette()["dark"]
    for role, value in dark.items():
        css_class = "brand-" + role.replace("_", "-")
        assert f".{css_class}{{" in style and value in style
        assert re.search(rf"\[data-og[sc][bc]\] \.{css_class}\{{", style), css_class
    # 23. the <style> is under 16 KB.
    assert len(style.encode()) < 16_384
    # 26. a hidden preheader right after <body>, with filler.
    preheader = children[0]
    assert "display:none" in (preheader.get("style") or "") and "&zwnj;&nbsp;" in LAYOUT["source"]
    # 33. a print stylesheet.
    assert "@media print" in style
    # brand-radius is declared once, in the layout, and the button and the record card carry it.
    assert style.count(".brand-radius{") == 1
    sources = {row["key"]: row["source"] for row in _shipped("block")}
    assert sum(".brand-radius{" in source for source in sources.values()) == 0
    assert all("brand-radius" in sources[key] for key in ("button", "record_card"))


def test_the_layout_holds_a_template_and_worst_case_samples_under_the_clipping_size(library: Any) -> None:
    """The layout with the default kit and the longest values its blocks take stays well under 102 KB."""

    long = "x" * 2_000
    with system_context(reason="tests.messaging_templates.layout"):
        template = MessageTemplate(
            kind=MessageTemplateKind.TEMPLATE, key="tests.worst_case", language="en", name="Worst case",
            format="markdown", layout="base", subject="{{ title }}", preheader="{{ title }}",
            source=(
                '{% include "parts/greeting" with person=person only %}\n\n'
                '{% include "blocks/record_card" with kind=kind title=title status=status only %}\n\n'
                '{% include "blocks/quote" with author=author body=body only %}\n\n'
                '{% include "blocks/button" with label=title url=link only %}\n\n'
                '{% include "parts/ignore_notice" only %}\n\n'
                '{% include "parts/signature" only %}'
            ),
            variables={key: "x" for key in (
                "person.first_name", "kind", "title", "status", "author", "body", "link", "unsubscribe_url",
            )},
        )
        template.full_clean()
        template.save()
        rendered = template.render({
            "person": {"first_name": long}, "kind": long, "title": long[:200], "status": long, "author": long,
            "body": "\n".join([long[:80]] * 60), "link": "https://app.example.test/" + "a" * 1_900,
            "unsubscribe_url": "https://app.example.test/forms/email-preferences?" + "a" * 1_900,
        }, kit=library["kit"])
    assert len(rendered.html.encode()) < 104_448
    assert not re.search(r"\{\{|\{%|\{#", rendered.html + rendered.plain_text)
    assert "<style" not in rendered.plain_text and "{" not in rendered.plain_text
    assert rendered.plain_text.index("x" * 80) < rendered.plain_text.index("Stop these emails")


@pytest.mark.parametrize("language", ["es"])
def test_a_shipped_part_reads_in_its_own_language_and_the_blocks_fall_back(library: Any, language: str) -> None:
    with system_context(reason="tests.messaging_templates.layout"):
        greeting = MessageTemplate.objects.effective("part", "greeting", language=language)
        rendered = greeting.render({"person": {"first_name": ""}}, kit=library["kit"])
        assert rendered.plain_text.strip() == "Hola,"
        names = ["blocks/button", "parts/greeting", "parts/ignore_notice", "parts/signature"]
        named = MessageTemplate.objects.named_rows(names, language=language)
    assert {name: row.language for name, row in named.items()} == {
        "blocks/button": "en", "parts/greeting": "es", "parts/ignore_notice": "es", "parts/signature": "es",
    }
    assert named["parts/signature"].is_baseline


def test_a_spanish_template_signs_off_in_spanish_in_the_brands_name(library: Any) -> None:
    with system_context(reason="tests.messaging_templates.layout"):
        template = MessageTemplate(
            kind=MessageTemplateKind.TEMPLATE, key="tests.signed", language="es", name="Signed", format="markdown",
            layout="base", subject="Hola", preheader="Un saludo.",
            source='Gracias.\n\n{% include "parts/signature" only %}',
        )
        template.full_clean()
        template.save()
        rendered = template.render({}, kit=library["kit"])
    assert "El equipo de Angee" in rendered.plain_text and "El equipo de Angee" in rendered.html
    assert '<html lang="es"' in rendered.html
