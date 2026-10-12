"""Knowledge-owned derived-index and record-binding lifecycle receivers.

A page's outgoing wikilinks are rebuilt from its markdown body on every
body save, so the backlinks panel is a SQL query over rows, not a body scan.
Record bindings point to REBAC-typed records through a ``GenericForeignKey``. For
targets that do not declare a reverse generic relation, a ``post_delete`` receiver
removes bindings once the canonical row's DELETE has waited for binding writers.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db.models.signals import post_delete, post_save
from rebac.field_backing import canonical_model

from angee.base.refs import is_record_target_model
from angee.base.signals import connect_for_models

_MARKDOWN_LABEL = "knowledge.markdownpage"


def connect() -> None:
    """Wire knowledge-owned receivers after app population."""

    post_save.connect(rebuild_backlinks, dispatch_uid="angee.knowledge.backlinks")
    # Elevated writes can bind undeclared REBAC types, so schema target lists
    # cannot narrow this receiver's coverage.
    connect_for_models(post_delete, teardown_record_bindings, applies=is_record_target_model,
                       dispatch_uid="angee.knowledge.record_binding.teardown")


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
    """Delete bindings once, after the canonical target row has disappeared."""

    if sender._meta.apps is not apps or sender._meta.concrete_model is not canonical_model(sender):
        return
    apps.get_model("knowledge", "RecordBinding").objects.teardown_for_record(instance)
