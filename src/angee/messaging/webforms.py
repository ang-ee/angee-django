"""What messaging keeps of the form a channel held, for the migrations that end it.

A channel held a public form in six columns of its own until web forms took
forms over. Every name here is imported by migrations that projects retain:
``default_webform_schema`` by their materialized messaging history, and the rest
by their copy of the web forms bridge's template, which carries each channel's
form to a ``webforms.Form`` and then removes the columns. Keep these names and
what they mean for as long as such a migration can be in a project's history.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from django.apps import apps as installed_apps
from django.apps.registry import Apps
from django.db import models
from django.db.migrations import Migration
from django.db.models.functions import Cast

from angee.base.fields.identity import SqidField

FORM_COLUMNS = ("slug", "is_published", "form_schema_version", "form_schema", "max_body_bytes", "max_field_bytes")
"""The columns a channel's form lived in. Retained migrations import this."""


def default_webform_schema() -> dict[str, Any]:
    """Return the default of the removed ``Channel.form_schema`` column, which those migrations name."""

    return {"type": "object", "properties": {}}


def holds_form(channel: Mapping[str, Any]) -> bool:
    """Whether a channel's form columns say anything: a slug, a published flag or a schema of its own.

    ``channel`` maps the form columns to their values. This is the one judgment
    of "this channel holds a form": the bridge's template moves the channels it
    admits, and messaging refuses to let the columns go while it admits any.
    Retained migrations import it.
    """

    return bool(channel["slug"] or channel["is_published"] or channel["form_schema"] != default_webform_schema())


def channels_holding_forms(apps: Apps, using: str) -> list[dict[str, Any]]:
    """Return the form columns of every webform channel that holds a form, oldest first.

    ``apps`` is a migration state in which ``messaging.Channel`` still has its
    form columns; each row carries ``pk``, ``display_name`` and the columns.
    The backend is read as it is stored: a project that has not installed the
    web forms bridge registers no ``webform`` backend, and the field refuses a
    key it does not know. Retained migrations import it.
    """

    channels = apps.get_model("messaging", "Channel")._base_manager.using(using)
    webform = (
        channels.annotate(stored_backend=Cast("backend_class", models.CharField()))
        .filter(stored_backend="webform")
        .order_by("pk")
        .values("pk", "display_name", *FORM_COLUMNS)
    )
    return [channel for channel in webform if holds_form(channel)]


def channel_public_id(pk: Any) -> str:
    """Return a channel's public id from its key.

    ``sqid`` is no column, so a migration state has no field for it; the
    installed channel's own field says it. Retained migrations import it.
    """

    field = cast(SqidField, installed_apps.get_model("messaging", "Channel")._meta.get_field("sqid"))
    return field.public_id_from_value(pk)


def is_move(migration: Migration) -> bool:
    """Whether ``migration`` carries the channels' forms away before it removes their columns.

    Such a migration says so itself, with ``moves_webform_channels = True`` on
    its ``Migration``: the bridge's template declares it, so every project's
    copy does, under whatever name the project gave it.
    """

    return bool(getattr(migration, "moves_webform_channels", False))
