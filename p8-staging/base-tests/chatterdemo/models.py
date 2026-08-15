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

from angee.base.fields import StateField
from angee.base.mixins import AuditMixin, SqidMixin
from angee.base.models import AngeeModel
from angee.base.transitions import StateTransitions, save_state, transition
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
        """Return the document title for the thread's title fragment."""

        return self.title


class TrackedRecordParent(SqidMixin, AuditMixin, ThreadedModelMixin, AngeeModel):
    """Concrete threaded parent standing in for a materialized ``extends`` target.

    Opts into field tracking on ``status`` and guards that column with a
    ``StateTransitions`` graph whose transition persists through ``save_state`` — so
    a transition drives the same chatter save the tracking rides. Ungated (no
    ``rebac_resource_type``), like ``ThreadedTicket``, to keep the tracking scenario
    free of permission setup.
    """

    class Status(models.TextChoices):
        """Lifecycle for the tracked record."""

        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    sqid_prefix = "trp_"
    thread_tracking_fields = ("status",)

    title = models.CharField(max_length=160)
    status = StateField(choices_enum=Status, default=Status.OPEN)
    status_transitions = StateTransitions(status, {Status.OPEN: [Status.CLOSED]})

    class Meta:
        """Django model options for the tracked threaded parent."""

        abstract = False
        app_label = "chatterdemo"
        db_table = "test_chatterdemo_tracked_parent"

    @transition(status, source=Status.OPEN, target=Status.CLOSED, on_success=save_state)
    def close(self) -> None:
        """Move ``status`` open -> closed and persist it (a ``save_state`` edge)."""

    def __str__(self) -> str:
        """Return the record title for the thread's title fragment."""

        return self.title


class _AbstractTrackedRecordChild(AngeeModel):
    """The abstract source a materialized child contributes over the parent."""

    note = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        """Django model options for the child's abstract source."""

        abstract = True
        app_label = "chatterdemo"


class TrackedRecordChild(_AbstractTrackedRecordChild, TrackedRecordParent):
    """A materialized child with the ``child_overrides_parent`` (child-first) MRO.

    Emitted exactly as the composer would for ``extends`` +
    ``child_overrides_parent``: the abstract child source before the concrete parent
    (so the child's own methods win the MRO), MTI on the parent, and the
    parent-shared framework columns re-declared ``None`` so the child inherits the
    parent's columns instead of duplicating them. Pins that a transition ``save``
    still yields exactly one tracking note through this flipped MRO — the
    ``ThreadedModelMixin.save`` tracking runs once, not once per MRO level.
    """

    created_at = None
    updated_at = None

    class Meta:
        """Django model options for the materialized tracked child."""

        abstract = False
        app_label = "chatterdemo"
        db_table = "test_chatterdemo_tracked_child"
