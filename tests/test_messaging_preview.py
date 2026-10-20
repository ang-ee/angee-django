"""A message preview is user text: the first plain body wins, an HTML-only body is stripped."""

from __future__ import annotations

from angee.messaging.backends import ParsedPart
from angee.messaging.managers import _preview


def test_preview_prefers_the_plain_body_over_the_html_alternative() -> None:
    body = ParsedPart(
        type="multipart/alternative",
        children=(
            ParsedPart(type="text/html", role="body", text='<p style="margin:0">Hi there</p>'),
            ParsedPart(type="text/plain", role="body", text="Hi there, plain"),
        ),
    )
    assert _preview(body) == "Hi there, plain"


def test_preview_of_an_html_only_body_never_carries_markup() -> None:
    body = ParsedPart(type="text/html", role="body", text='<p style="margin:0 0 16px 0;"> Hi,<br>\n<b>team</b></p>')
    assert _preview(body) == "Hi, team"


def test_preview_skips_quoted_and_signature_text() -> None:
    body = ParsedPart(
        type="multipart/mixed",
        children=(
            ParsedPart(type="text/plain", role="quoted", text="> earlier"),
            ParsedPart(type="text/plain", role="signature", text="-- me"),
        ),
    )
    assert _preview(body) == ""
