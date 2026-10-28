"""Historical index imports retained for serialized migrations.

Runtime callers use angee.base.indexes. Retire after dependent histories are collapsed at the documented cutoff.
"""
from angee.base.indexes import PatternOpsIndex

__all__ = ["PatternOpsIndex"]
