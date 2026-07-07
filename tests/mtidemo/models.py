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

from angee.base.models import AngeeDataModel


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
        rebac_id_attr = "sqid"


class MtiChild(MtiParent):
    """A REBAC-gated multi-table-inheritance child sharing ``MtiParent``'s pk."""

    detail = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        """Django model options for the gated MTI child."""

        app_label = "mtidemo"
        db_table = "test_mtidemo_child"
        rebac_resource_type = "mtidemo/child"
        rebac_id_attr = "sqid"
