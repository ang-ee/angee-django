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


class MtiChildProxy(MtiChild):
    """An untyped proxy over the typed concrete ``MtiChild``.

    Carries no ``rebac_resource_type`` of its own (a bare proxy Meta), so it pins the
    proxy rule in :func:`angee.base.refs.canonical_record_target`: a proxy resolves to its
    concrete model first, then the MTI walk runs — an untyped proxy over a typed concrete
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


class MtiSideParent(models.Model):
    """A second concrete parent with its own primary key — a secondary MTI path.

    Deliberately shares no abstract base with :class:`MtiParent`, so a child inheriting
    both has two independent concrete tables (and two primary keys), not a diamond.
    ``managed = False``: never a table, only the shape the two-parent guard rejects.
    """

    label = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        """Django model options for the secondary concrete parent."""

        managed = False
        app_label = "mtidemo"
        db_table = "test_mtidemo_side_parent"


class MtiTwoParent(MtiParent, MtiSideParent):
    """A multiple-MTI child with two concrete parents — the corrupting shape refs rejects.

    ``MtiSideParent``'s row keeps its own primary key, so this child's pk does not address
    it; :func:`angee.base.refs.canonical_record_target` fails fast rather than store the
    child pk against the side parent's content type. ``managed = False`` — instantiated
    only to exercise the guard, never saved.
    """

    class Meta:
        """Django model options for the multiple-MTI child."""

        managed = False
        app_label = "mtidemo"
        db_table = "test_mtidemo_two_parent"
