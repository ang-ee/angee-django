"""Source model for the operator addon.

The operator daemon owns all real state; this addon adds no table. The single
``OperatorConnection`` source exists only so the synthetic ``operator/connection``
resource — gated by a const-backed ``admin`` relation in ``permissions.zed`` —
resolves to a model for the REBAC system check (``rebac.E009``). ``managed =
False``: Django owns no table and emits no table operations, there are no rows,
and the connection is a singleton (``operator/connection:default``) whose forward
access check synthesises the admin subject from the schema — so this model is
never read or written. It is a type anchor, nothing more.
"""

from __future__ import annotations

from angee.base.models import AngeeModel


class OperatorConnection(AngeeModel):
    """Table-less REBAC type anchor for the operator daemon connection."""

    class Meta:
        abstract = True
        managed = False
        rebac_resource_type = "operator/connection"
