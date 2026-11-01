"""Knowledge-owned derived-index lifecycle receiver.

A page's outgoing wikilinks are rebuilt from its markdown body on every
body save, so the backlinks panel is a SQL query over rows, not a body scan.
Target creation, renaming and trash changes refresh the affected incoming links.
"""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import ExitStack
from typing import TYPE_CHECKING, Any, cast

from django.apps import apps
from django.db import models
from django.db.models.signals import post_save

from angee.base.signals import bulk_updated, connect_for_models, pre_merge, publish_change

if TYPE_CHECKING:
    from angee.knowledge.models.bindings import RecordBinding

_MARKDOWN_LABEL = "knowledge.markdownpage"


def connect() -> None:
    """Wire knowledge-owned receivers after app population."""

    pre_merge.connect(binding_merge_scopes, dispatch_uid="angee.knowledge.binding_merge_scopes")

    def is_page(model: type[Any]) -> bool:
        return model._meta.label_lower in {_MARKDOWN_LABEL, "knowledge.page"}

    connect_for_models(
        post_save, rebuild_backlinks, applies=is_page, dispatch_uid="angee.knowledge.backlinks",
    )
    connect_for_models(
        bulk_updated, refresh_bulk_targets, applies=is_page, dispatch_uid="angee.knowledge.backlinks.bulk",
    )


def rebuild_backlinks(
    sender: type[Any],
    instance: Any,
    using: str,
    raw: bool = False,
    update_fields: Iterable[str] | None = None,
    knowledge_backlinks_rebuilt: bool = False,
    **_: Any,
) -> None:
    """Keep outgoing links and incoming target resolution current after saves."""

    if raw or knowledge_backlinks_rebuilt or instance._meta.label_lower not in {_MARKDOWN_LABEL, "knowledge.page"}:
        return
    link_model = apps.get_model(instance._meta.app_label, "Link")
    if instance._meta.label_lower == _MARKDOWN_LABEL and (update_fields is None or "body" in update_fields):
        link_model._default_manager.db_manager(using).rebuild_for(instance)
    if update_fields is None or {"title", "is_trashed"}.intersection(update_fields):
        link_model._default_manager.db_manager(using).refresh_targets((instance,))


def refresh_bulk_targets(
    sender: type[Any], instances: Iterable[Any], update_fields: Iterable[str], using: str, **_: Any,
) -> None:
    """Bulk trash/restore uses the same index owner as individual target saves."""

    if (sender._meta.label_lower in {_MARKDOWN_LABEL, "knowledge.page"}
            and {"title", "is_trashed"}.intersection(update_fields)):
        apps.get_model("knowledge", "Link").objects.db_manager(using).refresh_targets(instances)


def binding_merge_scopes(
    sender: type[models.Model], *, survivor: models.Model, merged: tuple[models.Model, ...],
    contexts: ExitStack, **_: Any,
) -> None:
    """Retain only this fold's binding scopes while the native mover owns its writes."""
    binding_model = cast("type[RecordBinding]", apps.get_model("knowledge", "RecordBinding"))
    if not merged or not binding_model.declares_target(sender):
        return
    predicate = models.Q()
    for record in merged:
        predicate |= binding_model.record_filter(record)
    previous = tuple(binding_model.system_queryset().using(survivor._state.db).filter(predicate).order_by("pk"))
    if previous:
        contexts.callback(binding_merge_completed, binding_model, previous, survivor._state.db)


def binding_merge_completed(model: type[RecordBinding], previous: tuple[RecordBinding, ...], using: str | None) -> None:
    """Observe the old and new scope identities after an actual in-place retarget.

    Collision deletions already publish through native post_delete. Identity-only
    observations expose no prior field values; the transport still gates each
    related reference and queues publication after the surrounding commit.
    """
    current = model.system_queryset().using(using).in_bulk(row.pk for row in previous)
    for before in previous:
        after = current.get(before.pk)
        if after is None or before.record_ref == after.record_ref:
            continue
        publish_change(before, action="update", update_fields=None)
        publish_change(after, action="update", update_fields=None)
