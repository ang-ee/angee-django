"""Storage-owned signals and receivers.

REBAC needs no signal work here: every storage relation is field-backed or
const-backed in ``permissions.zed``, so the engine derives them from the rows
at check time and stores no tuples. What remains is the addon's own
extension point (``file_finalized``) and the per-user Trash smart folder.
"""

from __future__ import annotations

import logging
from typing import Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import OperationalError, ProgrammingError
from django.db.models import Model
from django.db.models.signals import post_save
from django.dispatch import Signal
from rebac import system_context

logger = logging.getLogger(__name__)

file_finalized = Signal()
"""Sent (on commit) when a ``File`` flips to READY.

Receives ``sender`` (the concrete file model), ``instance``, and ``actor``.
Rendition, virus-scan, extraction, and indexing addons subscribe here.
"""


def connect() -> None:
    """Wire the per-user Trash smart folder to user creation."""

    post_save.connect(
        create_trash_folder,
        sender=get_user_model(),
        dispatch_uid="angee-storage-trash-folder",
    )


def create_trash_folder(
    sender: type[Model],
    instance: Model,
    created: bool,
    raw: bool = False,
    **kwargs: Any,
) -> None:
    """Ensure each user owns exactly one Trash smart folder."""

    del sender, kwargs
    if raw or not created:
        return
    try:
        folder_model = apps.get_model("storage", "Folder")
    except LookupError:
        # No composed concrete model (e.g. bare test settings) — nothing to own a Trash row.
        return
    try:
        with system_context(reason="storage.trash_folder"):
            folder_model._base_manager.get_or_create(
                owner=instance,
                smart_kind=folder_model.SmartKind.TRASH,
                is_virtual=True,
                defaults={"name": "Trash"},
            )
    except OperationalError, ProgrammingError:
        # A user created inside the migration window, before storage's own
        # migrations ran. Trash is a UI anchor, safe to create later.
        logger.warning("storage: skipped Trash folder for user %s (tables not ready)", instance.pk)
