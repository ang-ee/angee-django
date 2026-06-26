"""Render a model-generic ``<system_context>`` prompt block from a view envelope.

The chat client prefixes each message with the context of what the user is looking
at — the open record, a list selection, or a dashboard. The browser sends a view
envelope; this builds a compact text block the agent can read: the view metadata,
short previews of the selected rows, and a pointer to the MCP tools for full bodies.
Model-generic: it resolves the model from the envelope's ``app/model`` type and
summarises each row from a few public fields, so any addon's model works without a
per-model branch here.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import FieldDoesNotExist
from django.db import models
from rebac.resources import model_for_resource_type

from angee.base.fields import EncryptedField
from angee.base.models import instance_from_public_id

_PREVIEW_CAP = 20
"""Most rows previewed for a list/dashboard view — full bodies come from the MCP tools."""

_PREVIEW_FIELD_CAP = 6
"""Most fields shown per row preview, to keep the block compact."""

_HOUSEKEEPING_FIELDS = frozenset({"id", "created_at", "updated_at"})
"""Primary key + audit timestamps — bookkeeping, not previewable content."""


def render_view_context(view: dict[str, Any]) -> str:
    """Return a ``<system_context>`` block describing the open ``view``.

    ``view`` is ``{kind, type: "<app>/<model>", sqid?, sqids?, params?}``. Returns an
    empty string for an empty or untyped envelope (nothing to add). Rows are read
    scoped to the request's ambient actor — only rows the caller may read are
    previewed, so the block never leaks a row the user can't see — capped, and
    previewed from a few public fields. Call within a resolved actor context.
    """

    type_label = str(view.get("type") or "").strip()
    if not type_label:
        return ""
    model = _resolve_model(type_label)
    if model is None:
        return _block(view, type_label, "Unknown model — no preview available.", ())
    rows = _select_rows(model, view)
    previews = tuple(_preview(row) for row in rows)
    note = "Call the matching MCP tool (e.g. read_note) for a row's full body."
    return _block(view, type_label, note, previews)


def _resolve_model(type_label: str) -> type[models.Model] | None:
    """Return the model for a rebac resource type (e.g. ``"agents/mcp_server"``).

    rebac owns the resource-type↔model mapping, so the envelope's ``type`` (the rebac
    type, which may differ from the Django ``app_label.model_name``) resolves through
    :func:`rebac.resources.model_for_resource_type`, not ``apps.get_model``.
    """

    model = model_for_resource_type(type_label)
    return model if isinstance(model, type) and issubclass(model, models.Model) else None


def _select_rows(model: type[models.Model], view: dict[str, Any]) -> list[models.Model]:
    """Return the rows the view selects: one for a record, the listed set otherwise.

    A record/list selection resolves each public id through ``instance_from_public_id``
    (the owner-correct resolver, working for any Angee or plain queryset); a kindless
    or unselected view previews the first rows.
    """

    sqid = view.get("sqid")
    sqids = view.get("sqids") or ([] if sqid is None else [sqid])
    if sqids:
        rows = [instance_from_public_id(model, str(value)) for value in sqids[:_PREVIEW_CAP]]
        return [row for row in rows if row is not None]
    return list(model._default_manager.all()[:_PREVIEW_CAP])


def _preview(row: models.Model) -> str:
    """Return a one-line preview of ``row``: its public id, label, and a few fields."""

    identifier = getattr(row, "public_id", None) or row.pk
    parts = [f"{identifier}: {row}"]
    for name in _preview_field_names(type(row)):
        value = _field_value(row, name)
        if value:
            parts.append(f"{name}={value}")
    return " | ".join(parts)


def _preview_field_names(model: type[models.Model]) -> tuple[str, ...]:
    """Return the concrete, non-relational field names to preview, capped.

    Never previews an :class:`~angee.base.fields.EncryptedField`: its Python value
    decrypts to a secret (``client_secret``, OAuth ``material``) and this block is sent
    to a third-party LLM, so a secret-by-type column must never reach it — the same
    "never on a boundary" contract the field declares for GraphQL. Relations and the
    housekeeping columns are not content either.
    """

    names = [
        field.name
        for field in model._meta.concrete_fields
        if not field.is_relation and not isinstance(field, EncryptedField) and field.name not in _HOUSEKEEPING_FIELDS
    ]
    return tuple(names[:_PREVIEW_FIELD_CAP])


def _field_value(row: models.Model, name: str) -> str:
    """Return a short string for ``row``'s ``name`` field, truncated for body text."""

    try:
        row._meta.get_field(name)
    except FieldDoesNotExist:
        return ""
    value = getattr(row, name, "")
    text = str(value).strip().replace("\n", " ")
    return f"{text[:80]}…" if len(text) > 80 else text


def _block(view: dict[str, Any], type_label: str, note: str, previews: tuple[str, ...]) -> str:
    """Assemble the ``<system_context>`` text from the view metadata and row previews."""

    kind = str(view.get("kind") or "view")
    lines = [
        "<system_context>",
        f"The user is viewing a {kind} of {type_label}.",
    ]
    params = view.get("params")
    if params:
        lines.append(f"View params: {params}")
    if previews:
        lines.append(f"Selected rows ({len(previews)}):")
        lines.extend(f"- {preview}" for preview in previews)
    else:
        lines.append("No rows selected.")
    lines.append(note)
    lines.append("</system_context>")
    return "\n".join(lines)
