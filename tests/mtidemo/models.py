"""A REBAC-gated multi-table-inheritance pair for the grant-materialization tests.

``MtiChild`` IS-A ``MtiParent`` (Django MTI, shared primary key), and both carry
their own ``rebac_resource_type`` with a ``reader`` relation and a ``read``
permission in the adjacent ``permissions.zed`` — the ``parties.Organization`` IS-A
``parties.Party`` shape in miniature. It exists so the grant-materialization tests
can prove a grant on the child row lands on *every* identity the row carries: the
child type and each REBAC-registered parent type it IS-A, so a foreign key typed to
the parent still scopes the granted subject in. ``chatterdemo``'s MTI pair is
deliberately ungated (no ``rebac_resource_type``) and cannot exercise this gate;
this concrete pair does, in a real installed app so pytest-django builds its tables
and ``rebac sync`` loads its definitions.
"""

from __future__ import annotations

from django.db import models
from rebac import to_object_ref

from angee.base.models import AngeeDataModel
from angee.workflows.triggers import RecordChangedOptIn, TriggerGrantTarget


class MtiParent(AngeeDataModel):
    """A REBAC-gated parent — the shared identity an MTI child IS-A."""

    sqid_prefix = "mtp_"

    title = models.CharField(max_length=200, blank=True, default="")

    class Meta(AngeeDataModel.Meta):
        """Django model options for the gated MTI parent."""

        abstract = False
        app_label = "mtidemo"
        db_table = "test_mtidemo_parent"
        rebac_resource_type = "mtidemo/parent"


class MtiChild(RecordChangedOptIn, MtiParent):
    """A REBAC-gated multi-table-inheritance child sharing ``MtiParent``'s pk."""

    @classmethod
    def record_changed_grant_targets(cls, trigger):
        """Grant the fixture's existing concrete subjects for this test source."""
        return tuple(
            TriggerGrantTarget(to_object_ref(record), "reader", "write")
            for record in cls._base_manager.order_by("pk")
        )

    detail = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        """Django model options for the gated MTI child."""

        app_label = "mtidemo"
        db_table = "test_mtidemo_child"
        rebac_resource_type = "mtidemo/child"


class MtiChildProxy(MtiChild):
    """An untyped proxy over the typed concrete ``MtiChild``.

    Carries no ``rebac_resource_type`` of its own (a bare proxy Meta), so it pins the
    proxy rule in :func:`rebac.generic_target`: a proxy resolves to its concrete
    model first, then the MTI walk runs — an untyped proxy over a typed concrete
    row keys on the typed ancestor (``MtiParent``), never the proxy's own content type.
    """

    class Meta:
        """Django model options for the untyped MTI-child proxy."""

        proxy = True
        app_label = "mtidemo"


class MtiParentProxy(MtiParent):
    """An untyped proxy over the typed flat topmost model ``MtiParent``.

    The flat (non-MTI) counterpart of :class:`MtiChildProxy`: canonicalizes to
    ``MtiParent``'s own content type, not the proxy's, pinning that the proxy content type
    is never the edge key even without an MTI ancestor to climb to.
    """

    class Meta:
        """Django model options for the untyped MTI-parent proxy."""

        proxy = True
        app_label = "mtidemo"
