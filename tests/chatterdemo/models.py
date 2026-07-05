"""A REBAC-gated threaded record for the F-v messaging tests.

``ChatterDoc`` stands in for an arp document that composes
:class:`~angee.messaging.models.ThreadedModelMixin`: a real REBAC resource whose
``read``/``write``/``post`` permissions diverge, so the messaging tests can drive
the surface-isolation scenarios that ``ThreadedTicket`` (ungated — ``can_post``
always allows) cannot:

- **part 1** — an actor granted ``writer`` may change a tracked field (``write``)
  but may not post comments (``post``), so an automatic tracked-field log must land
  without consulting ``can_post``.
- **part 3** — a user with no grant cannot read the record, so an activity attached
  to it is unreachable through complete/cancel (authorization rides the record
  read, not the activity's own permission).

``thread_post_access`` is overridden to ``"post"`` so post access diverges from the
``"write"`` that authorizes saving a tracked field; the mixin's other access verbs
keep their defaults (``thread_read_access="read"``, ``thread_activity_access="write"``).
"""

from __future__ import annotations

from django.db import models

from angee.base.mixins import AuditMixin, SqidMixin
from angee.base.models import AngeeModel
from angee.messaging.models import ThreadedModelMixin


class ChatterDoc(SqidMixin, AuditMixin, ThreadedModelMixin, AngeeModel):
    """A gated document that opts into record chatter with post-access divergence."""

    sqid_prefix = "cdc_"
    thread_tracking_fields = ("status",)
    thread_post_access = "post"

    title = models.CharField(max_length=160)
    status = models.CharField(
        max_length=32,
        choices=(("open", "Open"), ("closed", "Closed")),
        default="open",
    )

    class Meta:
        """Django model options for the gated threaded test record."""

        abstract = False
        app_label = "chatterdemo"
        db_table = "test_chatterdemo_doc"
        rebac_resource_type = "chatterdemo/doc"
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return the document title for thread subjects."""

        return self.title
