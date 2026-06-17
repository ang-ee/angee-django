"""Source models for the platform addon.

The platform console owns no data; it reflects the runtime the composer already
built. Its only source is a table-less REBAC type anchor that exists so the
const-backed ``read`` permission in ``permissions.zed`` resolves to a model for
the REBAC system check (``rebac.E009``): ``PlatformExplorer`` anchors the
``platform/explorer`` namespace so a platform admin resolves as an effective
reader through its const. ``managed = False``: Django owns no table and emits no
table operations, there are no rows, and the forward access check synthesises the
admin subject from the schema — so this model is never read or written. It is a
type anchor, nothing more.
"""

from __future__ import annotations

from angee.base.models import AngeeModel


class PlatformExplorer(AngeeModel):
    """Table-less REBAC type anchor for the platform introspection surface."""

    runtime = True

    class Meta:
        abstract = True
        managed = False
        rebac_resource_type = "platform/explorer"
