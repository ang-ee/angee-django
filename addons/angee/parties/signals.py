"""Transactional account identity receivers.

After every migrate, storage's image drive is provisioned with its grant, and party
avatars stored before it move onto it (``PartyManager.rehome_avatars``), a no-op once
none remain. Each migrate also ranks the statement groups a declared fold family's
statements share with another writer, once per new fold
(``PartyManager.resolve_folded_statements``).
"""

from __future__ import annotations

import logging
from typing import Any

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import post_migrate, post_save
from rebac import system_context

from angee.base.signals import has_table, migrated_model
from angee.iam.events import person_created

logger = logging.getLogger(__name__)

_USER_NAME_FIELDS = frozenset({"first_name", "last_name", "username"})
"""User fields the person's display name derives from."""


def connect() -> None:
    """Wire person creation, account renames and the avatar rehome."""

    person_created.connect(_link_person, dispatch_uid="parties.person_created")
    post_save.connect(_follow_user_name, sender=get_user_model(), dispatch_uid="parties.user_name")
    post_migrate.connect(_rehome_avatars, dispatch_uid="parties.rehome_avatars")
    post_migrate.connect(_resolve_folded_statements, dispatch_uid="parties.resolve_folded_statements")


def _resolve_folded_statements(*, app_config: object, using: str, **kwargs: object) -> None:
    """Rank the statement groups the declared fold families share with another writer, on every migrate.

    It runs here, after every migration, because ranking needs the live statement
    models while a migration declaration's body is self-contained history over
    historical models. ``PartyManager.resolve_folded_statements`` owns the retry: it
    records a high-water mark per successful ranking, so a migrate with no newer fold
    is a few queries, and a failure fails the migrate and records no mark, so the next
    migrate ranks again.
    """

    party_model = migrated_model(app_config=app_config, using=using, app_label="parties", model_name="Party")
    if party_model is None or not has_table(using, apps.get_model(settings.ANGEE_CHANGESET_MODEL)):
        return
    party_model._default_manager.db_manager(using).resolve_folded_statements()


def _rehome_avatars(*, app_config: object, using: str, **kwargs: object) -> None:
    """Provision storage's image drive and move avatars stored before it onto it, once per migrate.

    Best-effort: it needs storage's tables as well as the party table, and no
    failure, a storage outage included, ever fails the migrate; the next one retries.
    """

    party_model = migrated_model(app_config=app_config, using=using, app_label="parties", model_name="Party")
    if party_model is None:
        return
    if not all(has_table(using, apps.get_model("storage", name)) for name in ("File", "Drive")):
        return
    try:
        party_model._default_manager.rehome_avatars()
    except Exception:
        logger.exception("Moving party avatars onto the image drive failed; the next migrate retries.")


def _link_person(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Create the account's person in IAM's transaction, including on replay."""

    del sender, kwargs
    with system_context(reason="parties.person_created"):
        apps.get_model("parties", "Party").objects.for_user(instance)


def _follow_user_name(
    sender: Any,
    instance: Any,
    created: bool,
    update_fields: Any = None,
    raw: bool = False,
    **kwargs: Any,
) -> None:
    """Rename the account's person when a saved user's name may have changed."""

    del sender, kwargs
    if created or raw or (update_fields is not None and not _USER_NAME_FIELDS & set(update_fields)):
        return
    with system_context(reason="parties.user_name"):
        apps.get_model("parties", "Party").objects.follow_user_name(instance)
