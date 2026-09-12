"""Deterministic extraction of bounded presentation facts from public HTML."""

from __future__ import annotations

import re
from hashlib import sha256
from html.parser import HTMLParser
from urllib.parse import urljoin, urlunsplit

from django.core.cache import cache

from angee.integrate.http import HttpClient, OutboundBudget, OutboundBudgetState
from angee.integrate.net import parse_http_url

_HEX = re.compile(r"#[0-9a-fA-F]{6}\b")
_FONT = re.compile(r"font-family\s*:\s*([^;}{]{1,160})", re.IGNORECASE)
_CACHE_SECONDS = 15 * 60
_ANALYSER_REVISION = 1
_HTML_BYTES = 768 * 1024
_STYLESHEET_BYTES = 512 * 1024
_TOTAL_BYTES = 2 * 1024 * 1024
_MAX_STYLESHEETS = 4
_OPERATION_BUDGET = OutboundBudget(requests=12, redirects=12, bytes=_TOTAL_BYTES, deadline_seconds=15)


class WebsiteFactsParser(HTMLParser):
    """Collect presentation facts without executing page code or fetching assets."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.in_title = False
        self.styles: list[str] = []
        self.in_style = False
        self.theme_color = ""
        self.site_name = ""
        self.stylesheets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value or "" for name, value in attrs}
        if tag == "title":
            self.in_title = True
        elif tag == "style":
            self.in_style = True
        elif tag == "meta" and values.get("name", "").lower() == "theme-color":
            self.theme_color = values.get("content", "")[:32]
        elif tag == "meta" and (values.get("property", "").lower() == "og:site_name" or values.get("name", "").lower() == "application-name"):
            self.site_name = values.get("content", "")[:200]
        elif tag == "link":
            rel = values.get("rel", "").lower().split()
            href = values.get("href", "")[:2048]
            if "stylesheet" in rel and href and href not in self.stylesheets and len(self.stylesheets) < _MAX_STYLESHEETS:
                self.stylesheets.append(href)
        style = values.get("style")
        if style:
            self.styles.append(style[:2048])

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        elif tag == "style":
            self.in_style = False

    def handle_data(self, data: str) -> None:
        if self.in_title and sum(map(len, self.title_parts)) < 300:
            self.title_parts.append(data)
        if self.in_style and sum(map(len, self.styles)) < 64 * 1024:
            self.styles.append(data)


def analyse_website(url: str, *, cache_partition: str) -> dict[str, object]:
    """Return cached, bounded facts for one public website URL."""

    normalized_url = _normalize_url(url)
    cache_key = f"appearance:analysis:v{_ANALYSER_REVISION}:{sha256(f'{cache_partition}\0{normalized_url}'.encode()).hexdigest()}"
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        return cached
    client = HttpClient()
    budget_state = OutboundBudgetState(_OPERATION_BUDGET)
    result = client.download_bounded(
        normalized_url,
        headers={"Accept": "text/html,application/xhtml+xml", "User-Agent": "Angee Appearance Analyser/1"},
        budget=_OPERATION_BUDGET,
        budget_state=budget_state,
        max_bytes=_HTML_BYTES,
        max_redirects=3,
    )
    if result is None:
        raise ValueError("The website could not be downloaded within the analysis budget.")
    if "html" not in result.content_type.lower() and not result.content.lstrip().startswith(b"<"):
        raise ValueError("The URL did not return HTML.")
    parser = WebsiteFactsParser()
    parser.feed(result.content.decode("utf-8", errors="replace"))
    css_parts = ["\n".join(parser.styles)]
    warnings: list[str] = []
    seen_stylesheets: set[str] = set()
    for href in parser.stylesheets:
        stylesheet_url = urljoin(result.final_url, href)
        if stylesheet_url in seen_stylesheets:
            continue
        seen_stylesheets.add(stylesheet_url)
        sheet = client.download_bounded(
            stylesheet_url,
            headers={"Accept": "text/css", "User-Agent": "Angee Appearance Analyser/1"},
            budget=_OPERATION_BUDGET,
            budget_state=budget_state,
            max_bytes=_STYLESHEET_BYTES,
            max_redirects=3,
        )
        if sheet is None:
            warnings.append(f"A linked stylesheet could not be analysed within the operation budget: {stylesheet_url[:200]}")
            continue
        if "css" not in sheet.content_type.lower():
            warnings.append(f"A linked stylesheet returned an unexpected content type: {sheet.final_url[:200]}")
            continue
        css_parts.append(sheet.content.decode("utf-8", errors="replace"))
    css = "\n".join(css_parts)
    colors: list[str] = []
    for color in [parser.theme_color, *_HEX.findall(css)]:
        normalized = color.lower()
        if _HEX.fullmatch(normalized) and normalized not in colors:
            colors.append(normalized)
        if len(colors) == 12:
            break
    fonts: list[str] = []
    for declaration in _FONT.findall(css):
        family = declaration.split(",", 1)[0].strip().strip("'\"")[:80]
        if family and family not in fonts:
            fonts.append(family)
        if len(fonts) == 6:
            break
    neutral_tint = next((color for color in colors if _is_neutral(color)), "")
    if not colors:
        warnings.append("No six-digit brand colors were found.")
    facts: dict[str, object] = {
        "final_url": result.final_url,
        "title": " ".join("".join(parser.title_parts).split())[:200],
        "site_name": " ".join(parser.site_name.split())[:200],
        "colors": colors,
        "neutral_tint": neutral_tint,
        "fonts": fonts,
        "warnings": warnings[:8],
    }
    cache.set(cache_key, facts, timeout=_CACHE_SECONDS)
    return facts


def _normalize_url(url: str) -> str:
    if not isinstance(url, str) or not url.strip() or len(url) > 2048:
        raise ValueError("Website URL must contain between 1 and 2048 characters.")
    parsed = parse_http_url(url.strip())
    if parsed.username or parsed.password:
        raise ValueError("Website URL must not include credentials.")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))


def _is_neutral(color: str) -> bool:
    channels = [int(color[index:index + 2], 16) for index in (1, 3, 5)]
    return max(channels) - min(channels) <= 24
