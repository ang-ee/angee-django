"""Knowledge-owned derived-index lifecycle receiver.

A page's outgoing wikilinks are rebuilt from its markdown body on every
body save, so the backlinks panel is a SQL query over rows, not a body scan.
Target creation, renaming and trash changes refresh the affected incoming links.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db.models.signals import post_save

from angee.base.signals import bulk_updated, connect_for_models

_MARKDOWN_LABEL = "knowledge.markdownpage"


def connect() -> None:
    """Wire knowledge-owned receivers after app population."""

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
        link_model._default_manager.rebuild_for(instance)
    if update_fields is None or {"title", "is_trashed"}.intersection(update_fields):
        link_model._default_manager.refresh_targets((instance,))


def refresh_bulk_targets(sender: type[Any], instances: Iterable[Any], update_fields: Iterable[str], **_: Any) -> None:
    """Bulk trash/restore uses the same index owner as individual target saves."""

    if (sender._meta.label_lower in {_MARKDOWN_LABEL, "knowledge.page"}
            and {"title", "is_trashed"}.intersection(update_fields)):
        apps.get_model("knowledge", "Link").objects.refresh_targets(instances)
