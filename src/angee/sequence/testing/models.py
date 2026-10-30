"""Canonical concrete sequence models for the bare-Django test runtime.

Work queues allocate their numbers from these sequences, and the REBAC checks
resolve the sequence role anchor, so register the models from conftest rather
than from the sequence test module.
"""

from angee.sequence.models import Sequence as AbstractSequence
from angee.sequence.models import SequenceCounter as AbstractSequenceCounter
from angee.sequence.models import SequenceRole as AbstractSequenceRole


class Sequence(AbstractSequence):
    """Concrete named counter used by sequence tests."""

    class Meta(AbstractSequence.Meta):
        """Django model options for the canonical test sequence."""

        abstract = False
        app_label = "sequence"
        db_table = "test_sequence_sequence"
        rebac_resource_type = "sequence/sequence"



class SequenceCounter(AbstractSequenceCounter):
    """Concrete per-period counter row used by sequence tests."""

    class Meta(AbstractSequenceCounter.Meta):
        """Django model options for the canonical test sequence counter."""

        abstract = False
        app_label = "sequence"
        db_table = "test_sequence_counter"



class SequenceRole(AbstractSequenceRole):
    """Concrete table-less REBAC anchor for the ``sequence/role`` namespace.

    The composer emits this anchor in the runtime; the bare test runtime
    registers it so the const-backed ``admin`` arm resolves. ``managed = False``:
    never a table, only a type anchor.
    """

    class Meta(AbstractSequenceRole.Meta):
        abstract = False
        managed = False
        app_label = "sequence"
        rebac_resource_type = "sequence/role"
