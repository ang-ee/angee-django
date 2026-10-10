"""Posts-owned landing for public message cores and engagement overlays."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from django.apps import apps
from django.db import transaction
from rebac import system_context

if TYPE_CHECKING:
    from angee.posts.backends import ParsedPost

PROVIDER_TRASH_REASON = "Hidden by the source."


def land_posts(
    channel: Any, posts: list[ParsedPost], *, owner_id: Any, historical: bool = False, relations: bool = True,
) -> list[Any]:
    """Land public posts through messaging, then apply the posts-owned overlay.

    Message/thread/part persistence stays on ``Message.objects.ingest``. This
    owner supplies the public structural facts that make ingest derive COMMENT,
    disables email quotation edges, then writes payload, metrics, reactions, and
    cross-post relations exactly once through their existing owners.
    """

    message_model = apps.get_model("messaging", "Message")
    thread_model = apps.get_model("messaging", "Thread")
    messages = []
    # Overlay and moderation must exist before the one messaging event owner runs.
    for post in posts:
        def overlay(message: Any, *, post: ParsedPost = post) -> None:
            _overlay_engagement([post], [message], owner_id=owner_id)
            with system_context(reason="posts.ingest.moderation"):
                if post.hidden and not message.is_trashed:
                    message.trash(reason=PROVIDER_TRASH_REASON)
                elif not post.hidden and message.is_trashed and message.trash_reason == PROVIDER_TRASH_REASON:
                    message.restore()
        with transaction.atomic():
            messages.extend(message_model.objects.ingest(
                [replace(post.message, metadata={**post.message.metadata, "tags": list(post.tags)})],
                channel=channel, created_by_id=owner_id, modality=thread_model.Modality.PUBLIC_THREAD,
                visibility=thread_model.Visibility.PUBLIC, quote_edges=False, historical=historical,
                after_landing=overlay,
            ))
    if relations:
        land_post_relations(channel, posts, messages, owner_id=owner_id)
    return messages


def _overlay_engagement(posts: list[ParsedPost], messages: list[Any], *, owner_id: Any) -> None:
    """Attach public payload and engagement to the rows messaging returned."""

    if not posts:
        return
    metrics_model = apps.get_model("posts", "PostMetrics")
    reaction_model = apps.get_model("messaging", "Reaction")
    handle_model = apps.get_model("parties", "Handle")
    landed, _ = _pair_landed(posts, messages)

    for message, post in landed:
        _write_public_payload(message, post)
        if post.metrics is not None:
            metrics_model.objects.upsert(message=message, metrics=post.metrics, owner_id=owner_id)

    handles = _resolve_reaction_handles(landed, handle_model, owner_id)
    reaction_model.objects.attribute(
        (
            (message, handles[(reaction.handle.platform, reaction.handle.value)], reaction.reaction)
            for message, post in landed
            for reaction in post.reactions
        ),
        created_by_id=owner_id,
    )


def land_post_relations(channel: Any, posts: Sequence[ParsedPost], messages: list[Any], *, owner_id: Any) -> None:
    """Finish successfully landed cross-post relations once a page is visible."""

    landed, by_key = _pair_landed(posts, messages)
    targets = _resolve_relation_targets(landed, by_key, channel_id=channel.pk)
    edge_model = apps.get_model("messaging", "MessageEdge")
    for message, post in landed:
        for relation in post.relations:
            target = targets.get((post.message.platform, relation.dst_external_id))
            if target is not None:
                edge_model.objects.relate(message, target, kind=relation.kind, created_by_id=owner_id)


def _pair_landed(posts: Sequence[ParsedPost], messages: list[Any]) -> tuple[list[Any], dict]:
    """Pair each post with the message messaging landed for it, keyed by external identity."""

    by_key = {(message.platform, message.external_id): message for message in messages}
    landed = [
        (message, post)
        for post in posts
        if (message := by_key.get((post.message.platform, post.message.external_id))) is not None
    ]
    return landed, by_key


def _resolve_reaction_handles(landed: list[Any], handle_model: Any, owner_id: Any) -> dict:
    """Upsert each distinct reactor handle once."""

    specs: dict[tuple[str, str], Any] = {}
    for _message, post in landed:
        for reaction in post.reactions:
            specs.setdefault((reaction.handle.platform, reaction.handle.value), reaction.handle)
    return {
        key: handle_model.objects.upsert(
            platform=parsed.platform,
            value=parsed.value,
            created_by_id=owner_id,
            display_name=parsed.display_name,
            external_id=parsed.external_id,
            metadata=parsed.metadata,
        )
        for key, parsed in specs.items()
    }


def _resolve_relation_targets(landed: list[Any], by_key: dict, *, channel_id: Any) -> dict:
    """Key every cross-post target by platform and external id."""

    message_model = apps.get_model("messaging", "Message")
    targets = dict(by_key)
    missing: dict[str, set[str]] = {}
    for _message, post in landed:
        for relation in post.relations:
            key = (post.message.platform, relation.dst_external_id)
            if key not in targets:
                missing.setdefault(post.message.platform, set()).add(relation.dst_external_id)
    for platform, external_ids in missing.items():
        rows = list(
            message_model.objects.with_external_ids(sorted(external_ids)).filter(platform=platform)
        )
        for row in sorted(rows, key=lambda row: row.channel_id == channel_id):
            targets[(platform, row.external_id)] = row
    return targets


def _write_public_payload(message: Any, post: ParsedPost) -> None:
    """Fold parsed public-post fields onto the shared message/thread rows."""

    if message.is_original_post != post.is_original_post:
        message.is_original_post = post.is_original_post
        message.save(update_fields=("is_original_post", "updated_at"))
    thread = message.thread
    if thread is None or not post.is_original_post or not post.subject_url:
        return
    subject_url = post.subject_url
    if thread.subject_url != subject_url:
        thread.subject_url = subject_url
        thread.save(update_fields=("subject_url", "updated_at"))
