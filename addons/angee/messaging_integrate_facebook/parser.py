"""Facebook takeout records mapped onto neutral messaging/posts/parties DTOs."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedThread, body_part
from angee.messaging.backup_ingest import ContentKeyCounter
from angee.messaging_integrate_facebook.archive import (
    FacebookArchive,
    FacebookCommentRecord,
    FacebookConnectionRecord,
    FacebookPostRecord,
)
from angee.messaging_integrate_meta.export import (
    MetaMessageRecord,
    decode_message,
    media_items,
)
from angee.parties.connections import ParsedConnection
from angee.posts.backends import ParsedPost

IDENTITY_METADATA = {
    "identity_confidence": "name_only",
    "source": "facebook_takeout",
}
# Facebook DYI exposes display names but no durable user ids.

_HASHTAG = re.compile(r"(?<!\w)#[\w-]+", re.UNICODE)


def parsed_message(record: MetaMessageRecord, *, own_name: str) -> ParsedMessage:
    """Map one shared Meta message while applying Facebook identity semantics."""

    return decode_message(
        record,
        platform="facebook",
        own_name=own_name,
        identity_metadata=IDENTITY_METADATA,
    )


def parsed_post(
    record: FacebookPostRecord,
    *,
    archive: FacebookArchive,
    own_name: str,
) -> ParsedPost:
    """Map one own post or album photo onto the posts overlay DTO."""

    text, title = _post_text_title(record)
    media_data = _post_media(record.data)
    media = media_items(archive.export, {"photos": media_data})
    metadata = {
        "source": "facebook_takeout",
        "identity": dict(IDENTITY_METADATA),
        **({"album": record.album} if record.album else {}),
    }
    subject_url = _post_subject_url(record.data)
    message = ParsedMessage(
        external_id=record.external_id,
        platform="facebook",
        direction="outbound",
        subject=title,
        sender=_handle(own_name or "Facebook User"),
        sent_at=record.sent_at,
        thread=ParsedThread(
            external_id=record.thread_key,
            modality="public_thread",
            visibility="public",
            title=record.thread_title,
            metadata={"source": "facebook_takeout"},
        ),
        body=body_part(text=text, media=media),
        metadata=metadata,
    )
    return ParsedPost(
        message=message,
        is_original_post=True,
        subject_url=subject_url,
        tags=tuple(_HASHTAG.findall(text)),
    )


@dataclass(frozen=True)
class CommentMatch:
    """A comment-to-own-post match and its evidence strength."""

    post: FacebookPostRecord | None
    strength: str


class FacebookPostMatcher:
    """Match comments to own posts by exact title/timestamp evidence."""

    def __init__(self, posts: list[FacebookPostRecord]) -> None:
        self._by_title: dict[str, list[FacebookPostRecord]] = defaultdict(list)
        self._by_timestamp: dict[int, list[FacebookPostRecord]] = defaultdict(list)
        for post in posts:
            _text, title = _post_text_title(post)
            normalized = _normalize_title(title)
            if normalized:
                self._by_title[normalized].append(post)
            self._by_timestamp[int(post.sent_at.timestamp())].append(post)

    def match(self, comment: FacebookCommentRecord) -> CommentMatch:
        """Return a strong two-signal match, a unique weak match, or unmatched."""

        title = _normalize_title(str(comment.data.get("title") or ""))
        title_matches = self._by_title.get(title, []) if title else []
        time_matches = self._by_timestamp.get(int(comment.sent_at.timestamp()), [])
        intersection = [post for post in title_matches if post in time_matches]
        if len(intersection) == 1:
            return CommentMatch(intersection[0], "strong")
        candidates = {post.external_id: post for post in [*title_matches, *time_matches]}
        if len(candidates) == 1:
            return CommentMatch(next(iter(candidates.values())), "weak")
        return CommentMatch(None, "unmatched")


def parsed_comment(
    record: FacebookCommentRecord,
    *,
    own_name: str,
    counter: ContentKeyCounter,
    matcher: FacebookPostMatcher,
) -> ParsedPost:
    """Map one own comment, attaching it to a matched post when evidence permits."""

    match = matcher.match(record)
    title = str(record.data.get("title") or "")
    text = _comment_text(record.data)
    thread_key = match.post.thread_key if match.post is not None else "feed:comments"
    external_id = counter.key(
        thread_key=thread_key,
        timestamp_ms=int(record.sent_at.timestamp() * 1000),
        sender_key=own_name,
        content=record.data,
    )
    thread = (
        None
        if match.post is not None
        else ParsedThread(
            external_id="feed:comments",
            modality="public_thread",
            visibility="public",
            title=f"{own_name}'s Comments" if own_name else "Facebook Comments",
            metadata={"source": "facebook_takeout"},
        )
    )
    message = ParsedMessage(
        external_id=external_id,
        platform="facebook",
        direction="outbound",
        subject=title,
        sender=_handle(own_name or "Facebook User"),
        sent_at=record.sent_at,
        in_reply_to=match.post.external_id if match.post is not None else "",
        thread=thread,
        body=body_part(text=text),
        metadata={
            "source": "facebook_takeout",
            "identity": dict(IDENTITY_METADATA),
            "post_match": match.strength,
        },
    )
    return ParsedPost(message=message, is_original_post=False)


def parsed_connection(record: FacebookConnectionRecord) -> ParsedConnection:
    """Map one friend record onto parties' shared connection DTO."""

    return ParsedConnection(
        handle=_handle(record.name),
        connected_at=record.connected_at,
        provenance=record.provenance,
    )


def _handle(name: str) -> ParsedHandle:
    """Return Facebook's accepted name-only handle with explicit confidence."""

    return ParsedHandle(
        platform="facebook",
        value=name,
        display_name=name,
        metadata=dict(IDENTITY_METADATA),
    )


def _post_text_title(record: FacebookPostRecord) -> tuple[str, str]:
    """Return the first post body and its title from Facebook's nested shape."""

    if record.album:
        return (
            str(record.data.get("description") or ""),
            str(record.data.get("title") or record.album),
        )
    text = ""
    for item in record.data.get("data", ()):
        if isinstance(item, Mapping) and item.get("post"):
            text = str(item["post"])
            break
    return text, str(record.data.get("title") or "")


def _comment_text(data: Mapping[str, Any]) -> str:
    """Return the first nested Facebook comment body."""

    for item in data.get("data", ()):
        if not isinstance(item, Mapping):
            continue
        comment = item.get("comment")
        if isinstance(comment, Mapping) and comment.get("comment"):
            return str(comment["comment"])
    return ""


def _post_media(data: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Collect direct album media and nested post attachment media."""

    media: list[Mapping[str, Any]] = []
    if data.get("uri"):
        media.append(data)
    for attachment in data.get("attachments", ()):
        if not isinstance(attachment, Mapping):
            continue
        for item in attachment.get("data", ()):
            if not isinstance(item, Mapping):
                continue
            value = item.get("media")
            if isinstance(value, Mapping) and value.get("uri"):
                media.append(value)
    return media


def _post_subject_url(data: Mapping[str, Any]) -> str:
    """Return the first canonical external-context URL on a post."""

    for attachment in data.get("attachments", ()):
        if not isinstance(attachment, Mapping):
            continue
        for item in attachment.get("data", ()):
            if not isinstance(item, Mapping):
                continue
            context = item.get("external_context")
            if isinstance(context, Mapping) and context.get("url"):
                return str(context["url"])
    return ""


def _normalize_title(value: str) -> str:
    """Return a whitespace/case-insensitive post-match title key."""

    return " ".join(str(value or "").split()).casefold()
