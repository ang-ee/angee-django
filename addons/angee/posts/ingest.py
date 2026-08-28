"""Posts-owned landing for public message cores and engagement overlays."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from django.apps import apps

from angee.posts.backends import ParsedPost


def land_posts(channel: Any, posts: list[ParsedPost], *, owner_id: Any) -> list[Any]:
    """Land public posts through messaging, then apply the posts-owned overlay.

    Message/thread/part persistence stays on ``Message.objects.ingest``. This
    owner supplies the public structural facts that make ingest derive COMMENT,
    disables email quotation edges, then writes payload, metrics, reactions, and
    cross-post relations exactly once through their existing owners.
    """

    message_model = apps.get_model("messaging", "Message")
    thread_model = apps.get_model("messaging", "Thread")
    messages = message_model.objects.ingest(
        [
            replace(
                post.message,
                metadata={**post.message.metadata, "tags": list(post.tags)},
            )
            for post in posts
        ],
        channel=channel,
        owner_id=owner_id,
        modality=thread_model.Modality.PUBLIC_THREAD,
        visibility=thread_model.Visibility.PUBLIC,
        quote_edges=False,
    )
    _overlay_engagement(channel, posts, messages, owner_id=owner_id)
    return messages


def _overlay_engagement(
    channel: Any,
    posts: list[ParsedPost],
    messages: list[Any],
    *,
    owner_id: Any,
) -> None:
    """Attach public payload and engagement to the rows messaging returned."""

    if not posts:
        return
    metrics_model = apps.get_model("posts", "PostMetrics")
    reaction_model = apps.get_model("messaging", "Reaction")
    edge_model = apps.get_model("messaging", "MessageEdge")
    handle_model = apps.get_model("parties", "Handle")
    by_key = {(message.platform, message.external_id): message for message in messages}
    landed = [
        (message, post)
        for post in posts
        if (message := by_key.get((post.message.platform, post.message.external_id))) is not None
    ]

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
        owner_id=owner_id,
    )

    targets = _resolve_relation_targets(landed, by_key, channel_id=channel.pk)
    for message, post in landed:
        for relation in post.relations:
            target = targets.get((post.message.platform, relation.dst_external_id))
            if target is not None:
                edge_model.objects.relate(message, target, kind=relation.kind, owner_id=owner_id)


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
    if thread is None:
        return
    subject_url = post.subject_url or ""
    if thread.subject_url != subject_url:
        thread.subject_url = subject_url
        thread.save(update_fields=("subject_url", "updated_at"))
