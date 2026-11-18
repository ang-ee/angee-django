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


def test_embeds_link_previews_and_meeting_notes_become_links_and_sections():
    body = NotionMarkdownConverter().convert(
        '<embed src="https://example.com/map"></embed>\n\n'
        '<embed src="https://example.com/tour">Walkthrough</embed>\n\n'
        'Photos:<br><link-preview url="https://app.notion.com/p/0123456789abcdef0123456789abcdef#block" '
        'source="https://example.com/photo" />\n\n'
        '<meeting-notes readOnlyViewMeetingNoteUrl="https://app.notion.com/p/meeting">\n'
        '\tMeeting <mention-date start="2026-09-30"/>\n'
        '\t<notes>\n\t\t- Agreed\n\t</notes>\n'
        '</meeting-notes>'
    )
    rendered = MarkdownIt().render(body)
    assert '<a href="https://example.com/map">embed</a>' in rendered
    assert '<a href="https://example.com/tour">Walkthrough</a>' in rendered
    assert '<a href="https://example.com/photo">link preview</a>' in rendered
    assert "Meeting 2026-09-30" in rendered
    assert "<li>Agreed</li>" in rendered
    assert "<pre>" not in rendered and "notes>" not in body


def test_kind_labels_fill_only_links_without_exported_text():
    body = NotionMarkdownConverter().convert(
        '<page url="https://example.com/page">A <span color="red">red</span></page> and '
        '<file src="https://example.com/file"></file>'
    )
    rendered = MarkdownIt().render(body)
    assert '<a href="https://example.com/page">A red</a>' in rendered
    assert '<a href="https://example.com/file">file</a>' in rendered


def test_toggle_heading_children_are_content_and_their_tables_close():
    body = NotionMarkdownConverter().convert(
        '## Guests {toggle="true"}\n'
        '\t### Notes {toggle="true"}\n'
        '\t\t- [ ] Finalize **notes** <span color="red">below</span>\n'
        '\t\t\t<table>\n\t\t\t<colgroup>\n\t\t\t<col width="90">\n\t\t\t</colgroup>\n'
        '<tr>\n<td>Name</td>\n<td>Notes</td>\n</tr>\n'
        '<tr>\n<td>Rafa</td>\n<td>• add here<br>• …</td>\n</tr>\n'
        '\t\t\t</table>\n'
        '\t\t- [ ] Send photos\n'
        '\t\t\t- Highest quality\n'
    )
    rendered = MarkdownIt().enable("table").render(body)
    assert "<pre>" not in rendered
    assert "<h2>Guests</h2>" in rendered and "<h3>Notes</h3>" in rendered
    assert "<strong>notes</strong> below" in rendered
    assert "<td>Rafa</td>" in rendered and "<td>• add here / • …</td>" in rendered
    assert "<li>Highest quality</li>" in rendered.split("Send photos", 1)[1]


def test_children_of_paragraphs_quotes_and_containers_are_not_code():
    body = NotionMarkdownConverter().convert(
        'Intro\n\t<empty-block/>\n\t- Child of a paragraph\n'
        '> Quote\n\tChild of a quote\n'
        '<callout icon="📌">\n\t## Inside {toggle="true"}\n\t\tNested child\n</callout>'
    )
    rendered = MarkdownIt().render(body)
    assert "<pre>" not in rendered
    assert "<li>Child of a paragraph</li>" in rendered
    assert "Child of a quote" in rendered.split("</blockquote>")[0]
    assert "<h2>Inside</h2>" in rendered and "<p>Nested child</p>" in rendered


def test_fenced_code_under_a_toggle_heading_keeps_its_indentation_and_markup():
    body = NotionMarkdownConverter().convert(
        '## Example {toggle="true"}\n'
        '\t```python\n\tdef f():\n\t\treturn "<span>"\n\t# comment\n\t```\n'
        '\tAfter <span color="red">code</span>\n'
    )
    assert body == '## Example \n```python\ndef f():\n\treturn "<span>"\n# comment\n```\nAfter code'


def test_a_deeper_fence_marker_inside_code_stays_code():
    source = '```md\n- item\n\t```\n\tnested\n\t```\n```\nAfter <span color="red">x</span>'
    assert NotionMarkdownConverter().convert(source) == '```md\n- item\n\t```\n\tnested\n\t```\n```\nAfter x'


def test_a_leading_inline_code_span_does_not_open_a_fence():
    source = '```a``b``` is code\n## H {toggle="true"}\n\tchild <span color="red">t</span>'
    body = NotionMarkdownConverter().convert(source)
    rendered = MarkdownIt().render(body)
    assert "<pre>" not in rendered and "<p>child t</p>" in rendered


@pytest.mark.parametrize("inner", ['<mention-date start="2026-09-30"/>', "<br>"])
def test_inline_markup_inside_a_link_is_its_text_not_an_empty_label(inner):
    body = NotionMarkdownConverter().convert(f'<page url="https://example.com/p">{inner}</page>')
    assert "page]" not in body
