"""Knowledge-owned derived-index and record-binding lifecycle receivers.

A page's outgoing wikilinks are rebuilt from its markdown body on every
body save, so the backlinks panel is a SQL query over rows, not a body scan.
Record bindings point to arbitrary models through a ``GenericForeignKey``. For
targets that do not declare a reverse generic relation, the global ``pre_delete``
receiver removes any canonical-target bindings before primary-key reuse can
resolve them onto another row.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from django.apps import AppConfig, apps
from django.db import router
from django.db.models.signals import post_migrate, post_save, pre_delete

_MARKDOWN_LABEL = "knowledge.markdownpage"
logger = logging.getLogger(__name__)


def connect() -> None:
    """Wire knowledge-owned receivers after app population."""

    post_save.connect(rebuild_backlinks, dispatch_uid="angee.knowledge.backlinks")
    pre_delete.connect(teardown_record_bindings, dispatch_uid="angee.knowledge.record_binding.teardown")
    post_migrate.connect(
        migrate_page_attribution,
        sender=apps.get_app_config("knowledge"),
        dispatch_uid="angee.knowledge.migrate_page_attribution",
    )


def migrate_page_attribution(sender: AppConfig, using: str, **_: Any) -> None:
    """Apply the page owner's pending access transition before permission sync."""

    page_model = sender.get_model("Page")
    if using != router.db_for_write(page_model) or not router.allow_migrate_model(using, page_model):
        return
    changes = page_model._default_manager.db_manager(using).migrate_attribution(apply=True)
    grants = sum(change.grants_viewer for change in changes)
    if grants:
        logger.info(
            "Page attribution migration: %d viewer share(s), %d lost write(s), %d ownerless-vault page(s) excluded.",
            grants,
            sum(change.loses_write for change in changes),
            sum(change.ownerless_vault for change in changes),
        )


def rebuild_backlinks(
    sender: type[Any],
    instance: Any,
    raw: bool = False,
    update_fields: Iterable[str] | None = None,
    knowledge_backlinks_rebuilt: bool = False,
    **_: Any,
) -> None:
    """Rebuild a page's outgoing wikilinks when its markdown body changes."""

    if raw or knowledge_backlinks_rebuilt or instance._meta.label_lower != _MARKDOWN_LABEL:
        return
    if update_fields is not None and "body" not in update_fields:
        return
    link_model = apps.get_model(instance._meta.app_label, "Link")
    link_model._default_manager.rebuild_for(instance)


def teardown_record_bindings(sender: type[Any], instance: Any, **kwargs: Any) -> None:
    """Delete bindings to the canonical target before any model row is deleted."""

    del sender, kwargs
    apps.get_model("knowledge", "RecordBinding").objects.teardown_for_record(instance)
