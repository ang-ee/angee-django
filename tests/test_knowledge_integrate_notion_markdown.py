"""Provider syntax becomes readable Markdown without corrupting literal examples."""

import pytest
from markdown_it import MarkdownIt

from angee.knowledge_integrate_notion.markdown import NotionMarkdownConverter

PAGE_ID = "01234567-89ab-cdef-0123-456789abcdef"
PAGE_URL = "https://www.notion.so/Guide-0123456789abcdef0123456789abcdef"
LOCAL_URL = "wikilink:Guide%20%23%20one"


@pytest.mark.parametrize("source", [
    f'[A **bold** guide]({PAGE_URL} "A title")',
    f'[A **bold** guide][ref]\n\n[ref]: {PAGE_URL} "A title"',
    f'<page url="{PAGE_URL}">A **bold** guide</page>',
    f'<mention-page url="{PAGE_URL}">A **bold** guide</mention-page>',
])
def test_imported_page_links_preserve_labels_and_native_markdown(source):
    converter = NotionMarkdownConverter({PAGE_ID: LOCAL_URL})
    body = converter.convert(source)
    rendered = MarkdownIt().render(body)
    assert f'href="{LOCAL_URL}"' in rendered
    assert 'A <strong>bold</strong> guide</a>' in rendered
    assert converter.page_ids == {PAGE_ID}
    if "[ref]:" in source:
        assert body.split("\n\n")[1] == source.split("\n\n")[1]


@pytest.mark.parametrize("url", [
    PAGE_URL,
    f"https://team.notion.site/{PAGE_ID}",
    f"https://app.notion.com/workspace?p={PAGE_ID}",
])
def test_notion_autolinks_resolve_the_same_page_identity(url):
    body = NotionMarkdownConverter({PAGE_ID: LOCAL_URL}).convert(f"<{url}>")
    assert f'href="{LOCAL_URL}"' in MarkdownIt().render(body)
    assert f">{url}</a>" in MarkdownIt().render(body)


@pytest.mark.parametrize("source", [
    f"[Unimported]({PAGE_URL})",
    f"[Block]({PAGE_URL}#block)",
    f"[Other host](https://example.com/{PAGE_ID})",
    f"`[Code]({PAGE_URL})`",
    f"```md\n[Code]({PAGE_URL})\n```",
    f"    [Code]({PAGE_URL})\n",
])
def test_unknown_targets_blocks_external_urls_and_literal_examples_stay_at_source(source):
    links = {} if source.startswith("[Unimported]") else {PAGE_ID: LOCAL_URL}
    assert NotionMarkdownConverter(links).convert(source) == source


@pytest.mark.parametrize("source", [
    "# Plain Markdown\n\n- [ ] Task\n\n[[Page]]\n",
    '```html\n<span color="red">Literal</span>\n```',
    '~~~html\n<span color="red">Literal</span>\n~~~',
    '    <span color="red">Literal</span>\n',
    '``<page url="https://example.com">Example</page>`` and `code`',
    r'Escaped \<span> and \{color="red"}',
])
def test_plain_markdown_and_code_stay_literal(source):
    assert NotionMarkdownConverter().convert(source) == source


def test_vendor_references_and_media_keep_their_urls():
    body = NotionMarkdownConverter().convert(
        '<page url="https://example.com/page">Child</page>\n'
        '<empty-block/>\n<video src="https://example.com/video">Watch</video>\n'
        '<mention-user url="https://example.com/user"/>\n'
        '<unknown url="https://example.com/block" alt="bookmark"/>\n'
        '<mention-date start="2026-10-26" startTime="10:00" timeZone="UTC"/>'
    )
    rendered = MarkdownIt().render(body)
    assert '<a href="https://example.com/page">Child</a>' in rendered
    assert '<a href="https://example.com/video">Watch</a>' in rendered
    assert '<a href="https://example.com/user">user</a>' in rendered
    assert '<a href="https://example.com/block">bookmark (view in Notion)</a>' in rendered
    assert "2026-10-26 10:00 (UTC)" in rendered
    assert "empty-block" not in body


def test_fenced_examples_and_real_vendor_markup_can_share_a_document():
    source = '~~~html\n<custom-block/>\n~~~\n\n<span color="red">Text</span>'
    assert NotionMarkdownConverter().convert(source) == '~~~html\n<custom-block/>\n~~~\n\nText'


def test_image_alt_text_does_not_rewrite_document_offsets():
    image = '![<span color="red">alt</span>](https://example.com/image.png)'
    assert NotionMarkdownConverter().convert(image + ' <span color="blue">Text</span>') == image + ' Text'


def test_containers_keep_children_and_inline_formatting():
    body = NotionMarkdownConverter().convert(
        '<details>\n<summary>A **bold** title</summary>\n'
        '\t- <span color="red">First</span>\n'
        '\t- Second {color="red"}\n</details>'
    )
    rendered = MarkdownIt().render(body)
    assert "A <strong>bold</strong> title" in rendered
    assert "<li>First</li>" in rendered
    assert "<li>Second</li>" in rendered
    assert "<details>" not in body


def test_tables_preserve_rows_cells_and_styled_text():
    body = NotionMarkdownConverter().convert(
        '<table>\n\t<tr><td>A</td><td>B</td></tr>\n'
        '\t<tr><td><span color="red">C</span> D</td><td>E<br>F</td></tr>\n</table>'
    )
    rendered = MarkdownIt().enable("table").render(body)
    assert "<th>A</th>" in rendered
    assert "<th>B</th>" in rendered
    assert "<td>C D</td>" in rendered
    assert "<td>E / F</td>" in rendered


@pytest.mark.parametrize("source", ["<custom-block/>", "<details>Unclosed", "<span>Wrong</details>"])
def test_unknown_or_malformed_markup_refuses_silent_data_loss(source):
    with pytest.raises(ValueError):
        NotionMarkdownConverter().convert(source)


def test_rewritten_link_title_retains_unicode():
    source = f'[Guide]({PAGE_URL} "日本語 café")'
    result = NotionMarkdownConverter({PAGE_ID: LOCAL_URL}).convert(source)
    assert 'title="日本語 café"' in MarkdownIt().render(result)
