"""What a kept response becomes.

A form selects one target by key (``Form.target_class``). An addon that owns a
record contributes its target with one settings line,
``"ANGEE_WEBFORM_TARGET_CLASSES.<key>": "<dotted path>"``, and maps answers to
its own fields in the target's ``config_model``. The web forms addon never names
another addon's model: a target calls its owner's own verb.
"""

from __future__ import annotations

from typing import Any, ClassVar

from angee.base.implementations import ImplBase


class FormTarget(ImplBase):
    """One way a response is turned into a record, selected per form.

    ``deliver`` runs after the response is kept, on a worker, as named system
    work and inside the transaction that records its outcome. It returns the
    record it made or found, or ``None`` when the response itself is the record.
    It must be safe to run again for the same response: a delivery that failed
    is retried, and a delivered response is never delivered twice (the response
    keeps its one link to the record made).
    """

    registry_setting: ClassVar[str] = "ANGEE_WEBFORM_TARGET_CLASSES"

    def __init__(self, form: Any) -> None:
        self.form = form
        self.config = self.parse_config(form.target_config)

    def deliver(self, response: Any) -> Any:
        """Make or find the record ``response`` becomes; ``None`` when there is none to make."""

        raise NotImplementedError


class StoreTarget(FormTarget):
    """The response is the record: a survey whose answers are read where they are kept."""

    key = "store"
    label = "Keep the response"

    def deliver(self, response: Any) -> Any:
        """Make nothing: the response stays the record."""

        return None
