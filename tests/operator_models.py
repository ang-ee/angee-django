"""Concrete table-less operator REBAC anchors for the bare-Django test runtime.

The composer emits these anchors in the runtime. The bare test runtime registers
them from conftest so the const relations on ``operator/connection`` and
``operator/role`` resolve, for the REBAC checks and for the index, exactly as
they do composed. ``managed = False``: never tables, only type anchors.
"""

from angee.operator.models import OperatorConnection as AbstractOperatorConnection
from angee.operator.models import OperatorRole as AbstractOperatorRole


class OperatorConnection(AbstractOperatorConnection):
    """Concrete table-less REBAC anchor for ``operator/connection``."""

    class Meta(AbstractOperatorConnection.Meta):
        abstract = False
        managed = False
        app_label = "operator"
        rebac_resource_type = "operator/connection"


class OperatorRole(AbstractOperatorRole):
    """Concrete table-less REBAC anchor for the ``operator/role`` namespace."""

    class Meta(AbstractOperatorRole.Meta):
        abstract = False
        managed = False
        app_label = "operator"
        rebac_resource_type = "operator/role"
