"""Translate Notion's enhanced Markdown into the Knowledge renderer's CommonMark.

Markdown-it owns code, escapes and link syntax; HTMLParser owns tag attributes.
Vendor extensions and opted-in page destinations are rewritten. The original export stays on the
RecordLink, so presentation details discarded here remain available at source.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from markdown_it import MarkdownIt
from markdown_it.rules_inline import StateInline, autolink, html_inline, link
from markdown_it.token import Token


class NotionMarkdownConverter(HTMLParser):
    """A per-document adapter for the stable enhanced-Markdown (v1) vocabulary."""

    _ATTRIBUTES = re.compile(r'\{(?:(?:color="[a-z_]+"|toggle="true")\s*)+\}')
    _CONTAINERS = {"details", "callout", "columns", "column", "synced_block", "synced_block_reference"}
    _LINKS = {
        "page", "database", "audio", "video", "file", "pdf", "mention-user", "mention-page",
        "mention-database", "mention-data-source", "mention-agent", "unknown",
    }
    _TABLE_TAGS = {"table", "tr", "td", "th", "colgroup", "col"}
    _PAGE_LINKS = {"page", "database", "mention-page", "mention-database", "mention-data-source"}
    _PAGE_ID = re.compile(r"([0-9a-f]{32}|[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})$", re.IGNORECASE)

    def __init__(self, page_links: Mapping[str, str] | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.page_links = page_links or {}
        self.page_ids: set[str] = set()
        self.parser = MarkdownIt("commonmark").disable("html_block")
        self.parser.inline.ruler.before("text", "notion", self._tag)
        self.parser.inline.ruler.at("link", self._link)
        self.parser.inline.ruler.at("autolink", self._autolink)
        self.source = ""
        self.literal_blocks: list[tuple[int, int, str]] = []
        self.edits: list[tuple[int, int, str]] = []
        self.stack: list[tuple[str, str]] = []
        self.containers: list[tuple[int, int]] = []
        self.open_containers: list[int] = []
        self.position = 0
        self.output = ""
        self.tag_end = 0
        self.table_tag = False
        self.table_columns = 0
        self.table_rows = 0

    def convert(self, source: str) -> str:
        """Adapt vendor markup and page destinations without reserializing Markdown."""
        self.source = source
        offsets = [0]
        for line in source.splitlines(keepends=True):
            offsets.append(offsets[-1] + len(line))
        blocks: list[Token] = []
        environment: dict[str, Any] = {}
        self.parser.block.parse(source, self.parser, environment, blocks)
        self.literal_blocks = [
            (offsets[token.map[0]], offsets[token.map[1]], token.type)
            for token in blocks if token.type in {"fence", "code_block"} and token.map is not None
        ]
        self.literal_blocks.extend(
            (offsets[reference["map"][0]], offsets[reference["map"][1]], "reference")
            for reference in environment.get("references", {}).values()
        )
        self.parser.inline.parse(source, self.parser, environment, [])
        if self.stack:
            raise ValueError("Unclosed Notion markup")
        offset = 0
        for line in source.splitlines(keepends=True):
            depth = sum(start <= offset < end for start, end in self.containers)
            indentation = len(line) - len(line.lstrip("\t"))
            if depth and indentation and not any(start <= offset < end for start, end, _ in self.edits):
                self.edits.append((offset, offset + min(depth, indentation), ""))
            offset += len(line)
        if not self.edits:
            return source
        chunks: list[str] = []
        offset = 0
        for start, end, replacement in sorted(self.edits):
            chunks.extend((source[offset:start], replacement))
            offset = end
        chunks.append(source[offset:])
        return "".join(chunks).strip()

    def _url(self, url: str) -> str:
        """Resolve only whole Notion page identities; block anchors remain at source."""

        try:
            parsed = urlsplit(url)
        except ValueError:
            return url
        host = parsed.hostname or ""
        if parsed.scheme not in {"http", "https"} or parsed.fragment or not (
            host in {"notion.so", "notion.site", "app.notion.com"}
            or host.endswith((".notion.so", ".notion.site"))
        ):
            return url
        candidate = parse_qs(parsed.query).get("p", [parsed.path.rsplit("/", 1)[-1]])[0]
        match = self._PAGE_ID.search(candidate)
        if match is None:
            return url
        key = str(UUID(match[1]))
        self.page_ids.add(key)
        return self.page_links.get(key, url)

    def _link(self, state: StateInline, silent: bool) -> bool:
        start, count = state.pos, len(state.tokens)
        if not link(state, silent):
            return False
        if not silent and state.src is self.source:
            opening = next(token for token in state.tokens[count:] if token.type == "link_open")
            original = str(opening.attrGet("href") or "")
            rewritten = self._url(original)
            if rewritten != original:
                label_end = self.parser.helpers.parseLinkLabel(state, start, True)
                title = opening.attrGet("title")
                suffix = f" {json.dumps(title, ensure_ascii=False)}" if title else ""
                self.edits.append((label_end + 1, state.pos, f"(<{rewritten}>{suffix})"))
        return True

    def _autolink(self, state: StateInline, silent: bool) -> bool:
        start, count = state.pos, len(state.tokens)
        if not autolink(state, silent):
            return False
        if not silent and state.src is self.source:
            opening = next(token for token in state.tokens[count:] if token.type == "link_open")
            original = str(opening.attrGet("href") or "")
            rewritten = self._url(original)
            if rewritten != original:
                label = re.sub(r"([\\\[\]])", r"\\\1", state.src[start + 1:state.pos - 1])
                self.edits.append((start, state.pos, f"[{label}](<{rewritten}>)"))
        return True

    def _tag(self, state: StateInline, silent: bool) -> bool:
        # Nested inline parses (image alt text) have their own offsets and are
        # literal labels, not vendor markup in this document.
        if state.src is not self.source:
            return False
        start = state.pos
        for begin, end, kind in self.literal_blocks:
            # Notion's containers use tabs to nest their children, not to mark
            # CommonMark indented code; convert() removes that owned indentation.
            if begin <= start < end and (kind in {"fence", "reference"} or not self.open_containers):
                state.pos = end
                return True
        attributes = self._ATTRIBUTES.match(state.src, start)
        if attributes:
            state.pos = attributes.end()
            if not silent:
                self.edits.append((start, state.pos, ""))
            return True
        if not html_inline(state, silent):
            return False
        if not silent:
            self.position, self.output = start, ""
            self.table_tag = False
            self.feed(state.src[start:state.pos])
            if self.table_tag and self.tag_end < start and not state.src[self.tag_end:start].strip():
                self.edits.append((self.tag_end, start, ""))
            self.edits.append((start, state.pos, self.output))
            self.tag_end = state.pos
        return True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        self.table_tag = tag in self._TABLE_TAGS
        prefix = suffix = ""
        if tag in self._LINKS:
            url = attributes.get("url") or attributes.get("src") or ""
            if url and self.parser.validateLink(url):
                if tag in self._PAGE_LINKS:
                    url = self._url(url)
                prefix, suffix = "[", f"](<{self.parser.normalizeLink(url)}>)"
        elif tag in self._CONTAINERS:
            prefix = suffix = "\n\n"
            icon = attributes.get("icon")
            if tag == "callout" and icon:
                prefix += icon + " "
            self.open_containers.append(self.position)
        elif tag == "summary":
            prefix, suffix = "#### ", "\n\n"
        elif tag == "table":
            self.table_rows = 0
            prefix = suffix = "\n\n"
        elif tag == "tr":
            self.table_columns = 0
            prefix, suffix = "|", "\n"
        elif tag in {"td", "th"}:
            self.table_columns += 1
            prefix, suffix = " ", " |"
        elif tag == "br":
            self.output = " / " if any(name == "table" for name, _ in self.stack) else "  \n"
            return
        elif tag == "empty-block":
            self.output = "\n"
            return
        elif tag == "mention-date":
            start = " ".join(filter(None, (attributes.get("start"), attributes.get("starttime"))))
            end = " ".join(filter(None, (attributes.get("end"), attributes.get("endtime"))))
            self.output = " – ".join(filter(None, (start, end)))
            if attributes.get("timezone"):
                self.output += f" ({attributes['timezone']})"
            return
        elif tag in {"table_of_contents", "col"}:
            return
        elif tag not in {"span", "colgroup"}:
            raise ValueError(f"Unsupported Notion markup: {tag}")
        self.stack.append((tag, suffix))
        self.output = prefix

    def handle_endtag(self, tag: str) -> None:
        self.table_tag = tag in self._TABLE_TAGS
        if not self.stack or self.stack[-1][0] != tag:
            raise ValueError("Mismatched Notion markup")
        _, self.output = self.stack.pop()
        if tag in self._CONTAINERS:
            self.containers.append((self.open_containers.pop(), self.position))
        elif tag == "tr":
            if not self.table_rows:
                self.output += "|" + " --- |" * self.table_columns + "\n"
            self.table_rows += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        depth = len(self.stack)
        self.handle_starttag(tag, attrs)
        prefix = self.output
        if len(self.stack) > depth:
            self.handle_endtag(tag)
            label = tag.removeprefix("mention-") if tag in self._LINKS else ""
            if tag == "unknown":
                label = f"{dict(attrs).get('alt') or 'Unsupported block'} (view in Notion)"
            self.output = prefix + label + self.output
