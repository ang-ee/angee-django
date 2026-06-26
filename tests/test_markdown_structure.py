"""Tests for the markdown-structure methods on :class:`MarkdownPage`.

These are pure-text behaviours (outline, section range, splice) that need no
database: they exercise the staticmethods directly, plus the ``outline``
property on a (in-memory, unsaved) concrete instance.
"""

from __future__ import annotations

import pytest

from angee.knowledge.models import (
    AmbiguousMatchError,
    MarkdownPage,
    OutlineEntry,
    SectionNotFoundError,
    StructuredEditError,
)
from tests.conftest import MarkdownPage as ConcreteMarkdownPage

# Ported verbatim from the proven spike (scratchpad/md_spike.py): a nested
# outline with code blocks so splices can prove byte-for-byte round-trips.
SAMPLE = """\
# Knowledge Base

Intro paragraph for the whole doc.

## Install

Some install preamble.

### macOS

    brew install angee

### Linux

    apt install angee

## Usage

How to use it.

### CLI

Run `angee dev`.

## Notes

Closing notes.
"""


# ---------------------------------------------------------------------------
# outline / parse_outline
# ---------------------------------------------------------------------------


def test_parse_outline_order_levels_and_slugs() -> None:
    """``parse_outline`` returns every ATX heading in source order."""

    assert MarkdownPage.parse_outline(SAMPLE) == [
        OutlineEntry(level=1, text="Knowledge Base", slug="knowledge-base", line=0),
        OutlineEntry(level=2, text="Install", slug="install", line=4),
        OutlineEntry(level=3, text="macOS", slug="macos", line=8),
        OutlineEntry(level=3, text="Linux", slug="linux", line=12),
        OutlineEntry(level=2, text="Usage", slug="usage", line=16),
        OutlineEntry(level=3, text="CLI", slug="cli", line=20),
        OutlineEntry(level=2, text="Notes", slug="notes", line=24),
    ]


def test_outline_property_delegates_to_parse_outline() -> None:
    """The instance ``outline`` property reads ``self.body`` through the staticmethod."""

    page = ConcreteMarkdownPage(body=SAMPLE)
    assert page.outline == MarkdownPage.parse_outline(SAMPLE)


def test_parse_outline_skips_setext_headings() -> None:
    """Only single-line ATX headings are addressable; setext underlines are body."""

    body = "Title\n=====\n\n## Real\n\nbody\n"
    assert [(entry.level, entry.text) for entry in MarkdownPage.parse_outline(body)] == [(2, "Real")]


def test_parse_outline_normalizes_crlf() -> None:
    """CRLF input yields the same line coordinates as the LF body."""

    assert MarkdownPage.parse_outline(SAMPLE.replace("\n", "\r\n")) == MarkdownPage.parse_outline(SAMPLE)


# ---------------------------------------------------------------------------
# section_range — tail-match + fail-fast
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("heading_path", "expected"),
    [
        ("Install", (4, 16)),
        (["Install"], (4, 16)),
        (["macOS"], (8, 12)),
        (["Usage", "CLI"], (20, 24)),
        (["CLI"], (20, 24)),
        (["Notes"], (24, 28)),
    ],
)
def test_section_range_tail_match(heading_path: str | list[str], expected: tuple[int, int]) -> None:
    """A bare leaf or a qualified ancestor path both resolve by tail-match."""

    assert MarkdownPage.section_range(SAMPLE, heading_path) == expected


def test_section_range_includes_child_sections() -> None:
    """A parent section's range spans its nested children (section-inclusive)."""

    start, end = MarkdownPage.section_range(SAMPLE, "Install")
    span = SAMPLE.split("\n")[start:end]
    assert "### macOS" in span
    assert "### Linux" in span


def test_section_range_missing_raises_section_not_found() -> None:
    """A heading path that matches nothing fails fast, never silent."""

    with pytest.raises(SectionNotFoundError):
        MarkdownPage.section_range(SAMPLE, ["Nope"])


def test_section_range_ambiguous_raises_then_qualified_path_disambiguates() -> None:
    """Duplicate leaf headings are ambiguous; the ancestor-qualified path is not."""

    ambiguous = "# A\n## Detail\nx\n# B\n## Detail\ny\n"
    with pytest.raises(AmbiguousMatchError):
        MarkdownPage.section_range(ambiguous, ["Detail"])
    assert MarkdownPage.section_range(ambiguous, ["B", "Detail"]) == (4, 7)


def test_section_not_found_is_a_structured_edit_error() -> None:
    """Both leaf errors share the ``StructuredEditError`` base for one error map."""

    assert issubclass(SectionNotFoundError, StructuredEditError)
    assert issubclass(AmbiguousMatchError, StructuredEditError)


# ---------------------------------------------------------------------------
# spliced_section — replace / append / prepend keep the rest byte-identical
# ---------------------------------------------------------------------------


def _assert_outside_unchanged(body: str, heading_path: str | list[str], result: str) -> None:
    """Assert everything outside the addressed section is byte-identical."""

    start, end = MarkdownPage.section_range(body, heading_path)
    lines = body.split("\n")
    assert result.startswith("\n".join(lines[:start]))
    assert result.endswith("\n".join(lines[end:]))


def test_spliced_section_replace_keeps_rest_byte_identical() -> None:
    """Replacing a section body changes only that section's content."""

    result = MarkdownPage.spliced_section(SAMPLE, ["macOS"], "replace", "    brew install angee@next\n")
    _assert_outside_unchanged(SAMPLE, ["macOS"], result)
    assert "brew install angee@next" in result
    assert "brew install angee\n" not in result  # the old body line is gone
    assert "### Linux\n\n    apt install angee" in result  # sibling untouched


def test_spliced_section_prepend_lands_under_heading() -> None:
    """Prepend inserts before the existing body, right under the heading."""

    result = MarkdownPage.spliced_section(SAMPLE, ["Notes"], "prepend", "> Heads up.\n")
    _assert_outside_unchanged(SAMPLE, ["Notes"], result)
    assert "## Notes\n\n> Heads up.\n\nClosing notes.\n" in result
    assert "\n\n\n" not in result  # no blank-line pile-up at the seam


def test_spliced_section_append_lands_after_children() -> None:
    """Append (section-inclusive) lands after nested children, before the next heading."""

    result = MarkdownPage.spliced_section(SAMPLE, ["Install"], "append", "See the docs for more.\n")
    _assert_outside_unchanged(SAMPLE, ["Install"], result)
    assert result.index("apt install angee") < result.index("See the docs for more.")
    assert result.index("See the docs for more.") < result.index("## Usage")


def test_spliced_section_normalizes_crlf() -> None:
    """A CRLF body splices to an LF result, never mixing line endings."""

    result = MarkdownPage.spliced_section(SAMPLE.replace("\n", "\r\n"), ["macOS"], "replace", "x\n")
    assert "\r" not in result


def test_spliced_section_rejects_unknown_op() -> None:
    """An unsupported op fails fast rather than silently no-op."""

    with pytest.raises(StructuredEditError):
        MarkdownPage.spliced_section(SAMPLE, ["Notes"], "splice", "x")


# ---------------------------------------------------------------------------
# spliced_unique — exact-string, 0 / 1 / many
# ---------------------------------------------------------------------------


def test_spliced_unique_replaces_single_occurrence() -> None:
    """One match is replaced exactly, the rest left alone."""

    result = MarkdownPage.spliced_unique(SAMPLE, "Run `angee dev`.", "Run `angee build`.")
    assert "Run `angee build`." in result
    assert "Run `angee dev`." not in result


def test_spliced_unique_missing_raises_section_not_found() -> None:
    """A target that is absent fails fast."""

    with pytest.raises(SectionNotFoundError):
        MarkdownPage.spliced_unique(SAMPLE, "nonexistent snippet", "x")


def test_spliced_unique_ambiguous_raises() -> None:
    """A target with more than one occurrence is rejected as ambiguous."""

    with pytest.raises(AmbiguousMatchError):
        MarkdownPage.spliced_unique(SAMPLE, "angee", "ANGEE")
