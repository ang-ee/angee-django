"""Source models for the operator addon.

The operator daemon owns all real state; this addon adds no table. Its two
sources are table-less REBAC type anchors that exist only so synthetic resources
gated by a const-backed ``admin`` relation in ``permissions.zed`` resolve to a
model for the REBAC system check (``rebac.E009``): ``OperatorConnection`` anchors
``operator/connection`` (the singleton daemon connection) and ``OperatorRole``
anchors the ``operator/role`` namespace (so a platform admin resolves as an
effective operator-admin through its const). ``managed = False``: Django owns no
table and emits no table operations, there are no rows, and the forward access
check synthesises the admin subject from the schema — so these models are never
read or written. They are type anchors, nothing more.
"""

from __future__ import annotations

from angee.base.models import AngeeModel, role_anchor


class OperatorConnection(AngeeModel):
    """Table-less REBAC type anchor for the operator daemon connection."""

    runtime = True

    class Meta:
        abstract = True
        managed = False
        rebac_resource_type = "operator/connection"


OperatorRole = role_anchor("operator/role")
"""Table-less REBAC type anchor for the ``operator/role`` namespace.

The const-backed ``admin`` relation on ``operator/role`` (``permissions.zed``)
registers through this anchor's ``rebac_resource_type`` (``rebac.E009``), so a
platform admin resolves as an effective operator-admin through the const. See
:func:`angee.base.models.role_anchor`.
"""
