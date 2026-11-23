"""What a kept response becomes.

A form selects one target by key (``Form.target_class``). An addon that owns a
record contributes its target with one settings line,
``"ANGEE_WEBFORM_TARGET_CLASSES.<key>": "<dotted path>"``, and maps answers to
its own fields in the target's ``config_model``. The web forms addon never names
another addon's model: a target calls its owner's own verb, and says who may
choose it (:meth:`FormTarget.permits`). The addon also declares the type its
target makes, as a relation on ``webforms/response_record`` in its
``permissions.extends.zed`` (``relation enquiry: acme/enquiry //
rebac:field=record``): a response becomes only a record of a declared type.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from angee.base.implementations import ImplBase

REFUSAL_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")


class DeliveryRefused(Exception):
    """The target's owner will not take this response as it is, however often it is offered.

    ``code`` is a short name of the reason, chosen by the target
    (``"unknown_category"``). It is all the response keeps of the refusal and
    what staff read, so it never carries an answer. A refused response is not
    tried again by itself; staff correct what the owner refused and deliver it,
    or dismiss it.
    """

    def __init__(self, code: str) -> None:
        if not REFUSAL_CODE.fullmatch(code):
            raise ValueError("A refusal code is a short lower-case name, such as unknown_category.")
        super().__init__(code)
        self.code = code


class FormTarget(ImplBase):
    """One way a response is turned into a record, selected per form.

    ``deliver`` runs after the response is kept, on a worker, as named system
    work and inside the transaction that records its outcome. It returns the
    record it made or found, or ``None`` when the response itself is the record.
    It must be safe to run again for the same response: a delivery that failed
    is retried, and a delivered response is never delivered twice (the response
    keeps its one link to the record made). Raise :class:`DeliveryRefused` when
    the owner refuses the response itself; any other exception is a failure
    that is tried again.
    """

    registry_setting: ClassVar[str] = "ANGEE_WEBFORM_TARGET_CLASSES"

    def __init__(self, form: Any) -> None:
        self.form = form
        self.config = self.parse_config(form.target_config)

    def permits(self, actor: Any) -> bool:
        """Whether ``actor`` may point a form at this target, as it is configured.

        A target makes its owner's records as system work, for anyone who
        submits the form. So choosing it is the owner's decision, not the form
        manager's: answer with the owner's own access check for the record this
        configuration would make (``Enquiry(queue=…).with_actor(actor)
        .has_access("create")``). Every target states it. It is asked when a
        form's target or its configuration changes under an acting user, never
        for system work and never at delivery.
        """

        raise NotImplementedError

    def clean(self) -> None:
        """Refuse, with ``ValidationError``, a configuration the owner will never take.

        Asked by ``Form.clean`` when a form's target or its configuration
        changes and the form is validated, for an acting user and for system
        work alike, such as a form a resource row declares; ``permits`` then
        decides whether the acting user may choose it. It may run with no
        acting user. What it reads for an acting user, it reads through their
        access (``instance_actor(self.form)``), so it discloses nothing they
        cannot read. The default takes every configuration its ``config_model``
        admits.
        """

    def deliver(self, response: Any) -> Any:
        """Make or find the record ``response`` becomes; ``None`` when there is none to make."""

        raise NotImplementedError


class StoreTarget(FormTarget):
    """The response is the record: a survey whose answers are read where they are kept."""

    key = "store"
    label = "Keep the response"

    def permits(self, actor: Any) -> bool:
        """Anyone who manages the form: nothing is made outside it."""

        return True

    def deliver(self, response: Any) -> Any:
        """Make nothing: the response stays the record."""

        return None
