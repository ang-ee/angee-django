"""Import-export adapter that answers a request's access question through its owner.

A Need row may carry ``access`` (``approve`` or ``deny``) and ``access_by``, the
xref of the person who answered. After the row is saved - which opens its access
question, as any new request does - the adapter answers it through
:meth:`Need.decide_access` as that person, so the decision's permission,
account linking and follow rules apply. It answers only while the question is
open: an answered request keeps its answer on every later load.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from import_export import fields
from rebac import actor_context

from angee.intake.choices import NeedAccessAction
from angee.resources.loader import AngeeResource
from angee.resources.widgets import resolve_xref


class NeedResource(AngeeResource):
    """Save the request natively, then answer an open access question once."""

    access = fields.Field(column_name="access", readonly=True)
    access_by = fields.Field(column_name="access_by", readonly=True)

    def save_instance(self, instance: Any, is_create: bool, row: Mapping[str, Any], **kwargs: Any) -> None:
        super().save_instance(instance, is_create, row, **kwargs)
        answer = row.get("access")
        if answer in (None, ""):
            return
        actions = {"approve": NeedAccessAction.INTAKE_APPROVE, "deny": NeedAccessAction.INTAKE_DENY}
        if answer not in actions:
            raise ValidationError({"access": "Choose approve or deny."})
        decider = self._decider(row)
        instance.refresh_from_db()
        if instance.access_verdict is not None:
            return
        with actor_context(decider):
            instance.with_actor(decider).decide_access(actions[answer])

    def _decider(self, row: Mapping[str, Any]) -> Any:
        value = row.get("access_by")
        if not isinstance(value, str) or not value:
            raise ValidationError({"access_by": "An access answer names who answered it."})
        try:
            decider = resolve_xref(value, self.ledger_model, self.addon_aliases)
        except ValueError as error:
            raise ValidationError({"access_by": str(error)}) from error
        if not isinstance(decider, get_user_model()):
            raise ValidationError({"access_by": "An access answer's xref must name a user."})
        return decider
