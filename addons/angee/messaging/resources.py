"""Import-export adapters that write chatter through the threaded record's owner verbs.

A resource row names its threaded record and its author by xref. The adapter
posts through the record's own verb as that author, so the owner's permission,
thread, parts, fan-out and follower rules apply exactly as they do for a person.
Chatter is append-only: a changed row needs a new ``_xref``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from import_export import fields, widgets
from rebac import RebacMixin, actor_context

from angee.resources.loader import AngeeResource
from angee.resources.widgets import resolve_xref


class _ChatterResource(AngeeResource):
    """Resolve the row's record and author; persist through the subclass's verb."""

    record = fields.Field(column_name="record", readonly=True)
    author = fields.Field(column_name="author", readonly=True)

    def validate_instance(
        self, instance: Any, import_validation_errors: Any = None, validate_unique: bool = True,
    ) -> None:
        """Report column errors only: the record's verb validates the chatter it writes."""

        del instance, validate_unique
        if import_validation_errors:
            raise ValidationError(import_validation_errors)

    def save_instance(self, instance: Any, is_create: bool, row: Mapping[str, Any], **kwargs: Any) -> None:
        """Write a new row through its verb; refuse to rewrite chatter already posted."""

        if not is_create:
            raise ValidationError(
                f"{self.entry.display}: chatter row {row['_xref']!r} changed after it was posted; "
                "chatter is append-only, so give the new content a new _xref."
            )
        self.before_save_instance(instance, row, **kwargs)
        record = self._xref(row, "record")
        author = self._xref(row, "author")
        if not isinstance(author, get_user_model()):
            raise ValidationError({"author": "An author xref must name a user."})
        with actor_context(author):
            if isinstance(record, RebacMixin):
                record = record.with_actor(author)
            written = self.write(record, author, instance, row)
        for field in instance._meta.concrete_fields:
            setattr(instance, field.attname, getattr(written, field.attname))
        instance._state.adding = False
        instance._state.db = written._state.db
        self.after_save_instance(instance, row, **kwargs)

    def write(self, record: Any, author: Any, instance: Any, row: Mapping[str, Any]) -> models.Model:
        """Persist one chatter row on ``record`` as ``author``."""

        raise NotImplementedError

    def _xref(self, row: Mapping[str, Any], column: str) -> models.Model:
        value = row.get(column)
        if not isinstance(value, str) or not value:
            raise ValidationError({column: "This column needs an xref."})
        try:
            return resolve_xref(value, self.ledger_model, self.addon_aliases)
        except ValueError as error:
            raise ValidationError({column: str(error)}) from error


class MessageResource(_ChatterResource):
    """Post a comment or log a note on a threaded record's chatter.

    Columns: ``record`` and ``author`` (xrefs), ``body``, ``kind`` (``comment``,
    the default, posts like a person; ``note`` logs a note), and the native
    ``parent`` and ``idempotency_key``. ``edited: true`` records one edit by
    the author through the edit verb, keeping the same body.
    """

    body = fields.Field(column_name="body", readonly=True)
    kind = fields.Field(column_name="kind", readonly=True, default="comment")
    idempotency_key = fields.Field(column_name="idempotency_key", readonly=True)
    edited = fields.Field(column_name="edited", readonly=True, default=False, widget=widgets.BooleanWidget())

    def write(self, record: Any, author: Any, instance: Any, row: Mapping[str, Any]) -> models.Model:
        body = str(row.get("body") or "").strip()
        if not body:
            raise ValidationError({"body": "A chatter row needs a body."})
        kind = row.get("kind") or "comment"
        if kind not in {"comment", "note"}:
            raise ValidationError({"kind": "Choose comment or note."})
        verb = record.message_post if kind == "comment" else record.message_log
        message = verb(body, parent=instance.parent, idempotency_key=str(row.get("idempotency_key") or "") or None)
        edited = row.get("edited") not in (None, "") and self.fields["edited"].widget.clean(row["edited"])
        if edited and not message.edit_history:
            type(message).objects.update_content(message, body=body, edited_by_id=author.pk)
            message.refresh_from_db()
        return message
