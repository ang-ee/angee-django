"""Backlink index maintenance for the knowledge addon.

A page's outgoing wikilinks are rebuilt from its markdown body on every
body save, so the backlinks panel is a SQL query over rows, not a body scan.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db.models.signals import post_save
from django.dispatch import receiver

_MARKDOWN_LABEL = "knowledge.markdownpage"


@receiver(post_save, dispatch_uid="angee.knowledge.backlinks")
def rebuild_backlinks(
    sender: type[Any],
    instance: Any,
    raw: bool = False,
    update_fields: Iterable[str] | None = None,
    **_: Any,
) -> None:
    """Rebuild a page's outgoing wikilinks when its markdown body changes."""

    if raw or instance._meta.label_lower != _MARKDOWN_LABEL:
        return
    if update_fields is not None and "body" not in update_fields:
        return
    link_model = apps.get_model(instance._meta.app_label, "Link")
    link_model._default_manager.rebuild_for(instance)
